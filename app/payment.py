"""
x402 결제 게이트 - 공식 Coinbase `x402` 파이썬 SDK + CDP Facilitator 기반.

## 왜 바꿨나
이전 버전은 web3.py로 직접 온체인 트랜잭션을 조회해서 "우리 지갑으로 USDC가
왔는지"만 확인하는 자체(비표준) 검증이었다. 이 방식은 x402 Bazaar나
Agentic.Market 같은 공식 인덱서/마켓플레이스에 등록될 수 없다 - 이들은 공식
파실리테이터(facilitator)가 EIP-3009(`transferWithAuthorization`) 서명 기반의
표준 "exact" 결제 스킴으로 검증·정산한 거래만 인식하기 때문이다. 그래서 이
파일은 자체 검증 로직을 전부 걷어내고, 공식 `x402` 패키지가 제공하는
`PaymentMiddlewareASGI`를 조립해주는 역할만 한다.

## 동작 방식 (중요한 구조 변화)
이전에는 각 라우트 핸들러 안에서 `payment_gate(request)`를 직접 호출해서
"통과/거절"을 판단했다. 공식 SDK는 이 방식이 아니라, FastAPI 앱 생성 시
`app.add_middleware(PaymentMiddlewareASGI, routes=..., server=...)`로
**한 번만** 장착하는 ASGI 미들웨어 방식이다 - 요청이 라우트 핸들러에
도달하기도 전에 미들웨어가 결제 헤더를 검사하고, 없거나 유효하지 않으면
표준 402 응답(Bazaar 인덱서가 읽는 `accepts`/`extensions.bazaar` 스키마 포함)을
대신 반환한다. 그래서 main.py에서 이 모듈의 `build_resource_server()` /
`build_routes()`로 미들웨어를 조립하고, 각 라우트 핸들러 코드에서는 결제
관련 코드가 전부 사라졌다 (main.py 참고).

## CDP Facilitator vs 테스트넷 파실리테이터
- `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET`(.env)이 둘 다 채워져 있으면,
  Coinbase Developer Platform의 공식 CDP Facilitator를 사용한다. 이건
  메인넷(Base, chain id 8453) USDC 결제를 실제로 검증·정산할 수 있는
  유일한 경로다. CDP 키는 https://portal.cdp.coinbase.com 에서 발급받는다.
- 키가 없으면 Coinbase가 운영하는 공개 테스트넷 파실리테이터
  (`https://x402.org/facilitator`)로 자동 대체된다. 이건 무료지만
  **Base Sepolia 테스트넷 결제만 검증**할 수 있어서, 이 경우
  `X402_NETWORK` 설정과 무관하게 네트워크를 강제로 테스트넷으로
  전환한다 - 실제 메인넷 USDC는 이 상태에서 정산되지 않는다.

## 알아두어야 할 점 (정직하게 밝혀둠)
- 이 프로젝트를 작성한 샌드박스 환경은 외부 네트워크가 막혀 있어 `x402`/`cdp-sdk`
  패키지를 실제로 설치·실행해보지 못했다. 공식 문서(docs.x402.org,
  docs.cdp.coinbase.com, PyPI x402 README)에서 확인한 API 모양대로 작성했지만,
  `RouteConfig`가 Bazaar 메타데이터(서비스명/태그/아이콘 등)를 위한 추가
  파라미터를 지원하는지는 문서에서 완전히 확인하지 못했다. 로컬에서
  `python -c "from x402.http.types import RouteConfig; help(RouteConfig)"`로
  실제 설치된 버전의 필드를 확인하고, 필요하면 `build_routes()`에 채워 넣으면 된다.
- x402 패키지 버전에 따라 import 경로가 바뀔 수 있다 (이 파일은 2.21.0 기준).
  `pip install "x402[fastapi]" "cdp-sdk"` 실행 후 import 에러가 나면, 설치된
  버전의 실제 모듈 경로를 확인해서 맞춰야 한다.
"""
import logging

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

from app.config import settings

logger = logging.getLogger("alphapipeline")

