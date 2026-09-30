"""
HIP-4 확률 급변 웹훅 이벤트 (MCP Events, 2026-09-30 추가) - EXPERIMENTAL.

## 배경
2026-09-29 OpenAI DevDay에서 ChatGPT 플러그인 자동화가 MCP 서버의 이벤트
구독(webhook push)을 지원한다고 발표했다. 근거 스펙은
github.com/modelcontextprotocol/experimental-ext-triggers-events - 아직
"탐색적 초안(exploratory, not an official spec)" 단계이고, 이 세션이 실제로
확인한 PR(#1 design sketch, #7 capabilities.extensions 초안)에도 TTL 범위
(5분~1일 사이에서 논의 중), CRC 챌린지 검증, capabilities 선언 키 등 여러
디테일이 아직 확정되지 않았다.

그래서 이 모듈은 스펙을 그대로 베끼는 대신, 그 중 이미 안정적인 부분(Standard
Webhooks 서명 포맷 - standardwebhooks.com, 오늘 액션 카드의 스크립트가 쓴 것과
동일)만 가져다 쓰고, 나머지(정확한 JSON-RPC 메서드 계약, capabilities 키 이름)는
"바뀔 수 있다"는 전제로 최소 구현했다. app/mcp_server.py의 events/list,
events/subscribe, events/unsubscribe와 이 모듈을 묶어서 부르는 이름 전체가
EXPERIMENTAL - README/Bazaar 설명에도 명시할 것.

## 왜 새 REST 엔드포인트로 감쌌나 (tools/call과 동일 패턴)
app/mcp_server.py의 tools/call은 자체 결제 로직이 없다 - 내부 self-call이
이미 결제가 걸려 있는 REST 경로(/v1/...)를 타면서 PaymentMiddlewareASGI가
자동으로 결제를 강제한다. events/subscribe도 똑같은 트릭을 쓴다: 실제 구독
생성 로직은 GET /v1/prediction/hip4-alerts/subscribe-v2 REST 엔드포인트에 있고
(main.py, app/payment.py의 build_routes()에 유료로 등록), MCP의
events/subscribe는 그 REST 경로를 internal self-call로 호출할 뿐이다. 그래서
MCP로 구독하든 REST로 직접 구독하든 가격/결제 검증이 완전히 동일하게 적용된다.

## 영속성에 대한 정직한 고백
구독 정보를 이 모듈 프로세스 메모리 + 로컬 JSON 파일(DATA_FILE)에 저장한다.
Render 무료 플랜은 디스크가 재배포/재시작 시 초기화될 수 있어서, 서버가
재시작되면 기존 구독이 전부 사라질 수 있다 - 이건 알려진 한계이고, 실제 DB
(Redis/Postgres 등)로 옮기기 전까지는 "베타/실험적 기능"으로만 안내해야 한다.
구독은 만료(TTL)되면 자동으로 무효화되고, 갱신 엔드포인트는 아직 없다(같은
webhook_url로 다시 구독하면 새 subscription_id가 발급된다 - v1의 의도적
단순화).

## 폴링 방식 (진짜 "이벤트"가 아니라 "짧은 주기 폴링 + 변화 감지")
Hyperliquid HIP-4에는 웹훅/스트리밍 API가 없다(무료 info API만 있음 -
app/logic.py의 get_hip4_snapshot() 모듈 docstring 참고). 그래서 "이벤트"의
실체는 settings.HIP4_EVENTS_POLL_INTERVAL_SECONDS(기본 300초=5분, 오늘 액션
카드와 동일)마다 get_hip4_snapshot()을 다시 불러서 직전 스냅샷과 비교하는
폴링이다. 5분보다 짧은 급변은 최대 5분 지연 후에나 감지된다 - Bazaar/README
설명에 "최대 poll interval만큼 지연될 수 있음"을 명시할 것.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.logic import get_hip4_snapshot

logger = logging.getLogger("alphapipeline.events")

# 지원하는 이벤트 타입. hip4.resolving은 의도적으로 뺐다 - Hyperliquid
# outcomeMeta 응답에 정산/만기 시각으로 검증된 필드를 아직 못 찾았고, 이
# 프로젝트의 "확인 안 된 데이터는 절대 지어내지 않는다" 원칙(README 참고) 상
# 추측으로 만들 수 없다. 나중에 실제 필드를 확인하면 추가한다.
SUPPORTED_EVENT_TYPES: dict[str, str] = {
    "hip4.prob_jump": (
        "An outcome's mid-price (implied probability) moved by at least the "
        "configured threshold (default: HIP4_EVENTS_JUMP_THRESHOLD_PCT, "
        f"currently {settings.HIP4_EVENTS_JUMP_THRESHOLD_PCT}) between two polls."
    ),
    "hip4.market_created": (
        "A new HIP-4 outcome/question appeared in Hyperliquid's outcomeMeta that "
        "was not present in the previous poll."
    ),
}

DATA_FILE = Path(
    os.getenv("HIP4_EVENTS_DATA_FILE", str(Path(__file__).resolve().parent.parent / "data" / "hip4_event_subscriptions.json"))
)

_lock = asyncio.Lock()
_subs: dict[str, dict] | None = None  # subscription_id -> record; None = 아직 디스크에서 안 읽음
_last_rows: dict[str, dict] | None = None  # 폴링 비교용 직전 스냅샷 (프로세스 메모리 전용, 영속화 안 함)


def _load_from_disk_sync() -> dict[str, dict]:
    try:
        if DATA_FILE.exists():
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("구독 파일(%s) 로드 실패 - 빈 상태로 시작합니다", DATA_FILE)
    return {}


def _save_to_disk_sync(data: dict[str, dict]) -> None:
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(DATA_FILE)
    except Exception:
        logger.exception("구독 파일(%s) 저장 실패 - 이번 변경은 재시작 시 사라질 수 있습니다", DATA_FILE)


async def _ensure_loaded() -> dict[str, dict]:
    global _subs
    if _subs is None:
        _subs = await asyncio.to_thread(_load_from_disk_sync)
    return _subs


async def _persist() -> None:
    assert _subs is not None
    await asyncio.to_thread(_save_to_disk_sync, dict(_subs))


# ===== SSRF 방어용 webhook_url 검증 =====
# 이 엔드포인트는 유료 API지만, 결제만 하면 "우리 서버가 임의 URL로 POST를
# 쏘게" 만들 수 있다는 뜻이기도 하다 - 사설 IP/루프백/클라우드 메타데이터
# 엔드포인트(169.254.169.254)로의 요청은 최소한으로 막아둔다. 완벽한 SSRF
# 방어(DNS rebinding 등)는 아니고, 가장 흔한 남용 형태만 걸러내는 수준이다.
def _is_webhook_url_allowed(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL을 파싱할 수 없습니다"
    if parsed.scheme not in ("http", "https"):
        return False, "http:// 또는 https:// URL만 허용됩니다"
    host = parsed.hostname or ""
    if not host:
        return False, "호스트가 없는 URL입니다"
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False, "로컬호스트로의 웹훅은 허용되지 않습니다"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # 도메인 이름 - DNS가 사설 IP로 풀리는 경우까지는 여기서 못 막음(정직하게 밝혀둠)
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        return False, "사설/예약 IP 대역으로의 웹훅은 허용되지 않습니다"
    return True, ""


def _new_subscription_id() -> str:
    return "sub_" + uuid.uuid4().hex[:20]


async def create_subscription(
    *,
    webhook_url: str,
    secret: str,
    event_types: list[str] | None = None,
    underlying: str | None = None,
    threshold_pct: float | None = None,
) -> dict:
    """GET /v1/prediction/hip4-alerts/subscribe-v2 핸들러가 부른다 (main.py 참고).

    성공 시 subscription 레코드(dict)를 반환하고, 검증 실패 시 ValueError를
    던진다 (호출부가 400으로 매핑).
    """
    subs = await _ensure_loaded()

    if not webhook_url:
        raise ValueError("webhook_url은 필수입니다")
    ok, reason = _is_webhook_url_allowed(webhook_url)
    if not ok:
        raise ValueError(f"webhook_url이 허용되지 않습니다: {reason}")
    if not secret or len(secret) < 16:
        raise ValueError("secret은 최소 16자 이상이어야 합니다 (서명 검증용 - 직접 생성해서 넣어주세요, 예: openssl rand -base64 32)")

    requested_types = event_types or list(SUPPORTED_EVENT_TYPES.keys())
    unknown = [t for t in requested_types if t not in SUPPORTED_EVENT_TYPES]
    if unknown:
        raise ValueError(
            f"지원하지 않는 event_types: {unknown}. 지원 목록: {list(SUPPORTED_EVENT_TYPES.keys())} "
            "(hip4.resolving은 아직 구현되지 않았습니다 - 이 모듈 docstring 참고)"
        )

    async with _lock:
        active = [s for s in subs.values() if s.get("status") == "active" and s["expires_at"] > time.time()]
        if len(active) >= settings.HIP4_EVENTS_MAX_SUBSCRIPTIONS:
            raise ValueError("서버의 활성 구독 수가 한도에 도달했습니다 - 잠시 후 다시 시도해주세요")

        sub_id = _new_subscription_id()
        now = time.time()
        record = {
            "subscription_id": sub_id,
            "webhook_url": webhook_url,
            "secret": secret,
            "event_types": requested_types,
            "underlying": underlying.strip().upper() if underlying else None,
            "threshold_pct": float(threshold_pct) if threshold_pct is not None else None,
            "status": "active",
            "created_at": now,
            "expires_at": now + settings.HIP4_EVENTS_SUBSCRIPTION_TTL_HOURS * 3600,
            "consecutive_failures": 0,
            "last_delivered_at": None,
        }
        subs[sub_id] = record
        await _persist()
        return record


async def cancel_subscription(*, subscription_id: str, secret: str) -> bool:
    """POST /v1/prediction/hip4-alerts/unsubscribe 핸들러가 부른다.

    구독 생성 시 받은 secret과 정확히 일치해야만 취소할 수 있다 (남의 구독을
    임의로 끊지 못하게 하는 최소한의 인증). 성공하면 True, 대상이 없거나
    secret이 틀리면 False.
    """
    subs = await _ensure_loaded()
    async with _lock:
        record = subs.get(subscription_id)
        if record is None:
            return False
        if not hmac.compare_digest(record.get("secret", ""), secret or ""):
            return False
        del subs[subscription_id]
        await _persist()
        return True


async def list_event_types() -> dict:
    """GET /v1/prediction/hip4-alerts/event-types 핸들러가 부른다 (무료)."""
    return {
        "enabled": settings.HIP4_EVENTS_ENABLED,
        "event_types": [
            {"type": t, "description": desc} for t, desc in SUPPORTED_EVENT_TYPES.items()
        ],
        "poll_interval_seconds": settings.HIP4_EVENTS_POLL_INTERVAL_SECONDS,
        "default_threshold_pct": settings.HIP4_EVENTS_JUMP_THRESHOLD_PCT,
        "subscription_ttl_hours": settings.HIP4_EVENTS_SUBSCRIPTION_TTL_HOURS,
        "delivery": "Standard Webhooks (webhook-id / webhook-timestamp / webhook-signature headers, HMAC-SHA256, see standardwebhooks.com)",
        "status": "EXPERIMENTAL - based on a draft spec (github.com/modelcontextprotocol/experimental-ext-triggers-events) that has not stabilized yet. Subscriptions are not guaranteed to survive a server restart.",
        "notice": (
            "This is 5-minute-interval polling of Hyperliquid's free HIP-4 info API, "
            "not a true push feed upstream - a jump can be reported up to "
            f"{settings.HIP4_EVENTS_POLL_INTERVAL_SECONDS} seconds after it happened."
        ),
    }


# ===== Standard Webhooks 서명 (오늘 액션 카드 스크립트와 동일 규약) =====
def _sign(secret: str, webhook_id: str, timestamp: str, body: str) -> str:
    digest = hmac.new(secret.encode(), f"{webhook_id}.{timestamp}.{body}".encode(), hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


async def _deliver(client: httpx.AsyncClient, record: dict, event: dict) -> bool:
    body = json.dumps(event, ensure_ascii=False)
    webhook_id = uuid.uuid4().hex
    timestamp = str(int(time.time()))
    signature = _sign(record["secret"], webhook_id, timestamp, body)
    try:
        resp = await client.post(
            record["webhook_url"],
            content=body,
            headers={
                "content-type": "application/json",
                "webhook-id": webhook_id,
                "webhook-timestamp": timestamp,
                "webhook-signature": signature,
            },
            timeout=10.0,
        )
        return 200 <= resp.status_code < 300
    except Exception:
        logger.warning("웹훅 전달 실패: subscription=%s url=%s", record["subscription_id"], record["webhook_url"], exc_info=True)
        return False


# ===== HIP-4 스냅샷 diff (폴링 1회분) =====
def _flatten_snapshot(snapshot: dict) -> dict[str, dict]:
    """get_hip4_snapshot()의 중첩 구조를 {row_key: {price, label, underlying}} 형태로 평탄화."""
    rows: dict[str, dict] = {}
    for m in snapshot.get("standalone_markets", []):
        oid = m.get("outcome_id")
        fields = m.get("fields") or {}
        underlying = fields.get("underlying") or fields.get("perp")
        for side in m.get("sides", []):
            key = f"standalone:{oid}:{side.get('name')}"
            rows[key] = {
                "price": side.get("price"),
                "label": f"{m.get('template')} / {side.get('name')}",
                "underlying": underlying,
                "outcome_id": oid,
            }
    for q in snapshot.get("grouped_questions", []):
        qid = q.get("question_id")
        fields = q.get("fields") or {}
        underlying = fields.get("underlying") or fields.get("perp")
        for o in q.get("outcomes", []):
            key = f"grouped:{qid}:{o.get('outcome_id')}"
            rows[key] = {
                "price": o.get("price"),
                "label": f"{q.get('template')} / {o.get('label')}",
                "underlying": underlying,
                "outcome_id": o.get("outcome_id"),
                "question_id": qid,
            }
        fallback = q.get("fallback")
        if fallback:
            key = f"grouped:{qid}:fallback"
            rows[key] = {
                "price": fallback.get("price"),
                "label": f"{q.get('template')} / none_of_the_above",
                "underlying": underlying,
                "question_id": qid,
            }
    return rows


def _matches_subscription(record: dict, ev_type: str, row: dict) -> bool:
    if ev_type not in record.get("event_types", []):
        return False
    if record.get("status") != "active":
        return False
    if record.get("expires_at", 0) <= time.time():
        return False
    sub_underlying = record.get("underlying")
    if sub_underlying and (row.get("underlying") or "").upper() != sub_underlying:
        return False
    return True


async def poll_once() -> int:
    """settings.HIP4_EVENTS_POLL_INTERVAL_SECONDS마다 app/scheduler.py가 호출한다.

    반환값은 이번 폴링에서 실제로 전달을 시도한 이벤트 수(디버그/로그용).
    """
    global _last_rows
    try:
        snapshot = await get_hip4_snapshot(limit=500)
    except Exception:
        logger.exception("HIP-4 이벤트 폴링: 스냅샷 조회 실패 - 이번 주기는 건너뜁니다")
        return 0

    rows = _flatten_snapshot(snapshot)
    prev_rows = _last_rows
    _last_rows = rows
    if prev_rows is None:
        # 콜드스타트: 비교 기준선만 세우고 끝낸다 - 안 그러면 서버가 막
        # 켜졌을 때 존재하는 마켓 전부가 "새로 생긴 마켓"으로 오탐된다.
        logger.info("HIP-4 이벤트 폴링 기준선 설정 완료 (%d개 row)", len(rows))
        return 0

    subs = await _ensure_loaded()
    if not subs:
        return 0  # 구독자가 없으면 diff 계산 이상은 할 필요 없음(가벼운 조기 종료)

    default_threshold = settings.HIP4_EVENTS_JUMP_THRESHOLD_PCT
    # market_created는 threshold와 무관하게 확정된 이벤트, prob_jump 후보는
    # delta가 구독별 threshold_pct에 따라 발동 여부가 갈리므로 끝까지
    # (key, row, prev_price, price, delta) 원본 형태로 들고 있다가, 구독을
    # 순회할 때 그 구독의 threshold로 판정한다.
    created_events: list[dict] = []
    jump_candidates: list[tuple[str, dict, float, float, float]] = []
    for key, row in rows.items():
        price = row.get("price")
        if price is None:
            continue
        if key not in prev_rows:
            created_events.append(
                {"type": "hip4.market_created", "row_key": key, "price": price, **{k: v for k, v in row.items() if k != "price"}}
            )
            continue
        prev_price = prev_rows[key].get("price")
        if prev_price is None:
            continue
        delta = price - prev_price
        jump_candidates.append((key, row, prev_price, price, delta))

    if not created_events and not jump_candidates:
        return 0

    delivered = 0
    async with httpx.AsyncClient() as client:
        active_records = [s for s in subs.values() if s.get("status") == "active" and s.get("expires_at", 0) > time.time()]
        for record in active_records:
            sub_threshold = record.get("threshold_pct") if record.get("threshold_pct") is not None else default_threshold
            to_send: list[dict] = []
            for ev in created_events:
                if _matches_subscription(record, ev["type"], ev):
                    to_send.append(ev)
            for key, row, prev_price, price, delta in jump_candidates:
                if abs(delta) < sub_threshold:
                    continue
                if not _matches_subscription(record, "hip4.prob_jump", row):
                    continue
                to_send.append(
                    {
                        "type": "hip4.prob_jump",
                        "row_key": key,
                        "from": round(prev_price, 4),
                        "to": round(price, 4),
                        "delta": round(delta, 4),
                        **{k: v for k, v in row.items() if k != "price"},
                    }
                )

            for ev in to_send:
                payload = {
                    "event": ev["type"],
                    "subscription_id": record["subscription_id"],
                    "generated_at": snapshot.get("generated_at"),
                    **{k: v for k, v in ev.items() if k != "type"},
                }
                ok = await _deliver(client, record, payload)
                delivered += 1
                if ok:
                    record["consecutive_failures"] = 0
                    record["last_delivered_at"] = time.time()
                else:
                    record["consecutive_failures"] = record.get("consecutive_failures", 0) + 1
                    if record["consecutive_failures"] >= settings.HIP4_EVENTS_MAX_CONSECUTIVE_FAILURES:
                        logger.warning(
                            "구독 %s이 연속 실패 %d회로 자동 비활성화됩니다 (url=%s)",
                            record["subscription_id"], record["consecutive_failures"], record["webhook_url"],
                        )
                        record["status"] = "disabled"

    async with _lock:
        await _persist()
    return delivered
