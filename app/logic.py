"""
핵심 가공 로직 (Step 1 - 사업계획서 섹션 2).
1) /v1/unlocks/dump-risk : 락업 해제 덤핑 위험도
2) /v1/market/kimchi-alert : 거래소 간 차익/급변 감지
"""
import time
from datetime import datetime, timezone, timedelta
from app import data_sources as ds
from app.cache import unlock_cache, price_cache
from app.config import settings

KST = timezone(timedelta(hours=9))

# 사업계획서 기준 임계값
DUMP_RISK_SUPPLY_PCT_THRESHOLD = 3.0   # 유통량 대비 3% 이상 해제
DUMP_RISK_WINDOW_DAYS = 7              # D-7 이내
KIMCHI_REVERSE_PREMIUM_THRESHOLD = -1.5  # 역프 -1.5% 이하
KIMCHI_SURGE_THRESHOLD = 3.0             # 1시간 내 프리미엄 3%p 이상 급등

# VC/팀 물량으로 분류할 카테고리 키워드 (DropsTab category/unlockType 필드 기준 추정)
INSIDER_CATEGORY_KEYWORDS = ("insider", "team", "advisor", "investor", "vc", "seed", "private")


def _first(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _parse_event_date(raw) -> datetime | None:
    """DropsTab 응답의 날짜 필드가 epoch(초/밀리초)든 'YYYY-MM-DD' 문자열이든 파싱."""
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            ts = raw / 1000 if raw > 10_000_000_000 else raw  # ms -> s 추정
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(raw, str):
            s = raw.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError):
        return None
    return None


def _timestamp_now() -> str:
    now_utc = datetime.now(timezone.utc)
    now_kst = now_utc.astimezone(KST)
    return {
        "utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kst": now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
    }


def _is_insider_category(category: str) -> bool:
    if not category:
        return False
    c = category.lower()
    return any(k in c for k in INSIDER_CATEGORY_KEYWORDS)


def _process_event(ev: dict, now: datetime, window_end: datetime) -> dict | None:
    """DropsTab 언락 이벤트 1건을 파싱해 조건에 맞으면 결과 dict, 아니면 None."""
    event_time = _parse_event_date(_first(ev, "date", "unlockDate", "eventDate", "timestamp"))
    if event_time is None or not (now <= event_time <= window_end):
        return None

    supply_pct = _first(ev, "percentage", "unlockPercentage", "percentOfSupply", "supplyPercent")
    try:
        supply_pct = float(supply_pct)
    except (TypeError, ValueError):
        return None
    if supply_pct < DUMP_RISK_SUPPLY_PCT_THRESHOLD:
        return None

    token_symbol = str(_first(ev, "symbol", "coin", "ticker", "name") or "UNKNOWN").upper()
    amount = _first(ev, "amount", "unlockAmount", "noOfTokens")
    category = str(_first(ev, "category", "unlockType", "type") or "")
    is_insider = _is_insider_category(category)

    return {
        "token": token_symbol,
        "unlock_date_utc": event_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days_until_unlock": round((event_time - now).total_seconds() / 86400, 1),
        "unlock_supply_pct": round(supply_pct, 2),
        "unlock_amount": amount,
        "is_insider_vc_team": is_insider,
        "category": category or "unknown",
        "risk_level": _risk_level(supply_pct, is_insider, None),
    }


async def _enrich_with_volume_impact(item: dict) -> dict:
    """거래량 대비 충격량(%)을 바이낸스 24h 거래대금으로 계산해 채워넣는다."""
    try:
        stats = await ds.get_binance_24h_stats(f"{item['token']}USDT")
        vol_usd = float(stats.get("quoteVolume", 0))
        price = float(stats.get("lastPrice", 0))
        amount = float(item.get("unlock_amount") or 0)
        if vol_usd > 0 and amount > 0 and price > 0:
            impact_pct = round((amount * price / vol_usd) * 100, 2)
            item["volume_impact_pct"] = impact_pct
            item["risk_level"] = _risk_level(item["unlock_supply_pct"], item["is_insider_vc_team"], impact_pct)
        else:
            item["volume_impact_pct"] = None
    except Exception:
        item["volume_impact_pct"] = None
    return item


