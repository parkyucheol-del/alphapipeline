"""
1회성 통합 패치 스크립트: GET /v1/derivatives/funding-rate 신규 엔드포인트 추가
(Bybit v5 1순위, 바이낸스 USDT-M 선물 폴백).

전제: app/payment.py, app/schemas.py, main.py, app/config.py가 이미 이전 패치
(patch_sablier_goplus.py)로 token-risk까지 반영된 상태여야 합니다. 아직 그 패치를
안 돌렸다면 먼저 그것부터 적용해주세요.

건드리는 파일 6개: app/data_sources.py, app/config.py, app/schemas.py,
app/logic.py, app/payment.py, main.py

동작 방식: 모든 파일에 대해 먼저 "이 패치를 적용할 수 있는가"를 전부 확인하고
(검증 단계), 하나라도 실패하면 아무 파일도 건드리지 않고 종료한다(전부 적용
아니면 전부 취소).

실행: 레포 루트(app 폴더가 보이는 위치)에서 `python patch_funding_rate.py`
적용 후 `git diff`로 6개 파일 변경사항을 꼭 눈으로 확인할 것.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(".")
FILES = {
    "data_sources": ROOT / "app" / "data_sources.py",
    "config": ROOT / "app" / "config.py",
    "schemas": ROOT / "app" / "schemas.py",
    "logic": ROOT / "app" / "logic.py",
    "payment": ROOT / "app" / "payment.py",
    "main": ROOT / "main.py",
}


def fail(msg: str) -> None:
    sys.exit(f"\n중단: {msg}\n(아무 파일도 수정되지 않았습니다.)")


def read(key: str) -> str:
    path = FILES[key]
    if not path.exists():
        fail(f"{path}를 찾을 수 없습니다 - 레포 루트에서 실행해주세요.")
    return path.read_text(encoding="utf-8")


def must_replace(text: str, old: str, new: str, expected: int, where: str) -> str:
    count = text.count(old)
    if count != expected:
        fail(
            f"[{where}] 예상한 패턴을 찾지 못했습니다 (기대 {expected}회, 실제 {count}회).\n"
            f"찾던 텍스트:\n{old}\n\n"
            f"(이전 패치(patch_sablier_goplus.py)가 먼저 적용되어 있는지 확인해주세요.)"
        )
    return text.replace(old, new)


# ---------------------------------------------------------------------------
# 1) app/data_sources.py: Bybit(1순위)/바이낸스(폴백) 펀딩비 조회 함수 추가
# ---------------------------------------------------------------------------
ds_src = read("data_sources")

if "get_bybit_funding_rate" in ds_src:
    fail("data_sources.py: get_bybit_funding_rate가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

DS_NEW_FUNCTIONS = '''


BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"
BINANCE_PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"


async def get_bybit_funding_rate(symbol: str) -> dict:
    """
    Bybit v5 공개 티커 API에서 무기한 선물 펀딩비를 조회한다 (인증 불필요).
    category=linear는 USDT 마진 무기한 선물(BTCUSDT 등) 전용이다.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            BYBIT_TICKERS_URL, params={"category": "linear", "symbol": symbol}
        )
        r.raise_for_status()
        body = r.json()
        if body.get("retCode") != 0:
            raise RuntimeError(f"Bybit 응답 오류: {body.get('retMsg')}")
        items = (body.get("result") or {}).get("list") or []
        if not items:
            raise ValueError(f"Bybit에서 {symbol} 심볼을 찾지 못했습니다")
        return items[0]


