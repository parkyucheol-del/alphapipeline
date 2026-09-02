"""
AlphaPipeline - 초미세 결제 기반 온체인 데이터 파이프라인 API
사업계획서 Step 1~3 MVP 엔트리포인트.

로컬 실행: uvicorn main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from app.config import settings
from app.scheduler import start_scheduler
from app.logic import get_dump_risk, get_kimchi_alert, refresh_unlock_cache
from app.markdown_tool import url_to_markdown
from app.payment import ACTIVE_NETWORK, USE_CDP_FACILITATOR, build_resource_server, build_routes

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
    if settings.DUMP_RISK_ENABLED:
        try:
            # 서버 기동 시 락업 캐시가 비어있으면 1회 즉시 채워둔다 (첫 요청 지연 방지)
            await refresh_unlock_cache()
        except Exception:
            logger.exception("초기 unlock 캐시 생성 실패 (스케줄러가 다음 주기에 재시도합니다)")
    else:
        logger.info(
            "DUMP_RISK_ENABLED=false : dump-risk 엔드포인트는 '준비 중' 상태로 서빙됩니다 "
            "(kimchi-alert/ai-markdown으로 먼저 출시 후 수요 검증 전략)."
        )
    yield


app = FastAPI(
    title="AlphaPipeline API",
    description="AI 에이전트/트레이더 봇을 위한 초미세 결제(0.01 USDC) 데이터 API",
    version="0.2.0",
    lifespan=lifespan,
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
        "price_per_call_usdc": settings.PRICE_PER_CALL_USDC,
        "payment": {
            "protocol": "x402",
            "network": ACTIVE_NETWORK,
            "facilitator": "cdp" if USE_CDP_FACILITATOR else "x402.org-testnet-fallback",
            "bypassed_for_testing": settings.PAYMENT_BYPASS_FOR_TESTING,
        },
        "endpoints": [
            "/v1/market/kimchi-alert",
            "/v1/tools/ai-markdown",
        ],
        "coming_soon_endpoints": (
            ["/v1/unlocks/dump-risk"] if not settings.DUMP_RISK_ENABLED else []
        ),
        "docs": "/docs",
    }


@app.get("/healthz")
async def healthz():
    return {"status": "healthy"}


@app.get("/v1/unlocks/dump-risk")
async def dump_risk_endpoint():
    # DUMP_RISK_ENABLED=false 인 동안은 build_routes()가 이 경로를 x402 미들웨어의
    # 결제 대상 목록에서 아예 빼두기 때문에, 요청이 결제 검사 없이 곧장 여기로
    # 들어온다. 그래서 여기서 바로 503을 반환해도 호출자에게 과금되지 않는다.
    if not settings.DUMP_RISK_ENABLED:
        return JSONResponse(
            status_code=503,
            content={
                "status": "coming_soon",
                "message": (
                    "이 엔드포인트는 아직 준비 중입니다. "
                    "kimchi-alert / ai-markdown 수요를 먼저 검증한 뒤 오픈할 예정입니다."
                ),
            },
        )
    try:
        data = await get_dump_risk()
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("dump-risk 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get("/v1/market/kimchi-alert")
async def kimchi_alert_endpoint(symbol: str = Query("BTC", description="예: BTC, ETH, SOL")):
    # 결제 검증은 이제 main.py 상단에서 장착한 x402 PaymentMiddlewareASGI가
    # 라우트 진입 전에 처리한다 - 여기까지 왔다는 건 이미 결제가 확인됐다는 뜻.
    try:
        data = await get_kimchi_alert(symbol.upper())
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("kimchi-alert 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


@app.get("/v1/tools/ai-markdown")
async def ai_markdown_endpoint(url: str = Query(..., description="변환할 웹페이지 URL")):
    try:
        data = await url_to_markdown(url)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("ai-markdown 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)
