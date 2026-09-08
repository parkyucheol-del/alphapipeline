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
    get_contract_health_audit,
    get_dex_liquidity_slippage,
    get_dump_risk,
    get_exit_capacity_audit,
    get_funding_apr_matrix,
    get_funding_rate,
    get_kimchi_alert,
    get_macro_calendar_dday,
    get_neg_risk_arbitrage,
    get_token_diagnostic,
    get_token_risk,
    get_whale_position_audit,
    refresh_unlock_cache,
)
from app.llms_txt import build_llms_txt
from app.markdown_tool import url_to_markdown
from app.payment import ACTIVE_NETWORK, USE_CDP_FACILITATOR, build_resource_server, build_routes
from app.schemas import (
    ArbSpreadResponse,
    ContractHealthAuditResponse,
    DexSlippageResponse,
    DumpRiskResponse,
    ErrorResponse,
    FundingAprMatrixResponse,
    FundingRateResponse,
    KimchiAlertResponse,
    MacroDdayResponse,
    MarkdownResponse,
    PredictionExitCapacityAuditResponse,
    PredictionNegRiskArbitrageResponse,
    TokenDiagnosticResponse,
    TokenRiskResponse,
    WhalePositionAuditResponse,
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
    description="Pay-per-call ($0.005-$0.03 USDC) market/on-chain data API for AI agents and trading bots",
    version="0.3.0",
    lifespan=lifespan,
    # /openapi.json, /docs에 표시되는 태그 그룹 설명. x402 Bazaar 스펙과는 무관하지만,
    # OpenAPI 스펙을 그대로 읽는 에이전트 디렉토리(예: Agentic.Market 후보군)를 위해
    # 사람이 읽어도, 기계가 읽어도 되는 문서를 갖춰두는 차원.
    openapi_tags=[
        {"name": "market", "description": "Real-time price and on-chain event market data"},
        {"name": "tools", "description": "Utility tools for AI agents"},
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
            "/v1/security/contract-health-audit": settings.PRICE_CONTRACT_HEALTH_USDC,
            "/v1/derivatives/funding-rate": settings.PRICE_FUNDING_RATE_USDC,
            "/v1/derivatives/funding-apr-matrix": settings.PRICE_FUNDING_APR_USDC,
            "/v1/dex/liquidity-slippage": settings.PRICE_DEX_SLIPPAGE_USDC,
            "/v1/calendar/macro-dday": settings.PRICE_MACRO_DDAY_USDC,
            "/v1/arb/spread-matrix": settings.PRICE_ARB_SPREAD_USDC,
            "/v1/derivatives/whale-position-audit": settings.PRICE_WHALE_AUDIT_USDC,
            "/v1/security/token-diagnostic": settings.PRICE_TOKEN_DIAGNOSTIC_USDC,
            "/v1/prediction/neg-risk-arbitrage": settings.PRICE_NEG_RISK_ARBITRAGE_USDC,
            "/v1/prediction/exit-capacity-audit": settings.PRICE_EXIT_CAPACITY_AUDIT_USDC,
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
            "/v1/security/contract-health-audit",
            "/v1/derivatives/funding-rate",
            "/v1/derivatives/funding-apr-matrix",
            "/v1/dex/liquidity-slippage",
            "/v1/calendar/macro-dday",
            "/v1/arb/spread-matrix",
            "/v1/derivatives/whale-position-audit",
            "/v1/security/token-diagnostic",
            "/v1/prediction/neg-risk-arbitrage",
            "/v1/prediction/exit-capacity-audit",
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
        200: {"model": DumpRiskResponse, "description": "Unlock/vesting dump-risk data"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (DropsTab/Sablier/CoinGecko) error"},
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
        200: {"model": KimchiAlertResponse, "description": "Kimchi premium calculation result"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (Upbit/Binance) error"},
    },
)
async def kimchi_alert_endpoint(symbol: str = Query("BTC", description="e.g. BTC, ETH, SOL")):
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
        200: {"model": MarkdownResponse, "description": "Cleaned Markdown"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Target URL access/parsing error"},
    },
)
async def ai_markdown_endpoint(url: str = Query(..., description="Webpage URL to convert")):
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
        200: {"model": TokenRiskResponse, "description": "Token security / honeypot risk data"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (GoPlus/Honeypot.is) error"},
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
    "/v1/security/contract-health-audit",
    tags=["market"],
    summary="Audit LP lock/burn status for a token contract (GoPlus)",
    description=(
        "Use this endpoint to check whether a token's liquidity pool tokens are locked, "
        "burned, or freely held by a single wallet before trusting its liquidity - a key "
        "rug-pull signal that security.token_risk does not cover. Reuses the same GoPlus "
        "Security data as token-risk (no extra upstream call): lp_locked_pct (recognized "
        "third-party lockers), lp_burned_pct (sent to a known burn address), and "
        "top_unlocked_holder_pct (largest single non-locked LP holder), rolled up into a "
        "liquidity_health category (LOCKED / PARTIALLY_LOCKED / UNLOCKED / NO_LP_DATA). "
        "Deliberately does not include any qualitative 'suspicious transaction' judgment - "
        "only GoPlus's own numbers. Input: `chain_id` (e.g. 8453 for Base) and "
        "`contract_address` (required). Has no fallback if GoPlus fails, since Honeypot.is "
        "does not expose LP lock data."
    ),
    responses={
        200: {"model": ContractHealthAuditResponse, "description": "LP lock/burn audit result"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (GoPlus) error"},
    },
)
async def contract_health_audit_endpoint(
    chain_id: int = Query(..., description="EVM chain id, e.g. 8453 for Base"),
    contract_address: str = Query(..., description="Token contract address (0x...)"),
):
    try:
        data = await get_contract_health_audit(chain_id, contract_address)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("contract-health-audit 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/security/token-diagnostic",
    tags=["market"],
    summary="Bundle token_risk + contract_health_audit into one objective diagnostic report",
    description=(
        "Runs security.token_risk (honeypot/tax/mint/ownership) and "
        "security.contract_health_audit (LP lock/burn) in parallel against the same "
        "GoPlus data and returns both result sets combined, plus a deduped union of "
        "their risk_flags and a plain risk_flags_count. Deliberately does NOT compute "
        "a composite score or letter grade (A-F) - every field here is copied "
        "unchanged from the two underlying tools, no new weighting or judgment logic. "
        "Cheaper than calling both separately ($0.03 vs $0.04). Does not cover token "
        "unlock/vesting risk - use unlocks.dump_risk separately for that. Input: "
        "`chain_id` (e.g. 8453 for Base) and `contract_address` (required)."
    ),
    responses={
        200: {"model": TokenDiagnosticResponse, "description": "Combined diagnostic result (no composite score)"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (GoPlus) error"},
    },
)
async def token_diagnostic_endpoint(
    chain_id: int = Query(..., description="EVM chain id, e.g. 8453 for Base"),
    contract_address: str = Query(..., description="Token contract address (0x...)"),
):
    try:
        data = await get_token_diagnostic(chain_id, contract_address)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("token-diagnostic 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/derivatives/whale-position-audit",
    tags=["market"],
    summary="Audit a Hyperliquid wallet's open perp positions (leverage, liquidation risk, PnL)",
    description=(
        "Input a Hyperliquid wallet address you already know (from an explorer, a "
        "leaderboard screenshot, on-chain sleuthing, etc.) and get back its current "
        "perpetual futures exposure: every open position with side, size, leverage, "
        "unrealized PnL, liquidation price, and distance-to-liquidation percentage. "
        "This is deliberately an audit tool, not a 'smart money' discovery tool - "
        "Hyperliquid's public API has no leaderboard or large-trader disclosure "
        "endpoint, so this does not attempt to identify or rank wallets itself. "
        "risk_flags (HIGH_LEVERAGE, NEAR_LIQUIDATION) are computed from fixed numeric "
        "thresholds only, never a qualitative judgment. Input: `address` (required, "
        "Hyperliquid/EVM wallet address, 0x...)."
    ),
    responses={
        200: {"model": WhalePositionAuditResponse, "description": "Wallet position audit result"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (Hyperliquid) error"},
    },
)
async def whale_position_audit_endpoint(
    address: str = Query(..., description="Hyperliquid/EVM wallet address to audit (0x...)"),
):
    try:
        data = await get_whale_position_audit(address)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("whale-position-audit 처리 실패")
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
        200: {"model": FundingRateResponse, "description": "Perpetual futures funding rate data"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (Bybit/Binance) error"},
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
        200: {"model": FundingAprMatrixResponse, "description": "Annualized funding APR and carry-trade breakeven days"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (Bybit/Binance) error"},
    },
)
async def funding_apr_matrix_endpoint(
    symbol: str = Query(..., description="e.g. BTC, ETH, or BTCUSDT"),
    assumed_round_trip_cost_pct: float = Query(
        0.2, description="Combined entry+exit trading fee % across both legs, used for breakeven_days"
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
        "liquidity, 24h volume, an ESTIMATED slippage percentage for the given trade size, "
        "and a slippage_tiers array with the same estimate at fixed $1,000/$5,000/$10,000 "
        "sizes (independent of trade_size_usd) so an agent can gauge depth at a glance. "
        "Computed under a documented approximation (see notice) since GeckoTerminal's free "
        "API only exposes combined USD liquidity, not per-token reserve amounts. Input: "
        "`network` (e.g. base, eth - default base), `trade_size_usd` (required), and either "
        "`pool_address` (a specific pool) or `token_address` (the most liquid pool for that "
        "token is selected automatically) - one of the two is required. Do not treat "
        "estimated_slippage_pct or slippage_tiers as an exact on-chain quote - always "
        "re-verify with a live quote before executing, especially for concentrated-liquidity "
        "or stableswap pools."
    ),
    responses={
        200: {"model": DexSlippageResponse, "description": "DEX liquidity/slippage estimate data"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (GeckoTerminal) error or input error"},
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
        200: {"model": ArbSpreadResponse, "description": "Arbitrage spread calculation result"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (Coinbase/CoinGecko/GeckoTerminal) error or input error"},
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
        200: {"model": MacroDdayResponse, "description": "Macro event D-Day calendar data"},
        402: {"description": "x402 payment required"},
        500: {"model": ErrorResponse, "description": "Internal processing error"},
    },
)
async def macro_dday_endpoint():
    try:
        data = await get_macro_calendar_dday()
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("macro-dday 처리 실패")
        return JSONResponse(status_code=500, content={"error": "internal_error", "message": str(e)})


@app.get(
    "/v1/prediction/neg-risk-arbitrage",
    tags=["market"],
    summary="Detect basket arbitrage in a Polymarket neg-risk multi-outcome event",
    description=(
        "Use this endpoint to scan a specific Polymarket neg-risk (mutually-exclusive, "
        "multi-outcome) event for a risk-free or near risk-free basket arbitrage: since "
        "owning exactly 1 YES share of every outcome always settles to exactly $1, a "
        "basket price away from $1 is an edge - after subtracting assumed_round_trip_cost_pct "
        "(gas + fees + slippage buffer). Also computes buy/sell_basket_capacity_shares, the "
        "actual liquidity-bottleneck size the thinnest outcome's order book can support "
        "within max_slippage_pct, so arbitrage_viable reflects what's really executable, "
        "not just a top-of-book mirage. Input: required `event_slug` (from the event's URL "
        "on polymarket.com) and optional `assumed_round_trip_cost_pct` (default 1.5), "
        "`max_slippage_pct` (default 1.0), `min_net_edge_pct` (default 1.0). Polymarket only "
        "- Kalshi's Data ToS explicitly forbids this kind of commercial reuse of their data, "
        "so it is never used here. Pair with exit-capacity-audit on an individual leg before "
        "sizing a real position."
    ),
    responses={
        200: {"model": PredictionNegRiskArbitrageResponse, "description": "Neg-risk basket arbitrage result"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (Polymarket) error or input error"},
    },
)
async def neg_risk_arbitrage_endpoint(
    event_slug: str = Query(..., description="Polymarket event slug, from the event's URL on polymarket.com"),
    assumed_round_trip_cost_pct: float = Query(
        1.5, description="Gas + fees + slippage buffer, as a percentage of $1 basket notional"
    ),
    max_slippage_pct: float = Query(
        1.0, description="How far past each leg's best price to walk the book when sizing basket capacity"
    ),
    min_net_edge_pct: float = Query(
        1.0, description="Minimum net edge (% of $1 basket notional) required to flag arbitrage_viable: true"
    ),
):
    try:
        data = await get_neg_risk_arbitrage(
            event_slug=event_slug,
            assumed_round_trip_cost_pct=assumed_round_trip_cost_pct,
            max_slippage_pct=max_slippage_pct,
            min_net_edge_pct=min_net_edge_pct,
        )
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("neg-risk-arbitrage 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/prediction/exit-capacity-audit",
    tags=["market"],
    summary="Audit real executable liquidity for a Polymarket outcome position",
    description=(
        "Use this endpoint to check whether a given position size in a specific Polymarket "
        "outcome can actually be filled right now - walks the live order book and returns "
        "whether it's fully executable, the average fill price, and the price impact versus "
        "the best quote. This is a live, point-in-time snapshot, not historical/average "
        "liquidity. Input: required `position_size_shares`, and either `token_id` (if already "
        "known) or `market_slug` (+ optional `outcome`, default 'yes') to resolve it "
        "automatically - only an exact Polymarket market slug is supported, this endpoint "
        "does not do fuzzy keyword search since a wrong silent match would be worse than an "
        "error. Optional `side` ('sell' default, or 'buy'). Pair this with "
        "neg-risk-arbitrage to validate one leg of a detected opportunity before sizing it."
    ),
    responses={
        200: {"model": PredictionExitCapacityAuditResponse, "description": "Executable liquidity audit result"},
        402: {"description": "x402 payment required"},
        502: {"model": ErrorResponse, "description": "Upstream (Polymarket) error or input error"},
    },
)
async def exit_capacity_audit_endpoint(
    position_size_shares: float = Query(..., description="Number of outcome shares to sell (or buy). Must be positive."),
    token_id: str | None = Query(None, description="The outcome's CLOB token_id / asset_id, if already known"),
    market_slug: str | None = Query(
        None, description="Exact Polymarket market slug, used to resolve token_id automatically"
    ),
    outcome: str = Query("yes", description="'yes' (default) or 'no' - which side to resolve when using market_slug"),
    side: str = Query("sell", description="'sell' (default) or 'buy'"),
):
    try:
        data = await get_exit_capacity_audit(
            position_size_shares=position_size_shares,
            token_id=token_id,
            market_slug=market_slug,
            outcome=outcome,
            side=side,
        )
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("exit-capacity-audit 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get(
    "/v1/debug/polymarket-connectivity",
    include_in_schema=False,  # 결제 게이트(app/payment.py build_routes)에 등록 안 함 - 임시 진단용, 무료
)
async def polymarket_connectivity_debug():
    """
    임시 진단용 엔드포인트.

    한국 로컬 PC에서는 정부 ISP 차단으로 폴리마켓 도메인 접근 시 HTTP 451이
    뜨는 것을 이미 확인했음 (Polymarket 자체 지역제한과는 무관한 별개 이슈).
    이 엔드포인트는 그것과 별개로, 실제 배포 서버(Render, 프랑크푸르트) egress
    IP 기준으로 읽기 전용 GET이 실제로 통하는지 직접 확인하기 위한 것.

    확인 끝나면 반드시 삭제할 것 - 인증 없는 아웃바운드 프록시로 악용될 수
    있으므로 오래 남겨두지 않는다.
    """
    import httpx

    checks: dict = {}
    timeout = httpx.Timeout(8.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1) 폴리마켓 자체 geoblock 자가진단 - Render egress IP가 어느 나라/
        #    차단상태로 잡히는지 가장 빠르게 알려주는 신호. 단, 이 엔드포인트의
        #    의미는 공식적으로 "주문 실행 가능 여부"이므로 blocked:true가 나와도
        #    곧바로 "읽기도 막혔다"로 확대 해석하지 말 것 - 2), 3)과 같이 봐야 함.
        try:
            r = await client.get("https://polymarket.com/api/geoblock")
            checks["geoblock"] = {"status": r.status_code, "body": r.text[:500]}
        except Exception as e:
            checks["geoblock"] = {"error": f"{type(e).__name__}: {e}"}

        # 2) Gamma API 공개 조회 (인증 불필요, get_neg_risk_arbitrage 등이 실제로 쓰는 도메인)
        try:
            r = await client.get("https://gamma-api.polymarket.com/events", params={"limit": 1})
            checks["gamma_api"] = {"status": r.status_code, "body": r.text[:500]}
        except Exception as e:
            checks["gamma_api"] = {"error": f"{type(e).__name__}: {e}"}

        # 3) CLOB API 공개 조회 (인증 불필요, get_polymarket_order_book이 실제로 쓰는 도메인)
        try:
            r = await client.get("https://clob.polymarket.com/markets")
            checks["clob_api"] = {"status": r.status_code, "body": r.text[:500]}
        except Exception as e:
            checks["clob_api"] = {"error": f"{type(e).__name__}: {e}"}

    return JSONResponse(content={"checks": checks, "notice": "임시 진단용 엔드포인트 - 확인 후 삭제 예정"})


from app.mcp_server import register_mcp_routes as _register_mcp_routes  # noqa: E402

_register_mcp_routes(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
