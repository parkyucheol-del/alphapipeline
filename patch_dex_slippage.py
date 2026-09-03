"""
1회성 통합 패치 스크립트: GET /v1/dex/liquidity-slippage 신규 엔드포인트 추가
(GeckoTerminal 무료 API, DexScreener는 재판매 금지 약관으로 배제).

전제: app/payment.py, app/schemas.py, main.py, app/config.py가 이미 이전 두
패치(patch_sablier_goplus.py, patch_funding_rate.py)로 funding-rate까지 반영된
상태여야 합니다.

건드리는 파일 6개: app/data_sources.py, app/config.py, app/schemas.py,
app/logic.py, app/payment.py, main.py

동작 방식: 모든 파일에 대해 먼저 "이 패치를 적용할 수 있는가"를 전부 확인하고
(검증 단계), 하나라도 실패하면 아무 파일도 건드리지 않고 종료한다(전부 적용
아니면 전부 취소).

실행: 레포 루트(app 폴더가 보이는 위치)에서 `python patch_dex_slippage.py`
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
            f"(이전 패치들(patch_sablier_goplus.py, patch_funding_rate.py)이 먼저 적용되어 있는지 확인해주세요.)"
        )
    return text.replace(old, new)


# ---------------------------------------------------------------------------
# 1) app/data_sources.py: GeckoTerminal 풀 조회 함수 추가
# ---------------------------------------------------------------------------
ds_src = read("data_sources")

if "get_geckoterminal_pool" in ds_src:
    fail("data_sources.py: get_geckoterminal_pool이 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

DS_NEW_FUNCTIONS = '''


GECKOTERMINAL_POOL_URL = "https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_address}"
GECKOTERMINAL_TOKEN_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{token_address}/pools"


async def get_geckoterminal_pool(network: str, pool_address: str) -> dict:
    """
    GeckoTerminal에서 풀 하나의 데이터를 조회한다 (무료, 키 불필요, ~30req/min).
    DexScreener는 이용약관상 제3자 재판매/유료 서비스 제공 금지 조항이 있어
    데이터 소스로 쓰지 않는다 - GeckoTerminal 단독 사용.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            GECKOTERMINAL_POOL_URL.format(network=network, pool_address=pool_address)
        )
        r.raise_for_status()
        body = r.json()
        data = body.get("data")
        if not data:
            raise ValueError(f"GeckoTerminal에서 풀 {pool_address}을(를) 찾지 못했습니다")
        return data


