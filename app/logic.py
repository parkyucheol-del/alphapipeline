"""
핵심 가공 로직 (Step 1 - 사업계획서 섹션 2).
1) /v1/unlocks/dump-risk : 락업 해제 덤핑 위험도
2) /v1/market/kimchi-alert : 거래소 간 차익/급변 감지
"""
import asyncio
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
                "DROPSTAB_API_KEY is not set, so unlock data was not fetched. "
                "See the README's 'Unlock Data Source Change History' section to get a "
                "DropsTab API key and add it to .env."
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
            "This data only covers supply vested on-chain via the Sablier protocol. "
            "Lockups through other mechanisms (custom vesting contracts, exchange-side "
            "lockups, etc.) are not covered, so a token's absence from this list does "
            "not mean it has no lockup. Exact unlock timing (D-day) is not yet "
            "provided; this only computes the 'currently locked' supply ratio "
            "(timing_precision=pending_schema_verification)."
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
                f"No CoinGecko mapping exists for {symbol}, so its contract address/"
                "circulating supply could not be looked up. Add it to _COINGECKO_IDS "
                "in app/data_sources.py to support it."
            ),
        }

    try:
        info = await ds.get_coingecko_token_contract_and_supply(coin_id)
    except Exception as e:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "upstream_error",
            "message": f"CoinGecko lookup failed: {e}",
        }

    circulating_supply = info.get("circulating_supply")
    platforms = info.get("platforms") or {}
    if not circulating_supply or circulating_supply <= 0 or not platforms:
        return {
            "available": False,
            "symbol": symbol,
            "reason": "insufficient_token_metadata",
            "message": f"Could not obtain circulating supply or contract address info for {symbol} from CoinGecko.",
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
                f"No active Sablier vesting streams were found for {symbol}. "
                "This does not mean there is no lockup - it means this method did not "
                "find one. Vesting via other mechanisms (custom vesting contracts, "
                "exchange-side lockups) is not detected by this lookup."
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
            "message": "The symbol parameter is empty. Example: 'ATH', 'AO', 'CPOOL'",
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
            "message": f"DropsTab lookup failed: {e}",
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
            "message": f"No upcoming unlock events were found for {symbol}.",
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



def _bool_or_none(v) -> bool | None:
    """GoPlus는 불리언을 문자열 "1"/"0"으로 주는 경우가 많아 통일해서 변환한다."""
    if v is None:
        return None
    return str(v) == "1" or v is True


async def get_token_risk(chain_id: int, contract_address: str) -> dict:
    """
    GoPlus Security를 1순위로, 실패 시 Honeypot.is로 폴백해서 토큰 보안/허니팟
    위험도를 조회한다. GET /v1/security/token-risk가 사용한다 (main.py 참고).
    """
    contract_address = contract_address.lower()
    owner_address = None
    data_source = "goplus"
    notice = None

    try:
        gp = await ds.get_goplus_token_security(chain_id, contract_address)
        is_honeypot = _bool_or_none(gp.get("is_honeypot"))
        buy_tax = float(gp.get("buy_tax") or 0) * 100
        sell_tax = float(gp.get("sell_tax") or 0) * 100
        is_mintable = _bool_or_none(gp.get("is_mintable"))
        is_open_source = _bool_or_none(gp.get("is_open_source"))
        owner_address = gp.get("owner_address") or None
        owner_renounced = (
            owner_address in ("", "0x0000000000000000000000000000000000000000", None)
            if owner_address is not None
            else None
        )
        holder_count = (
            int(gp["holder_count"]) if gp.get("holder_count") not in (None, "") else None
        )
        is_in_dex = _bool_or_none(gp.get("is_in_dex"))
        token_name = gp.get("token_name") or None
        token_symbol = gp.get("token_symbol") or None
    except Exception as e:
        logger.warning("GoPlus 조회 실패, Honeypot.is로 폴백합니다: %s", e)
        try:
            hp = await ds.get_honeypot_is_check(chain_id, contract_address)
        except Exception as e2:
            return {
                "generated_at": _timestamp_now(),
                "chain_id": chain_id,
                "contract_address": contract_address,
                "risk_level": "UNKNOWN",
                "risk_flags": ["data_unavailable"],
                "data_source": "none",
                "notice": f"Both GoPlus and Honeypot.is lookups failed: {e2}",
            }
        honeypot_result = hp.get("honeypotResult") or {}
        simulation = hp.get("simulationResult") or {}
        contract_code = hp.get("contractCode") or {}
        token_info = hp.get("token") or {}
        is_honeypot = honeypot_result.get("isHoneypot")
        buy_tax = simulation.get("buyTax")
        sell_tax = simulation.get("sellTax")
        is_mintable = None
        is_open_source = contract_code.get("openSource")
        owner_renounced = None
        holder_count = token_info.get("totalHolders")
        is_in_dex = None
        token_name = token_info.get("name")
        token_symbol = token_info.get("symbol")
        data_source = "honeypot_is"
        notice = "GoPlus lookup failed; using Honeypot.is fallback data (narrower field coverage)."

    flags = []
    if is_honeypot:
        flags.append("honeypot")
    if is_mintable:
        flags.append("mintable")
    if is_open_source is False:
        flags.append("closed_source")
    if owner_renounced is False:
        flags.append("owner_not_renounced")
    if buy_tax and buy_tax >= 10:
        flags.append("high_buy_tax")
    if sell_tax and sell_tax >= 10:
        flags.append("high_sell_tax")

    if is_honeypot or (sell_tax and sell_tax >= 50):
        risk_level = "HIGH"
    elif flags:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "generated_at": _timestamp_now(),
        "chain_id": chain_id,
        "contract_address": contract_address,
        "token_name": token_name,
        "token_symbol": token_symbol,
        "is_honeypot": is_honeypot,
        "buy_tax_pct": round(buy_tax, 2) if buy_tax is not None else None,
        "sell_tax_pct": round(sell_tax, 2) if sell_tax is not None else None,
        "is_mintable": is_mintable,
        "is_open_source": is_open_source,
        "owner_renounced": owner_renounced,
        "owner_address": owner_address if data_source == "goplus" else None,
        "holder_count": holder_count,
        "is_in_dex": is_in_dex,
        "risk_level": risk_level,
        "risk_flags": flags,
        "data_source": data_source,
        "notice": notice,
    }


_LP_BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000dead",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000000",
}

_CONTRACT_HEALTH_NOTICE = (
    "LP lock/burn detection comes from GoPlus Security's lp_holders field, which "
    "flags an address as is_locked only when GoPlus recognizes it as a known "
    "third-party locker contract (e.g. Unicrypt, Team.Finance) - coverage varies by "
    "chain and is generally weaker outside Ethereum/BSC, so a low lp_locked_pct can "
    "mean 'actually unlocked' or just 'GoPlus doesn't recognize this locker'. Burn "
    "addresses are matched against a small known list and are always counted as "
    "permanently secured. This tool checks LP lock/burn status only - it does not "
    "re-run the honeypot/tax checks from security.token_risk, and it does not "
    "evaluate transaction history for suspicious activity. Always cross-verify on a "
    "block explorer before trusting liquidity as safe."
)


