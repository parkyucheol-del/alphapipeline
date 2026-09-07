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