async def refresh_unlock_cache() -> dict:
    """
    스케줄러가 settings.UNLOCK_REFRESH_INTERVAL_HOURS 주기(기본 24시간)로 호출.
    DropsTab의 전체 락업 해제 이벤트 목록을 페이지네이션으로 훑어서, D-7 이내
    유통량 3% 이상 해제되는 이벤트만 걸러 위험도를 계산한다.

    (원래 DeFiLlama 무료 API를 썼으나 emissions/unlocks 데이터가 유료 Pro API로
    이전되어 DropsTab으로 교체함 - README "락업 데이터 소스 변경 이력" 참고)

    락업 해제 일정은 몇 주 전에 미리 확정되는 경우가 대부분이라 실시간으로
    갱신할 필요가 없다 - 그래서 이 스캔은 드문 주기로만 실행되고(기본 하루 1회),
    그 결과가 다음 스캔 때까지 캐시된 채로 서빙된다.
    """
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(days=DUMP_RISK_WINDOW_DAYS)

    if not settings.DROPSTAB_API_KEY:
        payload = {
            "generated_at": _timestamp_now(),
            "window_days": DUMP_RISK_WINDOW_DAYS,
            "supply_pct_threshold": DUMP_RISK_SUPPLY_PCT_THRESHOLD,
            "protocols_scanned": 0,
            "count": 0,
            "unlocks": [],
            "notice": (
                "DROPSTAB_API_KEY가 설정되지 않아 락업 데이터를 가져오지 않았습니다. "
                "README의 '락업 데이터 소스 변경 이력'을 참고해 DropsTab API 키를 발급받아 .env에 넣어주세요."
            ),
        }
        unlock_cache["dump_risk"] = payload
        return payload

    raw_events = []
    scanned = 0
    for page in range(settings.MAX_UNLOCK_SCAN_PAGES):
        try:
            resp = await ds.get_dropstab_token_unlocks(page=page, page_size=settings.DROPSTAB_PAGE_SIZE)
        except Exception:
            break  # API 오류/키 만료 등 - 지금까지 모은 것만 사용

        page_items = _first(resp, "data", "items", "content", "result") or []
        if not page_items:
            break
        raw_events.extend(page_items)
        scanned += len(page_items)
        if len(page_items) < settings.DROPSTAB_PAGE_SIZE:
            break  # 마지막 페이지

    candidates = [_process_event(ev, now, window_end) for ev in raw_events]
    candidates = [c for c in candidates if c is not None]

    results = []
    for item in candidates:
        results.append(await _enrich_with_volume_impact(item))

    results.sort(key=lambda x: x["unlock_supply_pct"], reverse=True)
    payload = {
        "generated_at": _timestamp_now(),
        "window_days": DUMP_RISK_WINDOW_DAYS,
        "supply_pct_threshold": DUMP_RISK_SUPPLY_PCT_THRESHOLD,
        "protocols_scanned": scanned,
        "count": len(results),
        "unlocks": results,
    }
    unlock_cache["dump_risk"] = payload
    return payload


def _risk_level(supply_pct: float, is_insider: bool, impact_pct) -> str:
    score = supply_pct
    if is_insider:
        score += 2
    if impact_pct and impact_pct > 50:
        score += 3
    if score >= 8:
        return "HIGH"
    if score >= 4:
        return "MEDIUM"
    return "LOW"


async def get_dump_risk() -> dict:
    cached = unlock_cache.get("dump_risk")
    if cached:
        return cached
    # 캐시가 비어있으면(서버 첫 기동 등) 즉석에서 1회 계산
    return await refresh_unlock_cache()