async def get_contract_health_audit(chain_id: int, contract_address: str) -> dict:
    """
    token_risk와 동일한 GoPlus Security token_security 응답(추가 업스트림 호출
    없음)에서 LP(유동성 풀) 보유자의 잠금(is_locked)/소각(burn address) 비율만
    뽑아서 계산하는 경량 감사 도구. GET /v1/security/contract-health-audit가
    사용한다 (main.py 참고).

    의도적으로 하지 않는 것: "의심스러운 트랜잭션" 같은 정성적 판단은 절대 하지
    않는다 - GoPlus가 준 숫자(잠금/소각 비율)만 그대로 계산해서 보여준다.
    Honeypot.is는 LP 락업 데이터를 제공하지 않으므로 이 도구는 GoPlus 실패 시
    폴백이 없다.
    """
    contract_address = contract_address.lower()

    try:
        gp = await ds.get_goplus_token_security(chain_id, contract_address)
    except Exception as e:
        return {
            "generated_at": _timestamp_now(),
            "chain_id": chain_id,
            "contract_address": contract_address,
            "liquidity_health": "UNKNOWN",
            "risk_flags": ["data_unavailable"],
            "data_source": "none",
            "notice": f"GoPlus lookup failed (this tool has no fallback source for LP lock data): {e}",
        }

    token_name = gp.get("token_name") or None
    token_symbol = gp.get("token_symbol") or None

    try:
        lp_total_supply = float(gp.get("lp_total_supply") or 0) or None
    except (TypeError, ValueError):
        lp_total_supply = None

    lp_holders_raw = gp.get("lp_holders") or []
    lp_holder_count = len(lp_holders_raw) if lp_holders_raw else None

    locked_pct = 0.0
    burned_pct = 0.0
    top_unlocked_holder_pct = 0.0
    for h in lp_holders_raw:
        addr = (h.get("address") or "").lower()
        try:
            pct = float(h.get("percent") or 0) * 100
        except (TypeError, ValueError):
            pct = 0.0
        is_locked_flag = str(h.get("is_locked")) in ("1", "true", "True")
        if addr in _LP_BURN_ADDRESSES:
            burned_pct += pct
        elif is_locked_flag:
            locked_pct += pct
        else:
            top_unlocked_holder_pct = max(top_unlocked_holder_pct, pct)

    risk_flags: list[str] = []
    if not lp_holders_raw:
        liquidity_health = "NO_LP_DATA"
        risk_flags.append("lp_data_unavailable")
    else:
        secured_pct = locked_pct + burned_pct
        if secured_pct >= 95:
            liquidity_health = "LOCKED"
        elif secured_pct >= 50:
            liquidity_health = "PARTIALLY_LOCKED"
        else:
            liquidity_health = "UNLOCKED"
            risk_flags.append("lp_mostly_unlocked")
        if top_unlocked_holder_pct >= 50:
            risk_flags.append("single_holder_concentration")

    return {
        "generated_at": _timestamp_now(),
        "chain_id": chain_id,
        "contract_address": contract_address,
        "token_name": token_name,
        "token_symbol": token_symbol,
        "lp_total_supply": lp_total_supply,
        "lp_holder_count": lp_holder_count,
        "lp_locked_pct": round(locked_pct, 2) if lp_holders_raw else None,
        "lp_burned_pct": round(burned_pct, 2) if lp_holders_raw else None,
        "top_unlocked_holder_pct": round(top_unlocked_holder_pct, 2) if lp_holders_raw else None,
        "liquidity_health": liquidity_health,
        "risk_flags": risk_flags,
        "data_source": "goplus",
        "notice": _CONTRACT_HEALTH_NOTICE,
    }


_WHALE_AUDIT_NOTICE = (
    "This tool audits a wallet address you already know - it does not discover or rank "
    "'smart money' wallets, because Hyperliquid's public API has no leaderboard or "
    "large-trader disclosure endpoint. mark_price is derived from "
    "position_value_usd / size (Hyperliquid does not return a separate live quote in "
    "this response), so it can lag the true mark price briefly during fast moves. "
    "risk_flags are computed from fixed numeric thresholds only (leverage >= 20x -> "
    "HIGH_LEVERAGE, distance_to_liquidation_pct < 15 -> NEAR_LIQUIDATION), not a "
    "judgment call about the trader."
)


async def get_whale_position_audit(address: str) -> dict:
    """
    Hyperliquid clearinghouseState 하나만 호출해서 특정 지갑의 현재 무기한 선물
    포지션 리스크(레버리지/청산가/미실현손익)를 계산한다. GET
    /v1/derivatives/whale-position-audit가 사용한다 (main.py 참고).

    설계 배경(app/data_sources.py의 get_hyperliquid_clearinghouse_state 주석
    참고): Hyperliquid 공개 API에는 "대규모 트레이더 발굴/리더보드" 기능이 없어서,
    이 도구는 "누가 스마트 머니냐"를 판단하지 않고 사용자가 이미 알고 있는 지갑
    주소 하나를 감사(audit)하는 용도로 설계했다. contract_health_audit와 동일한
    철학 - 업스트림 숫자를 그대로 계산해서 보여줄 뿐, 정성적 판단 없음.
    """
    address = (address or "").strip().lower()

    try:
        state = await ds.get_hyperliquid_clearinghouse_state(address)
    except Exception as e:
        return {
            "generated_at": _timestamp_now(),
            "wallet_address": address,
            "open_position_count": 0,
            "positions": [],
            "risk_flags": ["data_unavailable"],
            "data_source": "none",
            "notice": f"Hyperliquid lookup failed: {e}",
        }

    margin_summary = state.get("marginSummary") or {}

    def _f(val, default=None):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    account_value = _f(margin_summary.get("accountValue"))
    total_margin_used = _f(margin_summary.get("totalMarginUsed"))
    total_notional = _f(margin_summary.get("totalNtlPos"))
    withdrawable = _f(state.get("withdrawable"))
    margin_usage_pct = (
        round(total_margin_used / account_value * 100, 2)
        if total_margin_used is not None and account_value
        else None
    )

    positions: list[dict] = []
    risk_flags: list[str] = []
    for entry in state.get("assetPositions") or []:
        pos = entry.get("position") or {}
        szi = _f(pos.get("szi"), 0.0) or 0.0
        size = abs(szi)
        side = "LONG" if szi >= 0 else "SHORT"
        entry_price = _f(pos.get("entryPx"))
        position_value = _f(pos.get("positionValue"))
        leverage_obj = pos.get("leverage") or {}
        leverage_value = _f(leverage_obj.get("value"))
        leverage_type = leverage_obj.get("type") or "unknown"
        unrealized_pnl = _f(pos.get("unrealizedPnl"))
        liquidation_price = _f(pos.get("liquidationPx"))

        mark_price = (
            round(position_value / size, 6) if position_value is not None and size else None
        )
        distance_to_liquidation_pct = None
        if mark_price and liquidation_price is not None:
            distance_to_liquidation_pct = round(
                abs(mark_price - liquidation_price) / mark_price * 100, 2
            )

        if leverage_value is not None and leverage_value >= 20:
            risk_flags.append(f"HIGH_LEVERAGE:{pos.get('coin')}")
        if distance_to_liquidation_pct is not None and distance_to_liquidation_pct < 15:
            risk_flags.append(f"NEAR_LIQUIDATION:{pos.get('coin')}")

        positions.append(
            {
                "coin": pos.get("coin"),
                "side": side,
                "size": size,
                "entry_price": entry_price,
                "mark_price": mark_price,
                "position_value_usd": position_value,
                "leverage": leverage_value,
                "leverage_type": leverage_type,
                "unrealized_pnl_usd": unrealized_pnl,
                "liquidation_price": liquidation_price,
                "distance_to_liquidation_pct": distance_to_liquidation_pct,
            }
        )

    return {
        "generated_at": _timestamp_now(),
        "wallet_address": address,
        "account_value_usd": account_value,
        "total_margin_used_usd": total_margin_used,
        "total_notional_position_usd": total_notional,
        "withdrawable_usd": withdrawable,
        "margin_usage_pct": margin_usage_pct,
        "open_position_count": len(positions),
        "positions": positions,
        "risk_flags": risk_flags,
        "data_source": "Hyperliquid clearinghouseState (official public API)",
        "notice": _WHALE_AUDIT_NOTICE,
    }