# 테스트넷 파실리테이터(x402.org/facilitator)는 Base Sepolia만 검증 가능하다.
TESTNET_FALLBACK_NETWORK = "eip155:84532"  # Base Sepolia

USE_CDP_FACILITATOR = bool(settings.CDP_API_KEY_ID and settings.CDP_API_KEY_SECRET)

# CDP 키 유무에 따라 실제로 결제를 검증할 네트워크 (라우트 등록/가격 표시에 공용으로 사용)
ACTIVE_NETWORK = settings.X402_NETWORK if USE_CDP_FACILITATOR else TESTNET_FALLBACK_NETWORK


def _build_facilitator_client() -> HTTPFacilitatorClient:
    if USE_CDP_FACILITATOR:
        # cdp-sdk가 CDP_API_KEY_ID / CDP_API_KEY_SECRET 환경변수를 직접 읽어서
        # 인증된 파실리테이터 설정을 만들어준다. main.py의 load_dotenv()가 먼저
        # 실행되어 이 값들이 이미 os.environ에 들어있어야 한다.
        from cdp.x402 import create_facilitator_config

        logger.info(
            "CDP Facilitator(메인넷, %s)로 x402 결제를 검증/정산합니다.", settings.X402_NETWORK
        )
        return HTTPFacilitatorClient(create_facilitator_config())

    logger.warning(
        "CDP_API_KEY_ID/CDP_API_KEY_SECRET이 설정되지 않아 공개 테스트넷 파실리테이터"
        "(x402.org/facilitator, %s)로 대체합니다. 이 상태로는 실제 메인넷 USDC 결제가 "
        "정산되지 않습니다 - 실서비스 전에는 반드시 CDP Facilitator를 쓰도록 CDP 키를 넣으세요.",
        TESTNET_FALLBACK_NETWORK,
    )
    return HTTPFacilitatorClient(FacilitatorConfig(url="https://x402.org/facilitator"))


def build_resource_server() -> x402ResourceServer:
    """PaymentMiddlewareASGI에 넘길 x402ResourceServer를 조립한다 (앱 시작 시 1회 호출)."""
    server = x402ResourceServer(_build_facilitator_client())
    server.register(ACTIVE_NETWORK, ExactEvmServerScheme())
    return server


def _payment_option() -> PaymentOption:
    return PaymentOption(
        scheme="exact",
        pay_to=settings.RECEIVER_WALLET_ADDRESS,
        price=f"${settings.PRICE_PER_CALL_USDC}",
        network=ACTIVE_NETWORK,
    )


def build_routes(dump_risk_enabled: bool) -> dict[str, RouteConfig]:
    """
    PaymentMiddlewareASGI에 넘길 라우트별 결제 스펙.
    여기 등록된 "METHOD /path" 조합만 결제가 필요해지고, 등록되지 않은 라우트는
    미들웨어를 그냥 통과한다 - dump_risk_enabled=False일 때 dump-risk를 이 dict에서
    빼두면, 그 요청은 결제 검사 없이 바로 핸들러로 가서 기존 503 "coming_soon"
    응답을 내보낸다 (호출자에게 과금되지 않음 - 기존 정책 그대로 유지).
    """
    option = _payment_option()
    routes: dict[str, RouteConfig] = {
        "GET /v1/market/kimchi-alert": RouteConfig(
            accepts=[option],
            mime_type="application/json",
            description="업비트 vs 바이낸스 김치프리미엄 실시간 계산 및 1시간 급변/역프 감지",
        ),
        "GET /v1/tools/ai-markdown": RouteConfig(
            accepts=[option],
            mime_type="application/json",
            description="임의의 웹페이지 URL을 광고/네비게이션 없는 AI 친화적 순수 마크다운으로 변환",
        ),
    }
    if dump_risk_enabled:
        routes["GET /v1/unlocks/dump-risk"] = RouteConfig(
            accepts=[option],
            mime_type="application/json",
            description="D-7 이내 유통량 3% 이상 락업 해제 이벤트와 거래량 대비 매도 충격 위험도",
        )
    return routes