async def get_binance_funding_rate(symbol: str) -> dict:
    """
    바이낸스 USDT-M 선물 premiumIndex 폴백 조회. 인증은 불필요하지만, 이 프로젝트가
    호스팅된 IP 대역에서 451(지역 차단)이 날 가능성이 있어 어디까지나 폴백이다
    (위 상단 주석의 바이낸스 지역차단/IP밴 이력 참고).
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(BINANCE_PREMIUM_INDEX_URL, params={"symbol": symbol})
        r.raise_for_status()
        return r.json()
'''
ds_src = ds_src.rstrip("\n") + "\n" + DS_NEW_FUNCTIONS.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 2) app/config.py: PRICE_FUNDING_RATE_USDC 추가 (기존 PRICE_DUMP_RISK_USDC 줄 스타일 복제)
# ---------------------------------------------------------------------------
cfg_src = read("config")

if "PRICE_FUNDING_RATE_USDC" in cfg_src:
    fail("config.py: PRICE_FUNDING_RATE_USDC가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

cfg_matches = re.findall(r"^[ \t]*.*PRICE_DUMP_RISK_USDC.*$", cfg_src, re.MULTILINE)
if len(cfg_matches) != 1:
    fail(
        f"config.py: PRICE_DUMP_RISK_USDC 줄을 정확히 1개 찾아야 하는데 {len(cfg_matches)}개 찾았습니다."
    )
cfg_old_line = cfg_matches[0]
if "0.03" not in cfg_old_line:
    fail(f"config.py: PRICE_DUMP_RISK_USDC 줄에서 기본값 0.03을 찾지 못했습니다:\n{cfg_old_line}")
cfg_new_line = cfg_old_line.replace("PRICE_DUMP_RISK_USDC", "PRICE_FUNDING_RATE_USDC").replace("0.03", "0.01")
cfg_insert_at = cfg_src.index(cfg_old_line) + len(cfg_old_line)
cfg_src = cfg_src[:cfg_insert_at] + "\n" + cfg_new_line + cfg_src[cfg_insert_at:]


# ---------------------------------------------------------------------------
# 3) app/schemas.py: FundingRateResponse + FUNDING_RATE_EXAMPLE 추가 (파일 맨 끝에 append)
# ---------------------------------------------------------------------------
schemas_src = read("schemas")

if "class FundingRateResponse" in schemas_src:
    fail("schemas.py: FundingRateResponse가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

SCHEMAS_NEW = '''


class FundingRateResponse(BaseModel):
    generated_at: TimestampPair
    symbol: str
    funding_rate: float | None = None
    funding_rate_percentage: float | None = None
    predicted_rate: float | None = None
    next_funding_time: TimestampPair | None = None
    funding_interval_hours: int | None = None
    data_source: str
    notice: str | None = None


FUNDING_RATE_EXAMPLE = {
    "generated_at": {"utc": "2026-09-04T12:00:00Z", "kst": "2026-09-04 21:00:00 KST"},
    "symbol": "BTCUSDT",
    "funding_rate": 0.0001,
    "funding_rate_percentage": 0.01,
    "predicted_rate": 0.0001,
    "next_funding_time": {"utc": "2026-09-04T16:00:00Z", "kst": "2026-09-05 01:00:00 KST"},
    "funding_interval_hours": 8,
    "data_source": "bybit",
    "notice": (
        "펀딩비는 다음 정산 시점(next_funding_time)에 적용될 예정 요율입니다. "
        "Bybit/바이낸스 둘 다 이와 별개의 '예측' 필드를 제공하지 않으므로 "
        "predicted_rate는 funding_rate와 동일한 값입니다."
    ),
}
'''
schemas_src = schemas_src.rstrip("\n") + "\n" + SCHEMAS_NEW.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 4) app/logic.py: get_funding_rate + 헬퍼 추가 (파일 맨 끝에 append)
# ---------------------------------------------------------------------------
logic_src = read("logic")

if "def get_funding_rate" in logic_src:
    fail("logic.py: get_funding_rate가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

LOGIC_NEW = '''


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
        "펀딩비는 다음 정산 시점(next_funding_time)에 적용될 예정 요율입니다. "
        "Bybit/바이낸스 둘 다 이와 별개의 '예측' 필드를 제공하지 않으므로 "
        "predicted_rate는 funding_rate와 동일한 값입니다."
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
                + " (Bybit 조회 실패로 바이낸스 폴백 데이터를 사용했습니다 - 이 서버의 IP 대역에서 "
                "바이낸스가 451로 차단될 수 있어 이 값도 항상 성공하지는 않습니다.)"
            )
        except Exception as e2:
            return {
                "generated_at": _timestamp_now(),
                "symbol": normalized,
                "data_source": "none",
                "notice": f"Bybit/바이낸스 둘 다 조회 실패: {e2}",
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
'''
logic_src = logic_src.rstrip("\n") + "\n" + LOGIC_NEW.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 5) app/payment.py: import 확장 + funding_rate_option + 신규 라우트
# ---------------------------------------------------------------------------
pay_src = read("payment")

PAY_OLD_IMPORT = """from app.schemas import (
    DUMP_RISK_EXAMPLE,
    KIMCHI_ALERT_EXAMPLE,
    MARKDOWN_EXAMPLE,
    TOKEN_RISK_EXAMPLE,
    DumpRiskResponse,
    KimchiAlertResponse,
    MarkdownResponse,
    TokenRiskResponse,
)"""
PAY_NEW_IMPORT = """from app.schemas import (
    DUMP_RISK_EXAMPLE,
    FUNDING_RATE_EXAMPLE,
    KIMCHI_ALERT_EXAMPLE,
    MARKDOWN_EXAMPLE,
    TOKEN_RISK_EXAMPLE,
    DumpRiskResponse,
    FundingRateResponse,
    KimchiAlertResponse,
    MarkdownResponse,
    TokenRiskResponse,
)"""
pay_src = must_replace(pay_src, PAY_OLD_IMPORT, PAY_NEW_IMPORT, 1, "payment.py: app.schemas import 블록")

PAY_OLD_OPTION = "    token_risk_option = _payment_option(settings.PRICE_TOKEN_RISK_USDC)"
PAY_NEW_OPTION = PAY_OLD_OPTION + "\n    funding_rate_option = _payment_option(settings.PRICE_FUNDING_RATE_USDC)"
pay_src = must_replace(pay_src, PAY_OLD_OPTION, PAY_NEW_OPTION, 1, "payment.py: token_risk_option 줄")

PAY_OLD_ROUTES_TAIL = '''            service_name="AlphaPipeline Token Risk Scanner",
            tags=["crypto", "security", "honeypot", "token-risk"],
        ),
    }'''
PAY_NEW_ROUTES_TAIL = '''            service_name="AlphaPipeline Token Risk Scanner",
            tags=["crypto", "security", "honeypot", "token-risk"],
        ),
        "GET /v1/derivatives/funding-rate": _make_route_config(
            accepts=[funding_rate_option],
            mime_type="application/json",
            description=(
                "Bybit (primary) / Binance (fallback) perpetual futures funding rate - "
                "the key signal for long/short crowding that traders use to time or hedge "
                "positions before the next funding settlement."
            ),
            resource=_resource_url("/v1/derivatives/funding-rate"),
            extensions=_bazaar_extension(
                input_example={"symbol": "BTC"},
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Ticker symbol, e.g. BTC, ETH, or BTCUSDT.",
                        }
                    },
                    "required": ["symbol"],
                },
                output_example=FUNDING_RATE_EXAMPLE,
                output_schema=_inline_schema_defs(FundingRateResponse.model_json_schema()),
            ),
            service_name="AlphaPipeline Funding Rate",
            tags=["crypto", "derivatives", "funding-rate"],
        ),
    }'''
pay_src = must_replace(pay_src, PAY_OLD_ROUTES_TAIL, PAY_NEW_ROUTES_TAIL, 1, "payment.py: routes 딕셔너리 끝부분")


# ---------------------------------------------------------------------------
# 6) main.py: import 확장 + 루트 메타데이터 확장 + 신규 엔드포인트
# ---------------------------------------------------------------------------
main_src = read("main")

MAIN_OLD_LOGIC_IMPORT = "from app.logic import get_dump_risk, get_kimchi_alert, get_token_risk, refresh_unlock_cache"
MAIN_NEW_LOGIC_IMPORT = (
    "from app.logic import get_dump_risk, get_funding_rate, get_kimchi_alert, get_token_risk, refresh_unlock_cache"
)
main_src = must_replace(main_src, MAIN_OLD_LOGIC_IMPORT, MAIN_NEW_LOGIC_IMPORT, 1, "main.py: app.logic import 줄")

MAIN_OLD_SCHEMAS_IMPORT = (
    "from app.schemas import DumpRiskResponse, ErrorResponse, KimchiAlertResponse, MarkdownResponse, "
    "TokenRiskResponse"
)
MAIN_NEW_SCHEMAS_IMPORT = (
    "from app.schemas import DumpRiskResponse, ErrorResponse, FundingRateResponse, KimchiAlertResponse, "
    "MarkdownResponse, TokenRiskResponse"
)
main_src = must_replace(main_src, MAIN_OLD_SCHEMAS_IMPORT, MAIN_NEW_SCHEMAS_IMPORT, 1, "main.py: app.schemas import 줄")

MAIN_OLD_PRICE_DICT = '''        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            "/v1/unlocks/dump-risk": settings.PRICE_DUMP_RISK_USDC,
            "/v1/security/token-risk": settings.PRICE_TOKEN_RISK_USDC,
        },'''
MAIN_NEW_PRICE_DICT = '''        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            "/v1/unlocks/dump-risk": settings.PRICE_DUMP_RISK_USDC,
            "/v1/security/token-risk": settings.PRICE_TOKEN_RISK_USDC,
            "/v1/derivatives/funding-rate": settings.PRICE_FUNDING_RATE_USDC,
        },'''
main_src = must_replace(main_src, MAIN_OLD_PRICE_DICT, MAIN_NEW_PRICE_DICT, 1, "main.py: price_per_call_usdc 딕셔너리")

MAIN_OLD_ENDPOINTS_LIST = '''        "endpoints": [
            "/v1/unlocks/dump-risk",
            "/v1/market/kimchi-alert",
            "/v1/tools/ai-markdown",
            "/v1/security/token-risk",
        ],'''
MAIN_NEW_ENDPOINTS_LIST = '''        "endpoints": [
            "/v1/unlocks/dump-risk",
            "/v1/market/kimchi-alert",
            "/v1/tools/ai-markdown",
            "/v1/security/token-risk",
            "/v1/derivatives/funding-rate",
        ],'''
main_src = must_replace(main_src, MAIN_OLD_ENDPOINTS_LIST, MAIN_NEW_ENDPOINTS_LIST, 1, "main.py: endpoints 리스트")

MAIN_OLD_MAIN_GUARD = '''async def token_risk_endpoint(
    chain_id: int = Query(..., description="EVM chain id, e.g. 8453 for Base"),
    contract_address: str = Query(..., description="Token contract address (0x...)"),
):
    try:
        data = await get_token_risk(chain_id, contract_address)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("token-risk 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)'''
MAIN_NEW_ENDPOINT_PLUS_GUARD = '''async def token_risk_endpoint(
    chain_id: int = Query(..., description="EVM chain id, e.g. 8453 for Base"),
    contract_address: str = Query(..., description="Token contract address (0x...)"),
):
    try:
        data = await get_token_risk(chain_id, contract_address)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("token-risk 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/derivatives/funding-rate",
    tags=["market"],
    summary="Get perpetual futures funding rate (Bybit primary, Binance fallback)",
    description=(
        "Use this endpoint when you need to gauge long/short crowding in perpetual "
        "futures before entering or hedging a position, or when asked about funding "
        "rate arbitrage / carry trade opportunities. Returns the current funding rate "
        "(as a decimal and as a percentage), the timestamp of the next funding "
        "settlement, and the funding interval in hours when available. Data source is "
        "Bybit (primary), falling back to Binance USDT-M futures if Bybit is "
        "unavailable - check data_source and notice to see which was used and read the "
        "notice for why predicted_rate equals funding_rate (neither exchange exposes a "
        "separate forecast field). Input: required `symbol` query parameter (e.g. BTC, "
        "ETH, or BTCUSDT - non-USDT-suffixed symbols are normalized to USDT pairs)."
    ),
    responses={
        200: {"model": FundingRateResponse, "description": "무기한 선물 펀딩비 데이터"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "업스트림(Bybit/바이낸스) 오류"},
    },
)
async def funding_rate_endpoint(symbol: str = Query(..., description="e.g. BTC, ETH, or BTCUSDT")):
    try:
        data = await get_funding_rate(symbol)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("funding-rate 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)'''
main_src = must_replace(main_src, MAIN_OLD_MAIN_GUARD, MAIN_NEW_ENDPOINT_PLUS_GUARD, 1, "main.py: __main__ 가드(신규 엔드포인트 삽입 위치)")


# ---------------------------------------------------------------------------
# 전부 검증 통과 - 이제 실제로 파일에 쓴다.
# ---------------------------------------------------------------------------
FILES["data_sources"].write_text(ds_src, encoding="utf-8", newline="\n")
FILES["config"].write_text(cfg_src, encoding="utf-8", newline="\n")
FILES["schemas"].write_text(schemas_src, encoding="utf-8", newline="\n")
FILES["logic"].write_text(logic_src, encoding="utf-8", newline="\n")
FILES["payment"].write_text(pay_src, encoding="utf-8", newline="\n")
FILES["main"].write_text(main_src, encoding="utf-8", newline="\n")

print("완료: 6개 파일 모두 패치했습니다.")
print("  - app/data_sources.py : Bybit/바이낸스 펀딩비 조회 함수 추가")
print("  - app/config.py       : PRICE_FUNDING_RATE_USDC 추가")
print("  - app/schemas.py      : FundingRateResponse / FUNDING_RATE_EXAMPLE 추가")
print("  - app/logic.py        : get_funding_rate 추가")
print("  - app/payment.py      : GET /v1/derivatives/funding-rate 라우트 등록")
print("  - main.py             : GET /v1/derivatives/funding-rate 엔드포인트 추가")
print("")
print("이제 'git diff'로 6개 파일 변경사항을 확인해주세요.")