_TOKEN_DIAGNOSTIC_NOTICE = (
    "This bundles security.token_risk and security.contract_health_audit into one "
    "call (same underlying GoPlus data, no new upstream calls, no new judgment logic) "
    "- it does not compute a composite score or letter grade. risk_flags is a plain "
    "deduped union of both tools' own flags; risk_flags_count is a count, not a "
    "weighted risk score. Does not cover token unlock/vesting risk - use "
    "unlocks.dump_risk separately for that (different input: symbol, not "
    "contract_address)."
)


async def get_token_diagnostic(chain_id: int, contract_address: str) -> dict:
    """
    security.token_risk + security.contract_health_audit를 병렬로 그대로 재호출해서
    하나의 응답으로 합친다. GET /v1/security/token-diagnostic가 사용한다 (main.py
    참고).

    의도적으로 하지 않는 것: 가중치를 매긴 합성 점수(예: "72/100")나 등급(A~F)을
    절대 계산하지 않는다 - 두 기존 도구가 이미 계산해둔 필드/플래그를 그대로
    노출하고, risk_flags는 둘의 합집합(중복 제거)만 낸다. 이건 사용자가 명시적으로
    요청한 설계 방향("점수 매기지 말고 무주관 집계 진단 도구")을 그대로 따른
    것이다. 두 하위 함수 다 GoPlus를 각자 호출하므로(중복 호출), 지연시간을
    줄이기 위해 asyncio.gather로 병렬 실행한다 - GoPlus는 무료라 원가에는 영향 없음.
    """
    risk_task = get_token_risk(chain_id, contract_address)
    health_task = get_contract_health_audit(chain_id, contract_address)
    risk, health = await asyncio.gather(risk_task, health_task)

    risk_flags = list(dict.fromkeys((risk.get("risk_flags") or []) + (health.get("risk_flags") or [])))

    data_sources = list(dict.fromkeys([risk.get("data_source"), health.get("data_source")]))
    checks_completed = sum(1 for d in (risk.get("data_source"), health.get("data_source")) if d and d != "none")

    return {
        "generated_at": _timestamp_now(),
        "chain_id": chain_id,
        "contract_address": contract_address.lower(),
        "token_name": risk.get("token_name") or health.get("token_name"),
        "token_symbol": risk.get("token_symbol") or health.get("token_symbol"),
        "is_honeypot": risk.get("is_honeypot"),
        "buy_tax_pct": risk.get("buy_tax_pct"),
        "sell_tax_pct": risk.get("sell_tax_pct"),
        "is_mintable": risk.get("is_mintable"),
        "is_open_source": risk.get("is_open_source"),
        "owner_renounced": risk.get("owner_renounced"),
        "holder_count": risk.get("holder_count"),
        "liquidity_health": health.get("liquidity_health", "UNKNOWN"),
        "lp_locked_pct": health.get("lp_locked_pct"),
        "lp_burned_pct": health.get("lp_burned_pct"),
        "top_unlocked_holder_pct": health.get("top_unlocked_holder_pct"),
        "risk_flags": risk_flags,
        "risk_flags_count": len(risk_flags),
        "checks_completed": checks_completed,
        "checks_total": 2,
        "data_sources": data_sources,
        "notice": _TOKEN_DIAGNOSTIC_NOTICE,
    }


def _normalize_futures_symbol(symbol: str) -> str:
    """예: "BTC" -> "BTCUSDT". 이미 USDT로 끝나면 그대로 둔다."""
    symbol = (symbol or "").upper().strip()
    if symbol.endswith("USDT"):
        return symbol
    return f"{symbol}USDT"


def _ms_epoch_to_timestamp_pair(ms) -> dict | None:
    """Bybit/바이낸스가 주는 밀리초 epoch 타임스탬프를 {utc, kst} 쌍으로 변환한다."""
    from datetime import datetime, timedelta, timezone

    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return None
    if not ms:
        return None
    kst_tz = timezone(timedelta(hours=9))
    dt_utc = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    dt_kst = dt_utc.astimezone(kst_tz)
    return {
        "utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kst": dt_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
    }


async def get_funding_rate(symbol: str) -> dict:
    """
    Bybit(1순위)/바이낸스(폴백) 무기한 선물 펀딩비 조회.
    GET /v1/derivatives/funding-rate가 사용한다 (main.py 참고).

    predicted_rate에 대한 정직한 설명: Bybit v5/바이낸스 둘 다 "지금 이 순간의
    펀딩비"와 별개로 "예측 펀딩비"를 따로 제공하지 않는다 - 현재 펀딩비 필드
    자체가 이미 다음 정산 시점(next_funding_time)에 적용될 요율이다. 그래서
    predicted_rate는 항상 funding_rate와 같은 값으로 채운다 (틀린 값을 지어내는
    것보다 정직한 선택).
    """
    normalized = _normalize_futures_symbol(symbol)
    base_notice = (
        "The funding rate is the rate that will apply at the next settlement "
        "(next_funding_time). Neither Bybit nor Binance provides a separate "
        "'predicted' field, so predicted_rate is always the same value as funding_rate."
    )

    try:
        data = await ds.get_bybit_funding_rate(normalized)
        funding_rate = float(data.get("fundingRate") or 0)
        next_funding_time = _ms_epoch_to_timestamp_pair(data.get("nextFundingTime"))
        funding_interval_hours = (
            int(data["fundingIntervalHour"]) if data.get("fundingIntervalHour") else None
        )
        data_source = "bybit"
        notice = base_notice
    except Exception as e:
        logger.warning("Bybit 펀딩비 조회 실패, 바이낸스로 폴백합니다: %s", e)
        try:
            data = await ds.get_binance_funding_rate(normalized)
            funding_rate = float(data.get("lastFundingRate") or 0)
            next_funding_time = _ms_epoch_to_timestamp_pair(data.get("nextFundingTime"))
            funding_interval_hours = None
            data_source = "binance"
            notice = (
                base_notice
                + " (Bybit lookup failed; using Binance fallback data - Binance may "
                "return a 451 block on this server's IP range, so this value does "
                "not always succeed either.)"
            )
        except Exception as e2:
            return {
                "generated_at": _timestamp_now(),
                "symbol": normalized,
                "data_source": "none",
                "notice": f"Both Bybit and Binance lookups failed: {e2}",
            }

    return {
        "generated_at": _timestamp_now(),
        "symbol": normalized,
        "funding_rate": funding_rate,
        "funding_rate_percentage": round(funding_rate * 100, 4),
        "predicted_rate": funding_rate,
        "next_funding_time": next_funding_time,
        "funding_interval_hours": funding_interval_hours,
        "data_source": data_source,
        "notice": notice,
    }


_FUNDING_APR_NOTICE = (
    "annualized_rate_pct is a flat extrapolation of the CURRENT funding rate "
    "(periods_per_year x current rate) - it assumes the rate stays constant, which "
    "funding rates rarely do over a full year. breakeven_days assumes a constant "
    "funding_collector_side position and a flat assumed_round_trip_cost_pct covering "
    "entry+exit trading fees on both the spot and perpetual legs - it excludes margin "
    "borrow cost, spot-perp basis risk, and perp liquidation risk. Re-verify with your "
    "actual exchange fee tier before sizing a real carry trade."
)

_DEFAULT_FUNDING_INTERVAL_HOURS = 8


