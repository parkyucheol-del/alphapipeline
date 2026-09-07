"""
AlphaPipeline - 초미세 결제 기반 온체인 데이터 파이프라인 API
사업계획서 Step 1~3 MVP 엔트리포인트.

로컬 실행: uvicorn main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import settings
from app.scheduler import start_scheduler
from app.logic import (
    get_arb_spread_matrix,
    get_dex_liquidity_slippage,
    get_dump_risk,
    get_funding_apr_matrix,
    get_funding_rate,
    get_kimchi_alert,
    get_macro_calendar_dday,
    get_token_risk,
    refresh_unlock_cache,
)
from app.llms_txt import build_llms_txt
from app.markdown_tool import url_to_markdown
from app.payment import ACTIVE_NETWORK, USE_CDP_FACILITATOR, build_resource_server, build_routes
from app.schemas import (
    ArbSpreadResponse,
    DexSlippageResponse,
    DumpRiskResponse,
    ErrorResponse,
    FundingAprMatrixResponse,
    FundingRateResponse,
    KimchiAlertResponse,
    MacroDdayResponse,
    MarkdownResponse,
    TokenRiskResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alphapipeline")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.is_wallet_configured():
        logger.warning(
            "RECEIVER_WALLET_ADDRESS가 설정되지 않았습니다. "
            ".env에 실제 지갑 주소를 넣기 전까지는 결제 수신이 불완전합니다."
        )
    if settings.PAYMENT_BYPASS_FOR_TESTING:
        logger.warning("PAYMENT_BYPASS_FOR_TESTING=true : x402 결제 미들웨어가 장착되지 않았습니다 (테스트 모드, 전 엔드포인트 무료).")

    start_scheduler()
    # DropsTab 키가 있든 없든(온체인 Sablier 폴백) 이제 dump-risk는 항상 실제
    # 데이터 경로를 갖고 있으므로, DUMP_RISK_ENABLED와 무관하게 캐시를 미리 채운다.
    # DUMP_RISK_ENABLED는 이제 "데이터가 있는지"가 아니라 "이 라우트에 x402 과금을
    # 적용할지"만 제어한다 (app/payment.py의 build_routes 참고).
    try:
        await refresh_unlock_cache()
    except Exception:
        logger.exception("초기 unlock 캐시 생성 실패 (스케줄러가 다음 주기에 재시도합니다)")
    yield


app = FastAPI(
    title="AlphaPipeline API",
    description="AI 에이전트/트레이더 봇을 위한 초미세 결제(0.01 USDC) 데이터 API",
    version="0.3.0",
    lifespan=lifespan,
    # /openapi.json, /docs에 표시되는 태그 그룹 설명. x402 Bazaar 스펙과는 무관하지만,
    # OpenAPI 스펙을 그대로 읽는 에이전트 디렉토리(예: Agentic.Market 후보군)를 위해
    # 사람이 읽어도, 기계가 읽어도 되는 문서를 갖춰두는 차원.
    openapi_tags=[
        {"name": "market", "description": "실시간 시세/온체인 이벤트 기반 시장 데이터"},
        {"name": "tools", "description": "AI 에이전트용 유틸리티 도구"},
    ],
)

# ===== x402 공식 결제 미들웨어 장착 =====
# PAYMENT_BYPASS_FOR_TESTING=true인 동안은 미들웨어를 아예 장착하지 않는다 -
# 로컬에서 지갑/CDP 키 없이도 전체 흐름을 빠르게 테스트하기 위함.
# (실서비스에서는 render.yaml처럼 반드시 false로 배포해야 함)
if not settings.PAYMENT_BYPASS_FOR_TESTING:
    from x402.http.middleware.fastapi import PaymentMiddlewareASGI

    _x402_server = build_resource_server()
    _x402_routes = build_routes(dump_risk_enabled=settings.DUMP_RISK_ENABLED)
    app.add_middleware(PaymentMiddlewareASGI, routes=_x402_routes, server=_x402_server)


@app.get("/")
async def root():
    return {
        "service": "AlphaPipeline",
        "status": "ok",
        # 2026-09부터 전 엔드포인트 단일가가 아니라 데이터 가치 기반 차등 요금제로
        # 전환함 (app/payment.py의 build_routes 참고) - 그래서 단일 숫자 대신
        # 엔드포인트별 가격표를 내려준다.
        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            # DUMP_RISK_ENABLED가 실제 과금 여부를 결정하는 것과 동일한 플래그를
            # 그대로 참조한다 - PRICE_DUMP_RISK_USDC 값과 무관하게 이 필드가 항상
            # 실제 서빙 상태와 일치하도록 (app/payment.py의 build_routes 참고).
            "/v1/unlocks/dump-risk": (
                settings.PRICE_DUMP_RISK_USDC if settings.DUMP_RISK_ENABLED else 0.0
            ),
            "/v1/security/token-risk": settings.PRICE_TOKEN_RISK_USDC,
            "/v1/derivatives/funding-rate": settings.PRICE_FUNDING_RATE_USDC,
            "/v1/derivatives/funding-apr-matrix": settings.PRICE_FUNDING_APR_USDC,
            "/v1/dex/liquidity-slippage": settings.PRICE_DEX_SLIPPAGE_USDC,
            "/v1/calendar/macro-dday": settings.PRICE_MACRO_DDAY_USDC,
            "/v1/arb/spread-matrix": settings.PRICE_ARB_SPREAD_USDC,
        },
        "payment": {
            "protocol": "x402",
            "network": ACTIVE_NETWORK,
            "facilitator": "cdp" if USE_CDP_FACILITATOR else "x402.org-testnet-fallback",
            "bypassed_for_testing": settings.PAYMENT_BYPASS_FOR_TESTING,
        },
        "endpoints": [
            "/v1/unlocks/dump-risk",
            "/v1/market/kimchi-alert",
            "/v1/tools/ai-markdown",
            "/v1/security/token-risk",
            "/v1/derivatives/funding-rate",
            "/v1/derivatives/funding-apr-matrix",
            "/v1/dex/liquidity-slippage",
            "/v1/calendar/macro-dday",
            "/v1/arb/spread-matrix",
        ],
        "coming_soon_endpoints": [],
        "docs": "/docs",
        "openapi_spec": "/openapi.json",
        "agent_spec": "/llms.txt",
    }


@app.get("/healthz")
async def healthz():
    return {"status": "healthy"}


@app.get(
    "/llms.txt",
    include_in_schema=False,  # x402 결제 대상 데이터 엔드포인트가 아니라 크롤러용 정적 문서라 OpenAPI 스펙에서는 뺌
)
async def llms_txt_endpoint():
    # 결제 게이트(app/payment.py의 build_routes)에 절대 등록하지 않는다 - 에이전트가
    # 돈을 내지 않고도 "이 서비스가 뭘 하는지"를 먼저 읽을 수 있어야 발견이 되기 때문.
    return PlainTextResponse(content=build_llms_txt(), media_type="text/plain; charset=utf-8")


@app.get(
    "/v1/unlocks/dump-risk",
    tags=["market"],
    summary="Detect tokens at risk of sell pressure from unlocks/vesting",
    description=(
        "Use this endpoint when you need to assess whether a token carries dumping risk from "
        "token unlocks or ongoing vesting schedules - before entering a position, when screening "
        "a token list for a trading strategy, or when a user asks 'is this token safe from unlocks'. "
        "Returns tokens whose currently-locked or unlock-eligible supply exceeds a materiality "
        "threshold (default 3% of circulating supply), each with a computed risk_level "
        "(LOW/MEDIUM/HIGH). Data source varies automatically: when a precise unlock calendar is "
        "configured it includes days_until_unlock; otherwise (the current default, sourced from "
        "on-chain Sablier vesting contracts) it reports only the currently-locked amount, with "
        "days_until_unlock=null and timing_precision='pending_schema_verification' - treat a null "
        "value as 'timing unknown', never as 'no risk'. No input parameters. Do not treat the "
        "absence of a token in this list as proof it has no lockup - coverage is limited to "
        "supported vesting mechanisms; always check the coverage_notice field in the response."
    ),
    responses={
        200: {"model": DumpRiskResponse, "description": "락업 해제 위험도 데이터"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "업스트림(DropsTab/Sablier/CoinGecko) 오류"},
    },
)
async def dump_risk_endpoint():
    try:
        data = await get_dump_risk()
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("dump-risk 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/market/kimchi-alert",
    tags=["market"],
    summary="Detect Korea-vs-global crypto price arbitrage (kimchi premium)",
    description=(
        "Use this endpoint when you need to know whether a cryptocurrency is trading at a "
        "premium or discount on Korean exchanges (Upbit) versus the global market, commonly "
        "known as the 'kimchi premium'. Call this when asked about cross-exchange arbitrage "
        "opportunities in Korean crypto markets, to detect a reverse premium (localized crash "
        "risk, triggered at -1.5% or below), or to detect a sudden premium surge within the last "
        "hour (3 percentage points or more). Returns the current premium percentage, its 1-hour "
        "change, and boolean alert flags. Input: optional `symbol` query parameter (e.g. BTC, "
        "ETH, SOL - default BTC). This is a live snapshot only - do not call it for historical "
        "or backtesting data, or for non-Korean-exchange comparisons."
    ),
    responses={
        200: {"model": KimchiAlertResponse, "description": "김치프리미엄 계산 결과"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "업스트림(업비트/바이낸스) 오류"},
    },
)
async def kimchi_alert_endpoint(symbol: str = Query("BTC", description="예: BTC, ETH, SOL")):
    # 결제 검증은 이제 main.py 상단에서 장착한 x402 PaymentMiddlewareASGI가
    # 라우트 진입 전에 처리한다 - 여기까지 왔다는 건 이미 결제가 확인됐다는 뜻.
    try:
        data = await get_kimchi_alert(symbol.upper())
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("kimchi-alert 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/tools/ai-markdown",
    tags=["tools"],
    summary="Convert any webpage into clean, agent-ready Markdown",
    description=(
        "Use this endpoint when you need to read the actual content of a webpage but want to "
        "avoid wasting tokens on HTML tags, ads, navigation menus, and scripts - or when raw "
        "HTML parsing is causing hallucinations in downstream reasoning. Call this before "
        "summarizing, extracting facts from, or answering questions about any arbitrary URL. "
        "Input: required `url` query parameter (the full http/https URL to convert). Returns the "
        "page title, character count, and clean Markdown body text. Do not call this for URLs "
        "requiring authentication/login, or for non-HTML resources such as PDFs or binary files - "
        "those are not supported."
    ),
    responses={
        200: {"model": MarkdownResponse, "description": "정제된 마크다운"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "대상 URL 접근/파싱 오류"},
    },
)
async def ai_markdown_endpoint(url: str = Query(..., description="변환할 웹페이지 URL")):
    try:
        data = await url_to_markdown(url)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("ai-markdown 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/security/token-risk",
    tags=["security"],
    summary="Check a token contract for honeypot/scam risk before buying",
    description=(
        "Use this endpoint when you need to know whether a token contract is safe to buy - "
        "call it right before entering a position on an unfamiliar or newly-listed token. "
        "Returns is_honeypot, buy/sell tax percentages, mintability, open-source status, "
        "ownership renouncement, holder count, and a summarized risk_level "
        "(LOW/MEDIUM/HIGH/UNKNOWN) with risk_flags. Data source is GoPlus Security "
        "(primary), falling back to Honeypot.is if GoPlus is unavailable - check the "
        "data_source and notice fields to see which was used. Input: required "
        "`chain_id` (EVM chain id, e.g. 8453 for Base) and `contract_address` query "
        "parameters. Do not treat a null field as 'safe' - it means that field could not "
        "be determined; check risk_level and risk_flags instead."
    ),
    responses={
        200: {"model": TokenRiskResponse, "description": "토큰 보안/허니팟 위험도 데이터"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "업스트림(GoPlus/Honeypot.is) 오류"},
    },
)
async def token_risk_endpoint(
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


@app.get(
    "/v1/derivatives/funding-apr-matrix",
    tags=["market"],
    summary="Annualize a perpetual funding rate into APR + carry-trade breakeven days",
    description=(
        "Use this endpoint to evaluate a spot+perpetual carry trade (cash-and-carry): it "
        "takes the current funding rate (same data as funding-rate) and annualizes it into "
        "an APR, tells you which side (SHORT or LONG perp) currently collects funding, and "
        "computes how many days of that funding income it takes to recoup an assumed "
        "round-trip trading cost. This is a pure calculation layer on top of funding-rate - "
        "no extra upstream API call. Input: required `symbol` (e.g. BTC, ETH, or BTCUSDT) "
        "and optional `assumed_round_trip_cost_pct` (default 0.2 - the combined entry+exit "
        "trading fee percentage across both the spot and perpetual legs; pass your own "
        "actual fee tier for an accurate breakeven_days). Does not account for margin "
        "borrow cost, spot-perp basis risk, or perp liquidation risk."
    ),
    responses={
        200: {"model": FundingAprMatrixResponse, "description": "펀딩비 연환산 APR 및 캐리 트레이드 손익분기일"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "업스트림(Bybit/바이낸스) 오류"},
    },
)
async def funding_apr_matrix_endpoint(
    symbol: str = Query(..., description="e.g. BTC, ETH, or BTCUSDT"),
    assumed_round_trip_cost_pct: float = Query(
        0.2, description="Combined entry+exit trading fee %% across both legs, used for breakeven_days"
    ),
):
    try:
        data = await get_funding_apr_matrix(symbol, assumed_round_trip_cost_pct=assumed_round_trip_cost_pct)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("funding-apr-matrix 처리 실패")
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


@app.get(
    "/v1/arb/spread-matrix",
    tags=["market"],
    summary="CEX-DEX arbitrage spread calculator",
    description=(
        "Use this endpoint before executing a cross-venue arbitrage trade to check "
        "whether a global reference price (Coinbase spot, CoinGecko fallback - NOT a "
        "specific exchange orderbook) and a DEX pool price (GeckoTerminal) diverge enough "
        "to be worth trading after an assumed flat gas cost. Returns gross/net spread "
        "percentages, a direction flag, and an is_profitable boolean against your "
        "min_spread_threshold_pct. Input: `symbol` (required), `network` (default base), "
        "`trade_size_usd` (default 1000), `min_spread_threshold_pct` (default 0.8), and "
        "either `pool_address` or `token_address` (most liquid pool auto-selected) - one "
        "of the two is required. Does not account for CEX deposit/withdrawal "
        "availability, trading fees, or slippage beyond trade_size_usd - always "
        "re-verify with live quotes before executing."
    ),
    responses={
        200: {"model": ArbSpreadResponse, "description": "차익거래 스프레드 계산 결과"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "업스트림(Coinbase/CoinGecko/GeckoTerminal) 오류 또는 입력 오류"},
    },
)
async def arb_spread_matrix_endpoint(
    symbol: str = Query(..., description="Ticker symbol, e.g. SUI, BTC, ETH"),
    network: str = Query("base", description="GeckoTerminal network id, e.g. base, eth"),
    trade_size_usd: float = Query(1000.0, description="Hypothetical trade size in USD"),
    min_spread_threshold_pct: float = Query(
        0.8, description="Net spread threshold (%) above which is_profitable is true"
    ),
    pool_address: str | None = Query(None, description="Specific DEX pool contract address"),
    token_address: str | None = Query(
        None, description="Token contract address (picks the most liquid pool automatically)"
    ),
):
    try:
        data = await get_arb_spread_matrix(
            symbol=symbol,
            network=network,
            trade_size_usd=trade_size_usd,
            pool_address=pool_address,
            token_address=token_address,
            min_spread_threshold_pct=min_spread_threshold_pct,
        )
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("arb-spread-matrix 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/calendar/macro-dday",
    tags=["market"],
    summary="Countdown to the nearest major US macro event (FOMC/CPI/NFP)",
    description=(
        "Use this endpoint when you need to know how much time is left before the next "
        "market-moving US macro release - a Fed interest rate decision (FOMC), CPI inflation "
        "report, or nonfarm payrolls (NFP) release - to plan position sizing or avoid holding "
        "risk into a high-impact print. Returns the nearest event's name, exact date/time (UTC "
        "and KST), a D-Day countdown, exact time remaining (days/hours/minutes), an impact "
        "level, and the next few upcoming events for context. Data is a static, pre-loaded "
        "2026 calendar sourced from official Federal Reserve and BLS release schedules - no "
        "live external API call is made, so this endpoint is fast and never fails on an "
        "upstream outage. No input parameters required."
    ),
    responses={
        200: {"model": MacroDdayResponse, "description": "매크로 이벤트 D-Day 캘린더 데이터"},
        402: {"description": "x402 결제 필요"},
        500: {"model": ErrorResponse, "description": "내부 처리 오류"},
    },
)
async def macro_dday_endpoint():
    try:
        data = await get_macro_calendar_dday()
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("macro-dday 처리 실패")
        return JSONResponse(status_code=500, content={"error": "internal_error", "message": str(e)})


from app.mcp_server import register_mcp_routes as _register_mcp_routes  # noqa: E402

_register_mcp_routes(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
