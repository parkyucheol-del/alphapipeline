"""
핵심 가공 로직 (Step 1 - 사업계획서 섹션 2).
1) /v1/unlocks/dump-risk : 락업 해제 덤핑 위험도
2) /v1/market/kimchi-alert : 거래소 간 차익/급변 감지
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from app import data_sources as ds
from app.cache import unlock_cache, price_cache, ttl_cached
from app.config import settings

logger = logging.getLogger("alphapipeline")

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

    DROPSTAB_API_KEY가 있으면 기존 DropsTab 경로(유료 플랜, 정확한 D-day 제공)를
    쓰고, 없으면 Sablier 온체인 조회 경로(무료, 무료인 대신 "지금 얼마나
    잠겨있는가" 기준)로 자동 전환한다. 어느 쪽이든 실제 서빙 상태(coming_soon
    아님)로 동작하는 게 목표 - dump-risk 재설계 배경은
    app/data_sources.py의 "dump-risk 온체인(Sablier) 재설계" 섹션 참고.
    """
    if settings.DROPSTAB_API_KEY:
        return await _refresh_unlock_cache_dropstab()
    return await _refresh_unlock_cache_onchain()


async def _refresh_unlock_cache_dropstab() -> dict:
    """
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


async def _refresh_unlock_cache_onchain() -> dict:
    """
    DropsTab 유료 플랜 없이 돌아가는 무료 경로. Sablier 프로토콜로 온체인
    베스팅되는 물량을 직접 스캔해서, "유통량 대비 아직 안 풀린 잠긴 물량 비율"이
    임계값(DUMP_RISK_SUPPLY_PCT_THRESHOLD) 이상인 토큰만 골라낸다.

    DropsTab 경로와의 핵심 차이(README/design 문서에 상세):
    - 정확한 "D-day"(cliff/베스팅 종료 타임스탬프)는 제공하지 못한다 - Sablier
      GraphQL 스키마의 해당 필드명을 이 세션에서 확인하지 못했기 때문
      (app/data_sources.py 상단 주석 참고). 그래서 days_until_unlock=None,
      timing_precision="pending_schema_verification"으로 명시한다.
    - "락업 해제 예정"이 아니라 "현재 잠겨있는 물량"을 본다 - 즉 몇 %가 앞으로
      언제 풀리는지가 아니라, 지금 이 순간 유통량 대비 얼마나 큰 물량이
      베스팅 컨트랙트에 묶여있는지(잠재적 매도 물량 규모)를 보여준다.
    - Sablier로 베스팅되는 토큰만 잡힌다 - 다른 방식(커스텀 컨트랙트, 거래소
      자체 락업)은 커버하지 못한다. 그래서 coverage_notice를 응답에 포함시켜
      "이 목록에 없다고 안전하다는 뜻이 아니다"를 명시한다.
    """
    try:
        streams = await ds.get_sablier_active_streams(limit=200)
    except Exception as e:
        logger.warning("Sablier 온체인 조회 실패: %s", e)
        streams = []

    # 심볼별로 잠긴 물량(intactAmount, decimals 반영)을 합산
    locked_by_symbol: dict[str, float] = {}
    contract_by_symbol: dict[str, str] = {}
    for s in streams:
        asset = s.get("asset") or {}
        symbol = str(asset.get("symbol") or "").upper()
        decimals = asset.get("decimals")
        if not symbol or decimals is None:
            continue
        try:
            intact_raw = float(s.get("intactAmount") or 0)
            intact = intact_raw / (10 ** int(decimals))
        except (TypeError, ValueError):
            continue
        if intact <= 0:
            continue
        locked_by_symbol[symbol] = locked_by_symbol.get(symbol, 0.0) + intact
        contract_by_symbol.setdefault(symbol, asset.get("address", ""))

    results = []
    for symbol, locked_amount in locked_by_symbol.items():
        coin_id = ds._COINGECKO_IDS.get(symbol)
        if not coin_id:
            continue  # 유통량을 조회할 수 있는 매핑이 없는 심볼은 스킵 (추측하지 않음)
        try:
            info = await ds.get_coingecko_token_contract_and_supply(coin_id)
        except Exception as e:
            logger.warning("CoinGecko 유통량 조회 실패 (%s): %s", symbol, e)
            continue

        circulating_supply = info.get("circulating_supply")
        if not circulating_supply or circulating_supply <= 0:
            continue

        unlock_supply_pct = round((locked_amount / circulating_supply) * 100, 2)
        if unlock_supply_pct < DUMP_RISK_SUPPLY_PCT_THRESHOLD:
            continue

        results.append({
            "token": symbol,
            "onchain_contract": contract_by_symbol.get(symbol),
            "unlock_date_utc": None,
            "days_until_unlock": None,
            "timing_precision": "pending_schema_verification",
            "unlock_supply_pct": unlock_supply_pct,
            "unlock_amount": round(locked_amount, 4),
            "is_insider_vc_team": None,
            "category": "onchain_vesting_stream (unclassified)",
            "risk_level": _risk_level(unlock_supply_pct, False, None),
            "data_source": "onchain_sablier",
        })

    results.sort(key=lambda x: x["unlock_supply_pct"], reverse=True)
    payload = {
        "generated_at": _timestamp_now(),
        "window_days": None,
        "supply_pct_threshold": DUMP_RISK_SUPPLY_PCT_THRESHOLD,
        "protocols_scanned": len(streams),
        "count": len(results),
        "unlocks": results,
        "data_source": "onchain_sablier",
        "coverage_notice": (
            "이 데이터는 Sablier 프로토콜로 온체인 베스팅되는 물량만 스캔한 결과입니다. "
            "다른 방식(커스텀 컨트랙트, 거래소 자체 락업 등)의 락업은 포함되지 않으므로, "
            "이 목록에 없다고 해서 해당 토큰에 락업이 없다는 뜻은 아닙니다. "
            "또한 정확한 해제 시점(D-day)은 아직 제공하지 않으며, '현재 잠겨있는 물량 "
            "비율'만 계산합니다 (timing_precision=pending_schema_verification)."
        ),
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


async def _get_symbol_dump_risk_onchain(symbol: str) -> dict:
    """
    get_symbol_dump_risk()의 온체인(Sablier) 경로. DropsTab 키 없이 특정 심볼
    하나에 대해 "현재 온체인에 잠겨있는 물량 비율"을 계산한다.

    스트림이 하나도 안 잡히면, 이건 "안전하다"는 뜻이 아니라 "Sablier로는
    못 찾았다"는 뜻이므로 reason=no_onchain_vesting_found로 정직하게 표시하고
    안전하다고 암시하는 문구는 절대 넣지 않는다.
    """
    coin_id = ds._COINGECKO_IDS.get(symbol)
    if not coin_id:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "symbol_not_mapped",
            "message": (
                f"{symbol}에 대한 CoinGecko 매핑이 없어 컨트랙트 주소/유통량을 조회할 수 없습니다. "
                "app/data_sources.py의 _COINGECKO_IDS에 추가하면 지원됩니다."
            ),
        }

    try:
        info = await ds.get_coingecko_token_contract_and_supply(coin_id)
    except Exception as e:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "upstream_error",
            "message": f"CoinGecko 조회 실패: {e}",
        }

    circulating_supply = info.get("circulating_supply")
    platforms = info.get("platforms") or {}
    if not circulating_supply or circulating_supply <= 0 or not platforms:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "insufficient_token_metadata",
            "message": f"{symbol}의 유통량 또는 컨트랙트 주소 정보를 CoinGecko에서 얻지 못했습니다.",
        }

    total_locked = 0.0
    matched_contract = None
    for chain, address in platforms.items():
        try:
            streams = await ds.get_sablier_streams_for_token(address)
        except Exception as e:
            logger.warning("Sablier 조회 실패 (%s/%s): %s", symbol, chain, e)
            continue
        for s in streams:
            asset = s.get("asset") or {}
            decimals = asset.get("decimals")
            if decimals is None:
                continue
            try:
                intact = float(s.get("intactAmount") or 0) / (10 ** int(decimals))
            except (TypeError, ValueError):
                continue
            if intact > 0:
                total_locked += intact
                matched_contract = matched_contract or address

    if total_locked <= 0:
        return {
            "available": True,
            "symbol": symbol,
            "reason": "no_onchain_vesting_found",
            "message": (
                f"{symbol}에 대해 Sablier 프로토콜로 베스팅되는 활성 스트림을 찾지 못했습니다. "
                "이는 '락업이 없다'는 뜻이 아니라 '이 방법으로는 못 찾았다'는 뜻입니다 - "
                "다른 방식(커스텀 컨트랙트, 거래소 자체 락업)의 베스팅은 이 조회로 잡히지 않습니다."
            ),
            "generated_at": _timestamp_now(),
        }

    unlock_supply_pct = round((total_locked / circulating_supply) * 100, 2)
    return {
        "available": True,
        "symbol": symbol,
        "onchain_contract": matched_contract,
        "unlock_date_utc": None,
        "days_until_unlock": None,
        "timing_precision": "pending_schema_verification",
        "unlock_supply_pct": unlock_supply_pct,
        "unlock_amount": round(total_locked, 4),
        "is_insider_vc_team": None,
        "category": "onchain_vesting_stream (unclassified)",
        "sell_pressure_risk_level": _risk_level(unlock_supply_pct, False, None),
        "data_source": "onchain_sablier",
        "generated_at": _timestamp_now(),
    }


async def get_symbol_dump_risk(symbol: str) -> dict:
    """
    특정 심볼 1개에 대한 락업 해제 D-Day / 유통량 대비 해제 비율 / 매도압력 점수.
    MCP 도구 get_token_dump_risk가 사용하는 진입점.

    DropsTab API 키가 있으면 DropsTab 경로(정확한 D-day 제공)를 쓰고, 없으면
    Sablier 온체인 조회 경로(_get_symbol_dump_risk_onchain, 무료지만 정확한
    날짜 대신 "현재 잠긴 물량 비율"만 제공)로 자동 전환한다.
    """
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "invalid_symbol",
            "message": "symbol 파라미터가 비어 있습니다. 예: 'ATH', 'AO', 'CPOOL'",
        }

    if not settings.DROPSTAB_API_KEY:
        return await _get_symbol_dump_risk_onchain(symbol)

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


@ttl_cached(price_cache, key_fn=lambda symbol="BTC": f"kimchi:{symbol}")
async def get_kimchi_alert(symbol: str = "BTC") -> dict:
    """
    업비트(KRW-{symbol}) vs 코인베이스/CoinGecko({symbol}) 김치프리미엄 계산.
    app/cache.py의 ttl_cached() 데코레이터로 캐싱한다(TTL은
    settings.KIMCHI_CACHE_TTL_SECONDS, 2026-09 기준 30초 - 봇의 초 단위 연타
    호출로부터 원가를 방어하는 목적. 캐시와 무관하게 호출자는 매번 x402로
    과금된다 - app/cache.py의 ttl_cached() docstring "왜 이게 순마진 100%
    방어인가" 참고).
    """
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
    return payload