async def get_funding_apr_matrix(
    symbol: str,
    assumed_round_trip_cost_pct: float = 0.2,
) -> dict:
    """
    기존 get_funding_rate() 결과에 연환산 APR과 캐리 트레이드(현물+반대 방향
    무기한선물 헤지) 손익분기일(breakeven_days)을 계산해서 얹는 순수 계산
    레이어 - 별도 외부 API 호출 없음. GET /v1/derivatives/funding-apr-matrix가
    사용한다 (main.py 참고).
    """
    underlying = await get_funding_rate(symbol)
    symbol_out = underlying.get("symbol", symbol.upper())
    funding_rate_percentage = underlying.get("funding_rate_percentage")
    funding_interval_hours = underlying.get("funding_interval_hours")
    data_source = underlying.get("data_source", "none")

    if funding_rate_percentage is None or data_source == "none":
        return {
            "generated_at": _timestamp_now(),
            "symbol": symbol_out,
            "funding_rate_percentage": None,
            "funding_interval_hours": funding_interval_hours,
            "periods_per_year": None,
            "annualized_rate_pct": None,
            "funding_collector_side": None,
            "daily_funding_income_pct": None,
            "assumed_round_trip_cost_pct": assumed_round_trip_cost_pct,
            "breakeven_days": None,
            "data_source": data_source,
            "notice": underlying.get("notice")
            or "Could not compute APR - underlying funding rate lookup failed.",
        }

    interval_hours = funding_interval_hours or _DEFAULT_FUNDING_INTERVAL_HOURS
    interval_assumed = funding_interval_hours is None
    periods_per_year = round((365 * 24) / interval_hours)
    periods_per_day = 24 / interval_hours

    annualized_rate_pct = round(funding_rate_percentage * periods_per_year, 4)
    daily_funding_income_pct = round(abs(funding_rate_percentage) * periods_per_day, 4)

    if funding_rate_percentage > 0:
        funding_collector_side = "SHORT"
    elif funding_rate_percentage < 0:
        funding_collector_side = "LONG"
    else:
        funding_collector_side = "NEUTRAL"

    breakeven_days = (
        round(assumed_round_trip_cost_pct / daily_funding_income_pct, 2)
        if daily_funding_income_pct > 0
        else None
    )

    notice = _FUNDING_APR_NOTICE
    if interval_assumed:
        notice += (
            " funding_interval_hours was unavailable from the underlying data source "
            f"(fallback exchange), so a standard {_DEFAULT_FUNDING_INTERVAL_HOURS}h "
            "settlement interval was assumed for this calculation."
        )

    return {
        "generated_at": _timestamp_now(),
        "symbol": symbol_out,
        "funding_rate_percentage": funding_rate_percentage,
        "funding_interval_hours": interval_hours,
        "periods_per_year": periods_per_year,
        "annualized_rate_pct": annualized_rate_pct,
        "funding_collector_side": funding_collector_side,
        "daily_funding_income_pct": daily_funding_income_pct,
        "assumed_round_trip_cost_pct": assumed_round_trip_cost_pct,
        "breakeven_days": breakeven_days,
        "data_source": data_source,
        "notice": notice,
    }