async def get_symbol_dump_risk(symbol: str) -> dict:
    """
    특정 심볼 1개에 대한 락업 해제 D-Day / 유통량 대비 해제 비율 / 매도압력 점수.
    MCP 도구 get_token_dump_risk가 사용하는 진입점.

    비즈니스 결정: dump-risk는 DropsTab 유료 플랜(Advanced, $59/월)이 있어야
    실데이터가 나오는데, 수요 검증 전까지는 결제하지 않기로 함 - README
    "락업 데이터 소스 변경 이력" 참고. 그래서 DUMP_RISK_ENABLED=false 이거나
    DROPSTAB_API_KEY가 비어있으면, 에러를 던지는 대신 "아직 서비스 준비 중"이라는
    구조화된 정상 응답을 돌려준다 (호출자가 이걸 실패로 오인하지 않도록 available=False로 명시).
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "invalid_symbol",
            "message": "symbol 파라미터가 비어 있습니다. 예: 'ATH', 'AO', 'CPOOL'",
        }

    if not settings.DUMP_RISK_ENABLED or not settings.DROPSTAB_API_KEY:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "feature_not_enabled",
            "message": (
                "dump-risk 기능은 현재 준비 중입니다 (DropsTab 유료 플랜 필요, 수요 검증 후 오픈 예정). "
                "kimchi-alert / ai-markdown 도구를 먼저 이용해주세요."
            ),
        }

    try:
        detail = await ds.get_dropstab_token_unlock_detail(symbol)
    except Exception as e:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "upstream_error",
            "message": f"DropsTab 조회 실패: {e}",
        }

    events = _first(detail, "events", "unlocks", "data", "items") or []
    if isinstance(detail, list):
        events = detail

    now = datetime.now(timezone.utc)
    upcoming = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        event_time = _parse_event_date(_first(ev, "date", "unlockDate", "eventDate", "timestamp"))
        if event_time is None or event_time < now:
            continue
        supply_pct = _first(ev, "percentage", "unlockPercentage", "percentOfSupply", "supplyPercent")
        try:
            supply_pct = float(supply_pct)
        except (TypeError, ValueError):
            supply_pct = None
        upcoming.append((event_time, ev, supply_pct))

    if not upcoming:
        return {
            "available": True,
            "symbol": symbol,
            "reason": "no_upcoming_unlock",
            "message": f"{symbol}에 대한 예정된 락업 해제 이벤트를 찾지 못했습니다.",
        }

    upcoming.sort(key=lambda t: t[0])
    event_time, ev, supply_pct = upcoming[0]

    amount = _first(ev, "amount", "unlockAmount", "noOfTokens")
    category = str(_first(ev, "category", "unlockType", "type") or "")
    is_insider = _is_insider_category(category)

    result = {
        "available": True,
        "symbol": symbol,
        "unlock_date_utc": event_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "days_until_unlock": round((event_time - now).total_seconds() / 86400, 1),
        "unlock_supply_pct": round(supply_pct, 2) if supply_pct is not None else None,
        "unlock_amount": amount,
        "is_insider_vc_team": is_insider,
        "category": category or "unknown",
        "generated_at": _timestamp_now(),
    }

    if supply_pct is not None:
        item = {
            "token": symbol,
            "unlock_amount": amount,
            "unlock_supply_pct": round(supply_pct, 2),
            "is_insider_vc_team": is_insider,
        }
        enriched = await _enrich_with_volume_impact(item)
        result["volume_impact_pct"] = enriched.get("volume_impact_pct")
        result["sell_pressure_risk_level"] = enriched.get("risk_level", _risk_level(supply_pct, is_insider, None))
    else:
        result["volume_impact_pct"] = None
        result["sell_pressure_risk_level"] = None

    return result


# 프리미엄 급등 감지를 위해 최근 1시간 값을 짧게 보관 (심볼별)
_premium_history: dict = {}


async def get_kimchi_alert(symbol: str = "BTC") -> dict:
    """
    업비트(KRW-{symbol}) vs 바이낸스({symbol}USDT) 김치프리미엄 계산.
    캐시(app/cache.py, TTL settings.KIMCHI_CACHE_TTL_SECONDS)로 실시간성을
    최대한 유지하면서 API 폭주만 살짝 방지한다.
    """
    cache_key = f"kimchi:{symbol}"
    cached = price_cache.get(cache_key)
    if cached:
        return cached

    upbit_data = await ds.get_upbit_price_krw(f"KRW-{symbol}")
    upbit_price_krw = float(upbit_data.get("trade_price", 0))
    usdkrw_rate = await ds.get_upbit_usdkrw_rate()
    binance_price_usdt = await ds.get_binance_price_usdt(f"{symbol}USDT")

    if usdkrw_rate <= 0 or binance_price_usdt <= 0:
        raise ValueError("가격 데이터를 가져오지 못했습니다 (환율 또는 바이낸스 시세 오류)")

    binance_price_krw_equiv = binance_price_usdt * usdkrw_rate
    premium_pct = ((upbit_price_krw / binance_price_krw_equiv) - 1) * 100

    now_ts = time.time()
    history = _premium_history.setdefault(symbol, [])
    history.append((now_ts, premium_pct))
    # 1시간 이전 기록 제거
    cutoff = now_ts - 3600
    _premium_history[symbol] = [h for h in history if h[0] >= cutoff]

    premium_1h_ago = _premium_history[symbol][0][1] if _premium_history[symbol] else premium_pct
    surge_1h_pct = round(premium_pct - premium_1h_ago, 2)

    is_reverse_premium_alert = premium_pct <= KIMCHI_REVERSE_PREMIUM_THRESHOLD
    is_surge_alert = surge_1h_pct >= KIMCHI_SURGE_THRESHOLD

    payload = {
        "generated_at": _timestamp_now(),
        "symbol": symbol,
        "upbit_price_krw": upbit_price_krw,
        "binance_price_usdt": binance_price_usdt,
        "usdkrw_rate_estimate": round(usdkrw_rate, 2),
        "kimchi_premium_pct": round(premium_pct, 3),
        "premium_change_1h_pct": surge_1h_pct,
        "alerts": {
            "reverse_premium": is_reverse_premium_alert,
            "premium_surge_1h": is_surge_alert,
        },
        "thresholds": {
            "reverse_premium_pct": KIMCHI_REVERSE_PREMIUM_THRESHOLD,
            "surge_1h_pct": KIMCHI_SURGE_THRESHOLD,
        },
    }
    price_cache[cache_key] = payload
    return payload