async def get_geckoterminal_pools_for_token(network: str, token_address: str) -> list[dict]:
    """토큰 주소만 있을 때, 그 토큰이 걸린 풀 목록을 조회한다 (유동성 큰 풀을 고르기 위함)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            GECKOTERMINAL_TOKEN_POOLS_URL.format(network=network, token_address=token_address),
            params={"sort": "h24_volume_usd_liquidity_desc"},
        )
        r.raise_for_status()
        body = r.json()
        return body.get("data") or []
'''
ds_src = ds_src.rstrip("\n") + "\n" + DS_NEW_FUNCTIONS.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 2) app/config.py: PRICE_DEX_SLIPPAGE_USDC 추가 (기존 PRICE_FUNDING_RATE_USDC 줄 스타일 복제)
# ---------------------------------------------------------------------------
cfg_src = read("config")

if "PRICE_DEX_SLIPPAGE_USDC" in cfg_src:
    fail("config.py: PRICE_DEX_SLIPPAGE_USDC가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

cfg_matches = re.findall(r"^[ \t]*.*PRICE_FUNDING_RATE_USDC.*$", cfg_src, re.MULTILINE)
if len(cfg_matches) != 1:
    fail(
        f"config.py: PRICE_FUNDING_RATE_USDC 줄을 정확히 1개 찾아야 하는데 {len(cfg_matches)}개 찾았습니다."
    )
cfg_old_line = cfg_matches[0]
if "0.01" not in cfg_old_line:
    fail(f"config.py: PRICE_FUNDING_RATE_USDC 줄에서 기본값 0.01을 찾지 못했습니다:\n{cfg_old_line}")
cfg_new_line = cfg_old_line.replace("PRICE_FUNDING_RATE_USDC", "PRICE_DEX_SLIPPAGE_USDC").replace("0.01", "0.02")
cfg_insert_at = cfg_src.index(cfg_old_line) + len(cfg_old_line)
cfg_src = cfg_src[:cfg_insert_at] + "\n" + cfg_new_line + cfg_src[cfg_insert_at:]


# ---------------------------------------------------------------------------
# 3) app/schemas.py: DexSlippageResponse + DEX_SLIPPAGE_EXAMPLE 추가 (파일 맨 끝에 append)
# ---------------------------------------------------------------------------
schemas_src = read("schemas")

if "class DexSlippageResponse" in schemas_src:
    fail("schemas.py: DexSlippageResponse가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

SCHEMAS_NEW = '''


class DexSlippageResponse(BaseModel):
    generated_at: TimestampPair
    network: str
    pool_address: str | None = None
    token_address: str | None = None
    pool_name: str | None = None
    liquidity_usd: float | None = None
    volume_24h_usd: float | None = None
    trade_size_usd: float
    estimated_slippage_pct: float | None = None
    price_impact_model: str
    data_source: str
    notice: str | None = None


DEX_SLIPPAGE_EXAMPLE = {
    "generated_at": {"utc": "2026-09-04T12:00:00Z", "kst": "2026-09-04 21:00:00 KST"},
    "network": "base",
    "pool_address": "0xd0b53d9277642d899df5c87a3966a349a798f224",
    "token_address": None,
    "pool_name": "WETH / USDC 0.05%",
    "liquidity_usd": 25000000.0,
    "volume_24h_usd": 8500000.0,
    "trade_size_usd": 10000.0,
    "estimated_slippage_pct": 0.08,
    "price_impact_model": "constant_product_50_50_approximation",
    "data_source": "geckoterminal",
    "notice": (
        "슬리피지는 GeckoTerminal이 제공하는 풀의 합산 USD 유동성만으로 계산한 근사치입니다 - "
        "이 풀이 표준 constant-product(x*y=k) AMM이고 두 토큰이 50:50 비율로 구성되어 있다고 "
        "가정합니다. Uniswap v3류 집중 유동성 풀이나 스테이블스왑 풀에서는 실제 슬리피지와 "
        "차이가 클 수 있습니다 - 실제 매매 전 온체인 견적(quote)으로 반드시 재확인하세요."
    ),
}
'''
schemas_src = schemas_src.rstrip("\n") + "\n" + SCHEMAS_NEW.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 4) app/logic.py: get_dex_liquidity_slippage + 헬퍼 추가 (파일 맨 끝에 append)
# ---------------------------------------------------------------------------
logic_src = read("logic")

if "def get_dex_liquidity_slippage" in logic_src:
    fail("logic.py: get_dex_liquidity_slippage가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

LOGIC_NEW = '''


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
    "슬리피지는 GeckoTerminal이 제공하는 풀의 합산 USD 유동성만으로 계산한 근사치입니다 - "
    "이 풀이 표준 constant-product(x*y=k) AMM이고 두 토큰이 50:50 비율로 구성되어 있다고 "
    "가정합니다. Uniswap v3류 집중 유동성 풀이나 스테이블스왑 풀에서는 실제 슬리피지와 "
    "차이가 클 수 있습니다 - 실제 매매 전 온체인 견적(quote)으로 반드시 재확인하세요."
)


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
                "data_source": "none",
                "notice": f"GeckoTerminal에서 {network}의 {token_address} 토큰에 연결된 풀을 찾지 못했습니다.",
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
            "data_source": "geckoterminal",
            "notice": _DEX_SLIPPAGE_NOTICE + " (이 풀의 유동성 데이터를 확인할 수 없어 슬리피지를 계산하지 못했습니다.)",
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
        "data_source": "geckoterminal",
        "notice": _DEX_SLIPPAGE_NOTICE,
    }
'''
logic_src = logic_src.rstrip("\n") + "\n" + LOGIC_NEW.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 5) app/payment.py: import 확장 + dex_slippage_option + 신규 라우트
# ---------------------------------------------------------------------------
pay_src = read("payment")

PAY_OLD_IMPORT = """from app.schemas import (
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
PAY_NEW_IMPORT = """from app.schemas import (
    DEX_SLIPPAGE_EXAMPLE,
    DUMP_RISK_EXAMPLE,
    FUNDING_RATE_EXAMPLE,
    KIMCHI_ALERT_EXAMPLE,
    MARKDOWN_EXAMPLE,
    TOKEN_RISK_EXAMPLE,
    DexSlippageResponse,
    DumpRiskResponse,
    FundingRateResponse,
    KimchiAlertResponse,
    MarkdownResponse,
    TokenRiskResponse,
)"""
pay_src = must_replace(pay_src, PAY_OLD_IMPORT, PAY_NEW_IMPORT, 1, "payment.py: app.schemas import 블록")

PAY_OLD_OPTION = "    funding_rate_option = _payment_option(settings.PRICE_FUNDING_RATE_USDC)"
PAY_NEW_OPTION = PAY_OLD_OPTION + "\n    dex_slippage_option = _payment_option(settings.PRICE_DEX_SLIPPAGE_USDC)"
pay_src = must_replace(pay_src, PAY_OLD_OPTION, PAY_NEW_OPTION, 1, "payment.py: funding_rate_option 줄")

PAY_OLD_ROUTES_TAIL = '''            service_name="AlphaPipeline Funding Rate",
            tags=["crypto", "derivatives", "funding-rate"],
        ),
    }'''
PAY_NEW_ROUTES_TAIL = '''            service_name="AlphaPipeline Funding Rate",
            tags=["crypto", "derivatives", "funding-rate"],
        ),
        "GET /v1/dex/liquidity-slippage": _make_route_config(
            accepts=[dex_slippage_option],
            mime_type="application/json",
            description=(
                "GeckoTerminal-backed DEX pool liquidity and estimated trade slippage - "
                "size a trade or compare pools before swapping, with a clearly-flagged "
                "constant-product approximation model."
            ),
            resource=_resource_url("/v1/dex/liquidity-slippage"),
            extensions=_bazaar_extension(
                input_example={
                    "network": "base",
                    "token_address": "0x4200000000000000000000000000000000000006",
                    "trade_size_usd": 10000,
                },
                input_schema={
                    "type": "object",
                    "properties": {
                        "network": {
                            "type": "string",
                            "description": "GeckoTerminal network id, e.g. base, eth. Defaults to base.",
                        },
                        "pool_address": {
                            "type": "string",
                            "description": "Specific DEX pool contract address (optional if token_address is given).",
                        },
                        "token_address": {
                            "type": "string",
                            "description": "Token contract address - the most liquid pool is auto-selected (optional if pool_address is given).",
                        },
                        "trade_size_usd": {
                            "type": "number",
                            "description": "Hypothetical trade size in USD to estimate slippage for.",
                        },
                    },
                    "required": ["trade_size_usd"],
                },
                output_example=DEX_SLIPPAGE_EXAMPLE,
                output_schema=_inline_schema_defs(DexSlippageResponse.model_json_schema()),
            ),
            service_name="AlphaPipeline DEX Slippage",
            tags=["crypto", "dex", "liquidity", "slippage"],
        ),
    }'''
pay_src = must_replace(pay_src, PAY_OLD_ROUTES_TAIL, PAY_NEW_ROUTES_TAIL, 1, "payment.py: routes 딕셔너리 끝부분")


# ---------------------------------------------------------------------------
# 6) main.py: import 확장 + 루트 메타데이터 확장 + 신규 엔드포인트
# ---------------------------------------------------------------------------
main_src = read("main")

MAIN_OLD_LOGIC_IMPORT = (
    "from app.logic import get_dump_risk, get_funding_rate, get_kimchi_alert, get_token_risk, refresh_unlock_cache"
)
MAIN_NEW_LOGIC_IMPORT = (
    "from app.logic import ("
    "\n    get_dex_liquidity_slippage,"
    "\n    get_dump_risk,"
    "\n    get_funding_rate,"
    "\n    get_kimchi_alert,"
    "\n    get_token_risk,"
    "\n    refresh_unlock_cache,"
    "\n)"
)
main_src = must_replace(main_src, MAIN_OLD_LOGIC_IMPORT, MAIN_NEW_LOGIC_IMPORT, 1, "main.py: app.logic import 줄")

MAIN_OLD_SCHEMAS_IMPORT = (
    "from app.schemas import DumpRiskResponse, ErrorResponse, FundingRateResponse, KimchiAlertResponse, "
    "MarkdownResponse, TokenRiskResponse"
)
MAIN_NEW_SCHEMAS_IMPORT = (
    "from app.schemas import ("
    "\n    DexSlippageResponse,"
    "\n    DumpRiskResponse,"
    "\n    ErrorResponse,"
    "\n    FundingRateResponse,"
    "\n    KimchiAlertResponse,"
    "\n    MarkdownResponse,"
    "\n    TokenRiskResponse,"
    "\n)"
)
main_src = must_replace(main_src, MAIN_OLD_SCHEMAS_IMPORT, MAIN_NEW_SCHEMAS_IMPORT, 1, "main.py: app.schemas import 줄")

MAIN_OLD_PRICE_DICT = '''        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            "/v1/unlocks/dump-risk": settings.PRICE_DUMP_RISK_USDC,
            "/v1/security/token-risk": settings.PRICE_TOKEN_RISK_USDC,
            "/v1/derivatives/funding-rate": settings.PRICE_FUNDING_RATE_USDC,
        },'''
MAIN_NEW_PRICE_DICT = '''        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            "/v1/unlocks/dump-risk": settings.PRICE_DUMP_RISK_USDC,
            "/v1/security/token-risk": settings.PRICE_TOKEN_RISK_USDC,
            "/v1/derivatives/funding-rate": settings.PRICE_FUNDING_RATE_USDC,
            "/v1/dex/liquidity-slippage": settings.PRICE_DEX_SLIPPAGE_USDC,
        },'''
main_src = must_replace(main_src, MAIN_OLD_PRICE_DICT, MAIN_NEW_PRICE_DICT, 1, "main.py: price_per_call_usdc 딕셔너리")

MAIN_OLD_ENDPOINTS_LIST = '''        "endpoints": [
            "/v1/unlocks/dump-risk",
            "/v1/market/kimchi-alert",
            "/v1/tools/ai-markdown",
            "/v1/security/token-risk",
            "/v1/derivatives/funding-rate",
        ],'''
MAIN_NEW_ENDPOINTS_LIST = '''        "endpoints": [
            "/v1/unlocks/dump-risk",
            "/v1/market/kimchi-alert",
            "/v1/tools/ai-markdown",
            "/v1/security/token-risk",
            "/v1/derivatives/funding-rate",
            "/v1/dex/liquidity-slippage",
        ],'''
main_src = must_replace(main_src, MAIN_OLD_ENDPOINTS_LIST, MAIN_NEW_ENDPOINTS_LIST, 1, "main.py: endpoints 리스트")

MAIN_OLD_MAIN_GUARD = '''async def funding_rate_endpoint(symbol: str = Query(..., description="e.g. BTC, ETH, or BTCUSDT")):
    try:
        data = await get_funding_rate(symbol)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("funding-rate 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)'''
MAIN_NEW_ENDPOINT_PLUS_GUARD = '''async def funding_rate_endpoint(symbol: str = Query(..., description="e.g. BTC, ETH, or BTCUSDT")):
    try:
        data = await get_funding_rate(symbol)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("funding-rate 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/dex/liquidity-slippage",
    tags=["market"],
    summary="Estimate DEX pool liquidity and trade slippage (GeckoTerminal)",
    description=(
        "Use this endpoint when you need to size a trade or check whether a DEX pool has "
        "enough depth before swapping - call it before executing a swap to estimate price "
        "impact, or when comparing pools for a given token. Returns the pool's total USD "
        "liquidity, 24h volume, and an ESTIMATED slippage percentage for a given trade size, "
        "computed under a documented approximation (see notice) since GeckoTerminal's free "
        "API only exposes combined USD liquidity, not per-token reserve amounts. Input: "
        "`network` (e.g. base, eth - default base), `trade_size_usd` (required), and either "
        "`pool_address` (a specific pool) or `token_address` (the most liquid pool for that "
        "token is selected automatically) - one of the two is required. Do not treat "
        "estimated_slippage_pct as an exact on-chain quote - always re-verify with a live "
        "quote before executing, especially for concentrated-liquidity or stableswap pools."
    ),
    responses={
        200: {"model": DexSlippageResponse, "description": "DEX 유동성/슬리피지 추정 데이터"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "업스트림(GeckoTerminal) 오류 또는 입력 오류"},
    },
)
async def dex_liquidity_slippage_endpoint(
    trade_size_usd: float = Query(..., description="Hypothetical trade size in USD"),
    network: str = Query("base", description="GeckoTerminal network id, e.g. base, eth"),
    pool_address: str | None = Query(None, description="Specific pool contract address"),
    token_address: str | None = Query(
        None, description="Token contract address (picks the most liquid pool automatically)"
    ),
):
    try:
        data = await get_dex_liquidity_slippage(
            network=network,
            trade_size_usd=trade_size_usd,
            pool_address=pool_address,
            token_address=token_address,
        )
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("dex-liquidity-slippage 처리 실패")
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
print("  - app/data_sources.py : GeckoTerminal 풀 조회 함수 추가")
print("  - app/config.py       : PRICE_DEX_SLIPPAGE_USDC 추가")
print("  - app/schemas.py      : DexSlippageResponse / DEX_SLIPPAGE_EXAMPLE 추가")
print("  - app/logic.py        : get_dex_liquidity_slippage 추가")
print("  - app/payment.py      : GET /v1/dex/liquidity-slippage 라우트 등록")
print("  - main.py             : GET /v1/dex/liquidity-slippage 엔드포인트 추가")
print("")
print("이제 'git diff'로 6개 파일 변경사항을 확인해주세요.")