def _pick_most_liquid_pool(pools: list[dict]) -> dict | None:
    """토큰의 풀 목록 중 reserve_in_usd(합산 USD 유동성)가 가장 큰 풀을 고른다."""
    best = None
    best_liquidity = -1.0
    for pool in pools:
        attrs = pool.get("attributes") or {}
        try:
            liquidity = float(attrs.get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            liquidity = 0.0
        if liquidity > best_liquidity:
            best_liquidity = liquidity
            best = pool
    return best


_DEX_SLIPPAGE_NOTICE = (
    "Slippage is an approximation computed only from the pool's aggregate USD "
    "liquidity as reported by GeckoTerminal - it assumes the pool is a standard "
    "constant-product (x*y=k) AMM with the two tokens in a 50:50 ratio. Actual "
    "slippage can differ significantly for Uniswap v3-style concentrated-liquidity "
    "pools or stableswap pools - always re-verify with an on-chain quote before "
    "trading. slippage_tiers uses the same approximation at three fixed trade sizes "
    "regardless of the trade_size_usd you passed in; warning_level thresholds (LOW "
    "<1%, MEDIUM 1-3%, HIGH >3%) are AlphaPipeline's own heuristic, not an industry "
    "standard."
)

_SLIPPAGE_TIER_SIZES_USD = (1000.0, 5000.0, 10000.0)


def _compute_slippage_tiers(half_liquidity_usd: float) -> list[dict]:
    """고정 $1k/$5k/$10k 구간에 대해 동일한 constant-product 근사로 가격 충격을
    계산하고, 자체 휴리스틱(LOW<1%/MEDIUM<3%/HIGH>=3%) 경고 등급을 붙인다."""
    tiers = []
    for size in _SLIPPAGE_TIER_SIZES_USD:
        impact_pct = (size / (half_liquidity_usd + size)) * 100
        if impact_pct < 1:
            warning_level = "LOW"
        elif impact_pct < 3:
            warning_level = "MEDIUM"
        else:
            warning_level = "HIGH"
        tiers.append(
            {
                "trade_size_usd": size,
                "estimated_price_impact_pct": round(impact_pct, 4),
                "warning_level": warning_level,
            }
        )
    return tiers


async def get_dex_liquidity_slippage(
    network: str,
    trade_size_usd: float,
    pool_address: str | None = None,
    token_address: str | None = None,
) -> dict:
    """
    GeckoTerminal(무료, 키 불필요) 기반 DEX 유동성 + 예상 슬리피지 조회.
    GET /v1/dex/liquidity-slippage가 사용한다 (main.py 참고).

    정직하게 밝혀둘 한계: GeckoTerminal 무료 API는 풀의 "합산 USD 유동성"만
    주고 각 토큰별 실제 보유량(reserve)은 주지 않는다. 그래서 슬리피지는
    표준 Uniswap v2류 constant-product(x*y=k) 풀이 정확히 50:50 비율로
    구성되어 있다고 "가정"하고 근사 계산한다 - 이 가정은 항상 notice
    필드에 명시한다 (그럴듯하지만 틀릴 수 있는 값을 조용히 내보내지 않기 위함).
    """
    if not pool_address and not token_address:
        raise ValueError("pool_address 또는 token_address 중 하나는 반드시 필요합니다")

    resolved_pool_address = pool_address
    if not pool_address:
        pools = await ds.get_geckoterminal_pools_for_token(network, token_address)
        best_pool = _pick_most_liquid_pool(pools)
        if not best_pool:
            return {
                "generated_at": _timestamp_now(),
                "network": network,
                "pool_address": None,
                "token_address": token_address,
                "trade_size_usd": trade_size_usd,
                "price_impact_model": "constant_product_50_50_approximation",
                "slippage_tiers": None,
                "data_source": "none",
                "notice": f"No pool linked to token {token_address} on {network} was found on GeckoTerminal.",
            }
        pool_data = best_pool
        resolved_pool_address = (pool_data.get("attributes") or {}).get("address")
    else:
        pool_data = await ds.get_geckoterminal_pool(network, pool_address)

    attrs = pool_data.get("attributes") or {}
    try:
        liquidity_usd = float(attrs.get("reserve_in_usd") or 0)
    except (TypeError, ValueError):
        liquidity_usd = 0.0

    volume_24h_usd = None
    volume_obj = attrs.get("volume_usd") or {}
    if volume_obj.get("h24") is not None:
        try:
            volume_24h_usd = float(volume_obj["h24"])
        except (TypeError, ValueError):
            volume_24h_usd = None

    pool_name = attrs.get("name")

    if liquidity_usd <= 0:
        return {
            "generated_at": _timestamp_now(),
            "network": network,
            "pool_address": resolved_pool_address,
            "token_address": token_address,
            "pool_name": pool_name,
            "liquidity_usd": liquidity_usd or None,
            "volume_24h_usd": volume_24h_usd,
            "trade_size_usd": trade_size_usd,
            "estimated_slippage_pct": None,
            "price_impact_model": "constant_product_50_50_approximation",
            "slippage_tiers": None,
            "data_source": "geckoterminal",
            "notice": _DEX_SLIPPAGE_NOTICE + " (Could not compute slippage because this pool's liquidity data is unavailable.)",
        }

    half_liquidity_usd = liquidity_usd / 2
    estimated_slippage_pct = (trade_size_usd / (half_liquidity_usd + trade_size_usd)) * 100

    return {
        "generated_at": _timestamp_now(),
        "network": network,
        "pool_address": resolved_pool_address,
        "token_address": token_address,
        "pool_name": pool_name,
        "liquidity_usd": liquidity_usd,
        "volume_24h_usd": volume_24h_usd,
        "trade_size_usd": trade_size_usd,
        "estimated_slippage_pct": round(estimated_slippage_pct, 4),
        "price_impact_model": "constant_product_50_50_approximation",
        "slippage_tiers": _compute_slippage_tiers(half_liquidity_usd),
        "data_source": "geckoterminal",
        "notice": _DEX_SLIPPAGE_NOTICE,
    }


_ARB_SPREAD_NOTICE = (
    "CEX-side price is Coinbase spot (CoinGecko fallback), not a specific exchange "
    "orderbook - it does not reflect actual tradable depth on any single exchange. "
    "DEX-side price is read from GeckoTerminal's pool price fields. net_spread_pct "
    "only subtracts an assumed flat gas cost - it excludes CEX deposit/withdrawal "
    "availability, trading fees, and slippage beyond trade_size_usd. Re-verify with "
    "live quotes before executing a real trade."
)

# 체인별 표준 스왑 1회 가스비 추정치(USD). base가 기본이고, 그 외 체인은
# 보수적으로 더 높은 기본값을 쓴다 - 나중에 체인이 늘어나면 이 딕셔너리에
# 추가하기만 하면 된다 (다른 로직 변경 불필요).
_ASSUMED_GAS_COST_USD = {"base": 0.05}
_DEFAULT_GAS_COST_USD = 0.5


def _extract_dex_token_price_usd(pool_data: dict, token_address: str | None) -> float | None:
    """
    GeckoTerminal 풀 데이터에서 우리가 원하는 토큰 쪽의 USD 가격을 뽑아낸다.

    GeckoTerminal 풀 응답은 토큰을 base/quote 두 역할로 나눠서 각각의 가격을
    attributes.base_token_price_usd / quote_token_price_usd에 담아준다. 어느 쪽이
    우리가 찾는 token_address인지는 relationships.base_token/quote_token.data.id
    (형식: "{network}_{address}")를 target 주소와 대소문자 무시 비교해서 판별한다.
    token_address가 없을 때(pool_address로 직접 조회한 경우)는 base_token 가격을
    기본값으로 쓴다 - 어느 쪽이 우리가 원하는 토큰인지 알 방법이 없기 때문이다.
    """
    attrs = pool_data.get("attributes") or {}
    relationships = pool_data.get("relationships") or {}

    if token_address:
        target = token_address.lower()
        base_id = ((relationships.get("base_token") or {}).get("data") or {}).get("id", "") or ""
        quote_id = ((relationships.get("quote_token") or {}).get("data") or {}).get("id", "") or ""
        if target in base_id.lower():
            price = attrs.get("base_token_price_usd")
        elif target in quote_id.lower():
            price = attrs.get("quote_token_price_usd")
        else:
            price = attrs.get("base_token_price_usd")
    else:
        price = attrs.get("base_token_price_usd")

    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


async def get_arb_spread_matrix(
    symbol: str,
    network: str = "base",
    trade_size_usd: float = 1000.0,
    pool_address: str | None = None,
    token_address: str | None = None,
    min_spread_threshold_pct: float = 0.8,
) -> dict:
    """
    글로벌 기준가(Coinbase 현물, CoinGecko 폴백) vs DEX 풀 가격의 차익거래
    스프레드를 계산한다. GET /v1/arb/spread-matrix가 사용한다 (main.py 참고).

    dex_liquidity_slippage와 동일한 패턴으로 pool_address 또는 token_address
    중 하나만 받고, token_address만 주어지면 가장 유동성 큰 풀을 자동으로
    고른다(_pick_most_liquid_pool 재사용).

    정직하게 밝혀둘 한계: "CEX 가격"은 특정 거래소 오더북이 아니라 코인베이스
    현물가(폴백: CoinGecko)다 - get_binance_price_usdt라는 함수명과 달리 실제
    바이낸스를 호출하지 않는다(과거 바이낸스 지역차단(451) 이슈로 코인베이스로
    전환됨, app/data_sources.py 참고). 그래서 이 스프레드는 "실제로 특정
    거래소에 지금 이 가격에 넣을 수 있는 주문이 있다"는 보장이 아니라 참고용
    기준가 차이일 뿐이다 - notice 필드에 이 사실을 항상 명시한다.
    """
    if not pool_address and not token_address:
        raise ValueError("pool_address 또는 token_address 중 하나는 반드시 필요합니다")

    symbol_upper = symbol.upper().strip()

    try:
        cex_price_usd = await ds.get_binance_price_usdt(f"{symbol_upper}USDT")
    except Exception as e:
        return {
            "generated_at": _timestamp_now(),
            "symbol": symbol_upper,
            "network": network,
            "pool_address": pool_address,
            "status_message": f"Could not fetch CEX-side price for {symbol_upper}: {e}",
            "is_profitable": False,
            "trade_size_usd": trade_size_usd,
            "assumed_gas_cost_usd": _ASSUMED_GAS_COST_USD.get(network, _DEFAULT_GAS_COST_USD),
            "min_spread_threshold_pct": min_spread_threshold_pct,
            "data_source": "none",
            "notice": _ARB_SPREAD_NOTICE,
        }

    resolved_pool_address = pool_address
    if not pool_address:
        pools = await ds.get_geckoterminal_pools_for_token(network, token_address)
        best_pool = _pick_most_liquid_pool(pools)
        if not best_pool:
            return {
                "generated_at": _timestamp_now(),
                "symbol": symbol_upper,
                "network": network,
                "pool_address": None,
                "status_message": f"No DEX pool linked to token {token_address} on {network} was found on GeckoTerminal.",
                "is_profitable": False,
                "trade_size_usd": trade_size_usd,
                "assumed_gas_cost_usd": _ASSUMED_GAS_COST_USD.get(network, _DEFAULT_GAS_COST_USD),
                "min_spread_threshold_pct": min_spread_threshold_pct,
                "data_source": "none",
                "notice": _ARB_SPREAD_NOTICE,
            }
        pool_data = best_pool
        resolved_pool_address = (pool_data.get("attributes") or {}).get("address")
    else:
        pool_data = await ds.get_geckoterminal_pool(network, pool_address)

    dex_price_usd = _extract_dex_token_price_usd(pool_data, token_address)
    gas_cost_usd = _ASSUMED_GAS_COST_USD.get(network, _DEFAULT_GAS_COST_USD)

    if not dex_price_usd or not cex_price_usd or cex_price_usd <= 0:
        return {
            "generated_at": _timestamp_now(),
            "symbol": symbol_upper,
            "network": network,
            "pool_address": resolved_pool_address,
            "status_message": "Could not compute spread - DEX pool price data is unavailable.",
            "is_profitable": False,
            "cex_price_usd": cex_price_usd or None,
            "dex_price_usd": dex_price_usd,
            "trade_size_usd": trade_size_usd,
            "assumed_gas_cost_usd": gas_cost_usd,
            "min_spread_threshold_pct": min_spread_threshold_pct,
            "data_source": "geckoterminal" if dex_price_usd is None else "coinbase+geckoterminal",
            "notice": _ARB_SPREAD_NOTICE,
        }

    gross_spread_pct = ((cex_price_usd - dex_price_usd) / dex_price_usd) * 100
    direction = "DEX_TO_CEX" if gross_spread_pct >= 0 else "CEX_TO_DEX"
    gas_cost_pct = (gas_cost_usd / trade_size_usd) * 100 if trade_size_usd > 0 else 0.0
    net_spread_pct = abs(gross_spread_pct) - gas_cost_pct
    is_profitable = net_spread_pct >= min_spread_threshold_pct

    status_message = (
        f"Spread is {net_spread_pct:.2f}%, "
        f"{'meets' if is_profitable else 'below'} {min_spread_threshold_pct}% threshold "
        f"({'Profitable' if is_profitable else 'Unprofitable'})."
    )

    return {
        "generated_at": _timestamp_now(),
        "symbol": symbol_upper,
        "network": network,
        "pool_address": resolved_pool_address,
        "status_message": status_message,
        "is_profitable": is_profitable,
        "gross_spread_pct": round(gross_spread_pct, 4),
        "net_spread_pct": round(net_spread_pct, 4),
        "direction": direction,
        "cex_price_usd": round(cex_price_usd, 8),
        "dex_price_usd": round(dex_price_usd, 8),
        "trade_size_usd": trade_size_usd,
        "assumed_gas_cost_usd": gas_cost_usd,
        "min_spread_threshold_pct": min_spread_threshold_pct,
        "data_source": "coinbase+geckoterminal",
        "notice": _ARB_SPREAD_NOTICE,
    }


# 2026년 FOMC(연준 금리 결정) / CPI(미국 소비자물가지수) /
# NFP(미국 고용지표, Employment Situation) 정적 일정표.
# 출처: federalreserve.gov/monetarypolicy/fomccalendars.htm (FOMC),
#       bls.gov/schedule/news_release/cpi.htm (CPI),
#       bls.gov/schedule/news_release/empsit.htm (NFP/Employment Situation).
# 발표 시각: FOMC 성명서는 회의 마지막날 미동부 시간 오후 2시(ET),
# CPI/NFP는 미동부 시간 오전 8시 30분(ET)이며, 아래 utc_iso는 2026년
# 미국 서머타임(DST, 3월 8일~11월 1일) 적용 여부를 이미 반영해 UTC로
# 미리 계산해둔 값이다 (정적 데이터라서 실행 시점에 DST를 다시 계산하지 않음).
_MACRO_EVENTS_2026 = [
    {"event_name": "FOMC 금리 결정 (1월)", "event_type": "FOMC", "utc_iso": "2026-01-28T19:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates"], "description": "Federal Reserve interest rate decision and policy statement."},
    {"event_name": "NFP (2025년 12월 기준)", "event_type": "NFP", "utc_iso": "2026-01-09T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2025년 12월 기준)", "event_type": "CPI", "utc_iso": "2026-01-13T13:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 1월 기준)", "event_type": "NFP", "utc_iso": "2026-02-11T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 1월 기준)", "event_type": "CPI", "utc_iso": "2026-02-13T13:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 2월 기준)", "event_type": "NFP", "utc_iso": "2026-03-06T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 2월 기준)", "event_type": "CPI", "utc_iso": "2026-03-11T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (3월, SEP 포함)", "event_type": "FOMC", "utc_iso": "2026-03-18T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates", "sep"], "description": "Federal Reserve interest rate decision, policy statement, and Summary of Economic Projections (dot plot)."},
    {"event_name": "NFP (2026년 3월 기준)", "event_type": "NFP", "utc_iso": "2026-04-03T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 3월 기준)", "event_type": "CPI", "utc_iso": "2026-04-10T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (4월)", "event_type": "FOMC", "utc_iso": "2026-04-29T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates"], "description": "Federal Reserve interest rate decision and policy statement."},
    {"event_name": "NFP (2026년 4월 기준)", "event_type": "NFP", "utc_iso": "2026-05-08T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 4월 기준)", "event_type": "CPI", "utc_iso": "2026-05-12T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 5월 기준)", "event_type": "NFP", "utc_iso": "2026-06-05T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 5월 기준)", "event_type": "CPI", "utc_iso": "2026-06-10T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (6월, SEP 포함)", "event_type": "FOMC", "utc_iso": "2026-06-17T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates", "sep"], "description": "Federal Reserve interest rate decision, policy statement, and Summary of Economic Projections (dot plot)."},
    {"event_name": "NFP (2026년 6월 기준)", "event_type": "NFP", "utc_iso": "2026-07-02T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 6월 기준)", "event_type": "CPI", "utc_iso": "2026-07-14T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (7월)", "event_type": "FOMC", "utc_iso": "2026-07-29T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates"], "description": "Federal Reserve interest rate decision and policy statement."},
    {"event_name": "NFP (2026년 7월 기준)", "event_type": "NFP", "utc_iso": "2026-08-07T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 7월 기준)", "event_type": "CPI", "utc_iso": "2026-08-12T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 8월 기준)", "event_type": "NFP", "utc_iso": "2026-09-04T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 8월 기준)", "event_type": "CPI", "utc_iso": "2026-09-11T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (9월, SEP 포함)", "event_type": "FOMC", "utc_iso": "2026-09-16T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates", "sep"], "description": "Federal Reserve interest rate decision, policy statement, and Summary of Economic Projections (dot plot)."},
    {"event_name": "NFP (2026년 9월 기준)", "event_type": "NFP", "utc_iso": "2026-10-02T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 9월 기준)", "event_type": "CPI", "utc_iso": "2026-10-14T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (10월)", "event_type": "FOMC", "utc_iso": "2026-10-28T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates"], "description": "Federal Reserve interest rate decision and policy statement."},
    {"event_name": "NFP (2026년 10월 기준)", "event_type": "NFP", "utc_iso": "2026-11-06T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 10월 기준)", "event_type": "CPI", "utc_iso": "2026-11-10T13:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 11월 기준)", "event_type": "NFP", "utc_iso": "2026-12-04T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "FOMC 금리 결정 (12월, SEP 포함)", "event_type": "FOMC", "utc_iso": "2026-12-09T19:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates", "sep"], "description": "Federal Reserve interest rate decision, policy statement, and Summary of Economic Projections (dot plot)."},
    {"event_name": "CPI (2026년 11월 기준)", "event_type": "CPI", "utc_iso": "2026-12-10T13:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
]

_MACRO_CALENDAR_NOTICE = (
    "This calendar statically embeds the 2026 FOMC rate-decision, US CPI, and US "
    "employment (NFP) schedule based on official Fed/BLS releases - it makes no "
    "live external API calls. The Fed/BLS may still change these dates, and 2027 "
    "dates are not yet included, so re-verify with the official sources "
    "(federalreserve.gov, bls.gov) before any important decision."
)


def _utc_iso_to_timestamp_pair(utc_iso: str) -> dict:
    '"...Z" 형식의 UTC ISO 문자열을 {utc, kst} 쌍으로 변환한다.'
    from datetime import datetime, timedelta, timezone

    dt_utc = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    kst_tz = timezone(timedelta(hours=9))
    dt_kst = dt_utc.astimezone(kst_tz)
    return {
        "utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kst": dt_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
    }


async def get_macro_calendar_dday() -> dict:
    """
    2026년 FOMC/CPI/NFP 일정을 정적으로 내장해 가장 가까운
    이벤트까지의 D-Day를 계산한다. 외부 API 호출 없음.
    GET /v1/calendar/macro-dday가 사용한다 (main.py 참고).
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    parsed = [
        (datetime.fromisoformat(ev["utc_iso"].replace("Z", "+00:00")), ev)
        for ev in _MACRO_EVENTS_2026
    ]
    upcoming_all = sorted((item for item in parsed if item[0] >= now), key=lambda x: x[0])

    if not upcoming_all:
        return {
            "generated_at": _timestamp_now(),
            "event_name": None,
            "event_type": None,
            "event_datetime": None,
            "d_day": None,
            "time_remaining": None,
            "impact_level": None,
            "tags": [],
            "description": None,
            "upcoming_events": [],
            "data_source": "static_2026_macro_calendar",
            "notice": _MACRO_CALENDAR_NOTICE
            + " All embedded 2026 events have passed - this calendar needs to be "
            "updated with next year's schedule.",
        }

    nearest_dt, nearest_ev = upcoming_all[0]
    total_seconds = max((nearest_dt - now).total_seconds(), 0)
    days_remaining = int(total_seconds // 86400)
    hours_remaining = int((total_seconds % 86400) // 3600)
    minutes_remaining = int((total_seconds % 3600) // 60)
    d_day = (nearest_dt.date() - now.date()).days

    upcoming_brief = [
        {
            "event_name": ev["event_name"],
            "event_type": ev["event_type"],
            "event_datetime": _utc_iso_to_timestamp_pair(ev["utc_iso"]),
            "impact_level": ev["impact_level"],
            "tags": ev["tags"],
        }
        for _, ev in upcoming_all[1:4]
    ]

    return {
        "generated_at": _timestamp_now(),
        "event_name": nearest_ev["event_name"],
        "event_type": nearest_ev["event_type"],
        "event_datetime": _utc_iso_to_timestamp_pair(nearest_ev["utc_iso"]),
        "d_day": d_day,
        "time_remaining": {
            "days": days_remaining,
            "hours": hours_remaining,
            "minutes": minutes_remaining,
        },
        "impact_level": nearest_ev["impact_level"],
        "tags": nearest_ev["tags"],
        "description": nearest_ev["description"],
        "upcoming_events": upcoming_brief,
        "data_source": "static_2026_macro_calendar",
        "notice": _MACRO_CALENDAR_NOTICE,
    }


# ============================================================================
# prediction.neg_risk_arbitrage / prediction.exit_capacity_audit (2026-09) -
# 예측시장(Polymarket) 확장 Wave 1. 상세 기획/리스크 검토는 프로젝트 노트
# 업데이트 32/33/34 참고. Kalshi는 Data ToS 위반 리스크로 완전히 배제했고,
# Polymarket 온체인 neg-risk 어댑터 메커니즘(YES 바스켓 합이 항상 $1로
# 정산)에 기반한 결정론적 계산만 한다 - LLM 판단/경험적 추정 없음.
#
# 2026-09 배포 전 외부 리뷰에서 지적받아 반영한 것 (v1 -> v2):
#   - 단순 top-of-book 가격 합만 보면 실제로는 못 사는 "가짜 차익"이 나올 수
#     있어서, 각 레그 오더북을 max_slippage_pct 이내로 걸어 실제 체결 가능한
#     최소 공통 수량(basket capacity)까지 계산한다.
#   - 비용 차감을 레그당 고정 $ 대신 get_funding_apr_matrix와 같은 방식의
#     퍼센트 파라미터(assumed_round_trip_cost_pct)로 통일했다.
#   - 오더북 조회를 asyncio.gather로 병렬화하고 짧은 TTL 캐시를 적용해
#     레이트리밋/지연 위험을 줄였다(app/data_sources.py 참고).
# ============================================================================

_NEG_RISK_ARBITRAGE_NOTICE = (
    "*_capacity_shares is how many full baskets (1 share of every outcome) you "
    "could execute right now within max_slippage_pct of each leg's best price - "
    "arbitrage_viable=false with a positive edge usually means the edge is real "
    "but too thin to size meaningfully. Cross-check the specific leg you intend "
    "to trade with prediction.exit_capacity_audit before sizing a real position."
)

_EXIT_CAPACITY_AUDIT_NOTICE = (
    "executable=false means the current book cannot fully fill this size - "
    "max_executable_shares is how much you could get out of (or into) right now "
    "at the prices already walked through above. This is a live, point-in-time "
    "snapshot, not an average or historical liquidity figure."
)


def _parse_book_levels(raw_levels: list[dict], reverse: bool) -> list[dict]:
    """CLOB 오더북의 bids/asks 배열(price/size가 문자열)을 정렬된 float 리스트로."""
    return sorted(
        ({"price": float(l["price"]), "size": float(l["size"])} for l in raw_levels),
        key=lambda l: l["price"],
        reverse=reverse,
    )


def _leg_capacity_shares(levels: list[dict], best_price: float, max_slippage_pct: float, side: str) -> float:
    """
    한 레그(outcome)의 오더북에서, best_price로부터 max_slippage_pct 이내인
    구간에 걸려있는 수량 합계 - 이 레그를 실제로 얼마나 살(팔) 수 있는지.
    """
    if not levels or best_price is None:
        return 0.0
    if side == "ask":
        cutoff = best_price * (1 + max_slippage_pct / 100)
        return sum(l["size"] for l in levels if l["price"] <= cutoff)
    cutoff = best_price * (1 - max_slippage_pct / 100)
    return sum(l["size"] for l in levels if l["price"] >= cutoff)


async def get_neg_risk_arbitrage(
    event_slug: str,
    assumed_round_trip_cost_pct: float = 1.5,
    max_slippage_pct: float = 1.0,
    min_net_edge_pct: float = 1.0,
) -> dict:
    """
    Polymarket "neg-risk"(상호배타적 다중 결과) 이벤트의 바스켓 차익을 계산한다.
    neg-risk 그룹은 YES 바스켓 합이 항상 $1로 정산되는 온체인 어댑터 메커니즘을
    갖고 있어서, best-ask 합이 $1 미만(혹은 best-bid 합이 $1 초과)이면 그 차이가
    거의 무위험 차익이다 - 단, 각 레그의 실제 체결 가능 수량(유동성 병목)까지
    함께 계산해서 top-of-book만 보고 못 사는 "가짜 차익"을 걸러낸다.
    GET /v1/prediction/neg-risk-arbitrage가 사용한다 (main.py 참고).
    """
    if not event_slug or not event_slug.strip():
        raise ValueError("event_slug는 필수입니다 (예: 'presidential-election-winner-2028')")
    event_slug = event_slug.strip()

    event = await ds.get_polymarket_event(event_slug)
    markets = [m for m in event.get("markets", []) if m.get("neg_risk")]
    if len(markets) < 2:
        raise ValueError(
            f"이벤트 '{event_slug}'에는 neg-risk 상호배타 그룹이 없습니다 "
            "(바스켓 차익 계산에는 2개 이상의 상호배타 마켓이 필요합니다)"
        )

    yes_token_ids: list[str] = []
    for m in markets:
        tokens = m.get("tokens", [])
        yes_token = next((t for t in tokens if str(t.get("outcome", "")).lower() == "yes"), None)
        if yes_token and yes_token.get("token_id"):
            yes_token_ids.append(str(yes_token["token_id"]))
    if len(yes_token_ids) < 2:
        raise ValueError(f"이벤트 '{event_slug}'의 YES 토큰 ID를 찾지 못했습니다")

    books = await asyncio.gather(*(ds.get_polymarket_order_book(tid) for tid in yes_token_ids))

    n = len(yes_token_ids)
    ask_levels_per_leg: list[list[dict]] = []
    bid_levels_per_leg: list[list[dict]] = []
    best_asks: list[float] = []
    best_bids: list[float] = []
    for book in books:
        asks = _parse_book_levels(book.get("asks") or [], reverse=False)
        bids = _parse_book_levels(book.get("bids") or [], reverse=True)
        ask_levels_per_leg.append(asks)
        bid_levels_per_leg.append(bids)
        if asks:
            best_asks.append(asks[0]["price"])
        if bids:
            best_bids.append(bids[0]["price"])

    basket_ask_sum = sum(best_asks) if len(best_asks) == n else None
    basket_bid_sum = sum(best_bids) if len(best_bids) == n else None

    buy_gross_edge = (1.0 - basket_ask_sum) if basket_ask_sum is not None else None
    sell_gross_edge = (basket_bid_sum - 1.0) if basket_bid_sum is not None else None

    cost_frac = assumed_round_trip_cost_pct / 100
    buy_net_edge = (buy_gross_edge - cost_frac) if buy_gross_edge is not None else None
    sell_net_edge = (sell_gross_edge - cost_frac) if sell_gross_edge is not None else None

    buy_basket_capacity_shares = 0.0
    if len(best_asks) == n:
        leg_caps = [
            _leg_capacity_shares(levels, best, max_slippage_pct, "ask")
            for levels, best in zip(ask_levels_per_leg, best_asks)
        ]
        buy_basket_capacity_shares = min(leg_caps) if leg_caps else 0.0

    sell_basket_capacity_shares = 0.0
    if len(best_bids) == n:
        leg_caps = [
            _leg_capacity_shares(levels, best, max_slippage_pct, "bid")
            for levels, best in zip(bid_levels_per_leg, best_bids)
        ]
        sell_basket_capacity_shares = min(leg_caps) if leg_caps else 0.0

    opportunity = "none"
    arbitrage_viable = False
    if (
        buy_net_edge is not None
        and buy_net_edge * 100 >= min_net_edge_pct
        and buy_basket_capacity_shares > 0
        and (sell_net_edge is None or buy_net_edge >= sell_net_edge)
    ):
        opportunity = "buy_basket"
        arbitrage_viable = True
    elif (
        sell_net_edge is not None
        and sell_net_edge * 100 >= min_net_edge_pct
        and sell_basket_capacity_shares > 0
    ):
        opportunity = "sell_basket"
        arbitrage_viable = True

    return {
        "generated_at": _timestamp_now(),
        "event_slug": event_slug,
        "num_outcomes": n,
        "basket_ask_sum": round(basket_ask_sum, 4) if basket_ask_sum is not None else None,
        "basket_bid_sum": round(basket_bid_sum, 4) if basket_bid_sum is not None else None,
        "buy_basket_gross_edge_usd": round(buy_gross_edge, 4) if buy_gross_edge is not None else None,
        "sell_basket_gross_edge_usd": round(sell_gross_edge, 4) if sell_gross_edge is not None else None,
        "assumed_round_trip_cost_pct": assumed_round_trip_cost_pct,
        "buy_basket_net_edge_usd": round(buy_net_edge, 4) if buy_net_edge is not None else None,
        "sell_basket_net_edge_usd": round(sell_net_edge, 4) if sell_net_edge is not None else None,
        "buy_basket_capacity_shares": round(buy_basket_capacity_shares, 4),
        "sell_basket_capacity_shares": round(sell_basket_capacity_shares, 4),
        "buy_basket_capacity_notional_usd": (
            round(buy_basket_capacity_shares * basket_ask_sum, 2) if basket_ask_sum is not None else None
        ),
        "sell_basket_capacity_notional_usd": (
            round(sell_basket_capacity_shares * basket_bid_sum, 2) if basket_bid_sum is not None else None
        ),
        "opportunity": opportunity,
        "arbitrage_viable": arbitrage_viable,
        "data_source": "polymarket-gamma+clob",
        "notice": _NEG_RISK_ARBITRAGE_NOTICE,
    }


async def get_exit_capacity_audit(
    position_size_shares: float,
    token_id: str | None = None,
    market_slug: str | None = None,
    outcome: str = "yes",
    side: str = "sell",
) -> dict:
    """
    Polymarket 특정 outcome의 실시간 오더북을 걷어서, 지정한 수량을 지금 당장
    실제로 체결할 수 있는지, 평균 체결가/가격충격은 얼마인지 계산한다.
    token_id를 몰라도 market_slug(+outcome)만 주면 Gamma API로 자동 해석한다 -
    단 정확한 slug만 지원하고 keyword 검색은 하지 않는다(애매한 매칭으로 엉뚱한
    마켓을 조용히 골라버리는 게 에러보다 나쁘다고 판단, 2026-09 배포 전 리뷰 반영).
    GET /v1/prediction/exit-capacity-audit가 사용한다 (main.py 참고).
    """
    if position_size_shares is None or position_size_shares <= 0:
        raise ValueError("position_size_shares는 0보다 큰 값이어야 합니다")
    if not token_id and not market_slug:
        raise ValueError("token_id 또는 market_slug(정확한 Polymarket 마켓 slug) 중 하나는 필요합니다")

    resolved_token_id = str(token_id).strip() if token_id else None
    if not resolved_token_id:
        market = await ds.get_polymarket_market(market_slug.strip())
        tokens = market.get("tokens", [])
        match = next((t for t in tokens if str(t.get("outcome", "")).lower() == outcome), None)
        if not match or not match.get("token_id"):
            raise ValueError(f"마켓 slug '{market_slug}'에서 '{outcome}' 토큰을 찾지 못했습니다")
        resolved_token_id = str(match["token_id"])

    book = await ds.get_polymarket_order_book(resolved_token_id)
    levels_raw = (book.get("bids") if side == "sell" else book.get("asks")) or []
    levels = _parse_book_levels(levels_raw, reverse=(side == "sell"))

    if not levels:
        return {
            "generated_at": _timestamp_now(),
            "token_id": resolved_token_id,
            "market_slug": market_slug,
            "side": side,
            "position_size_shares": position_size_shares,
            "executable": False,
            "best_quote": None,
            "avg_exit_price": None,
            "price_impact_pct": None,
            "max_executable_shares": 0.0,
            "data_source": "polymarket-clob",
            "notice": _EXIT_CAPACITY_AUDIT_NOTICE
            + f" No {'bids' if side == 'sell' else 'asks'} on the book right now - position is currently stuck.",
        }

    best_quote = levels[0]["price"]
    remaining = position_size_shares
    filled_notional = 0.0
    filled_shares = 0.0
    for level in levels:
        if remaining <= 0:
            break
        take = min(remaining, level["size"])
        filled_notional += take * level["price"]
        filled_shares += take
        remaining -= take

    executable = remaining <= 0
    avg_price = (filled_notional / filled_shares) if filled_shares > 0 else None
    impact_pct = (
        ((best_quote - avg_price) / best_quote * 100) if side == "sell" and avg_price is not None
        else ((avg_price - best_quote) / best_quote * 100) if avg_price is not None
        else None
    )

    return {
        "generated_at": _timestamp_now(),
        "token_id": resolved_token_id,
        "market_slug": market_slug,
        "side": side,
        "position_size_shares": position_size_shares,
        "executable": executable,
        "best_quote": round(best_quote, 4),
        "avg_exit_price": round(avg_price, 4) if avg_price is not None else None,
        "price_impact_pct": round(impact_pct, 3) if impact_pct is not None else None,
        "max_executable_shares": round(filled_shares, 4),
        "data_source": "polymarket-clob",
        "notice": _EXIT_CAPACITY_AUDIT_NOTICE,
    }
