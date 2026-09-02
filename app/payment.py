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

## x402 Bazaar 등록용 메타데이터 (2026-09 업데이트)
Render 배포(https://alphapipeline.onrender.com)가 실제로 살아있는 걸 확인한 뒤,
Bazaar 인덱서가 카탈로그에 반영할 때 읽는 `extensions.bazaar` 스키마를 각 라우트에
채워 넣었다. `RouteConfig`의 실제 필드는 공식 GitHub 소스(coinbase/x402,
python/x402/http/types.py)로 확인했다 - `resource`(절대 URL 문자열),
`extensions`(자유 형식 dict)가 존재해서, 여기에 `bazaar.info.input`/`output`
(입출력 스펙+예시)과 `bazaar.serviceName`/`tags`를 넣는다. 절대 URL이 필요한
이유: Bazaar 문서(docs.x402.org/extensions/bazaar)가 "relative URLs"를 흔한
등록 실패 사유로 명시하고 있어서, `PUBLIC_BASE_URL`(.env, 기본값이 이미 Render
주소로 설정됨) + 라우트 경로로 절대 URL을 만든다.

**등록이 실제로 되려면**: Bazaar는 "신청" 절차가 없다 - 공식 x402 클라이언트
SDK를 쓰는 누군가가 이 bazaar extension을 echo하면서 실제 결제를 완료하는
순간, 그 결제를 처리한 파실리테이터(우리는 CDP Facilitator)가 카탈로그에
반영한다. 즉 이 메타데이터를 넣는 것만으로는 카탈로그에 즉시 뜨지 않고,
실제 결제 트래픽이 최소 1건 있어야 한다 (README "x402 Bazaar 등록" 절 참고).

## 알아두어야 할 점 (정직하게 밝혀둠)
- `iconUrl`은 아직 안 넣었다 - 공개적으로 접근 가능한 이미지 URL이 있어야 하는데
  아직 호스팅해둔 아이콘이 없어서다. 나중에 아이콘 이미지를 하나 만들어서 어딘가에
  올리고, `_bazaar_extension()`에 `iconUrl` 키를 추가하면 된다.
- `extensions.bazaar` 안에 `serviceName`/`tags`를 나란히 두는 배치는 공식 예시
  JSON(docs.x402.org/extensions/bazaar)에서 "info" 옆에 있는 걸 보고 그대로 따른
  것인데, 문서에 있는 두 번째 예시는 이 필드들을 `resource`라는 별도 객체 안에도
  보여주고 있어서 정확한 스펙 버전에 따라 위치가 다를 수 있다. 실제 카탈로그 반영
  결과를 보고 필요하면 위치를 조정해야 할 수 있다.
- x402 패키지 버전에 따라 import 경로가 바뀔 수 있다 (이 파일은 2.21.0 기준).
"""
import logging

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

from app.config import settings
from app.schemas import DUMP_RISK_EXAMPLE, KIMCHI_ALERT_EXAMPLE, MARKDOWN_EXAMPLE

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


def _resource_url(path: str) -> str:
    """PUBLIC_BASE_URL + 라우트 경로 -> Bazaar가 요구하는 절대 URL."""
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"


def _bazaar_extension(
    *,
    method: str,
    query_params: dict,
    output_example: dict,
    service_name: str,
    tags: list[str],
) -> dict:
    """
    x402 Bazaar 인덱서가 읽는 extensions.bazaar 스키마 (docs.x402.org/extensions/bazaar
    의 공식 예시 구조를 그대로 따름). serviceName은 32자, tags는 최대 5개(각 32자)
    이내의 출력 가능한 ASCII 문자만 쓰라는 게 공식 제약이라, 호출부에서 지키도록 한다.
    """
    return {
        "bazaar": {
            "info": {
                "input": {
                    "type": "http",
                    "method": method,
                    "queryParams": query_params,
                },
                "output": {
                    "type": "json",
                    "example": output_example,
                },
            },
            "serviceName": service_name,
            "tags": tags,
        }
    }


def build_routes(dump_risk_enabled: bool) -> dict[str, RouteConfig]:
    """
    PaymentMiddlewareASGI에 넘길 라우트별 결제 스펙 + Bazaar 노출 메타데이터.
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
            resource=_resource_url("/v1/market/kimchi-alert"),
            extensions=_bazaar_extension(
                method="GET",
                query_params={"symbol": "BTC"},
                output_example=KIMCHI_ALERT_EXAMPLE,
                service_name="AlphaPipeline Kimchi Alert",
                tags=["crypto", "arbitrage", "korea", "realtime"],
            ),
        ),
        "GET /v1/tools/ai-markdown": RouteConfig(
            accepts=[option],
            mime_type="application/json",
            description="임의의 웹페이지 URL을 광고/네비게이션 없는 AI 친화적 순수 마크다운으로 변환",
            resource=_resource_url("/v1/tools/ai-markdown"),
            extensions=_bazaar_extension(
                method="GET",
                query_params={"url": "https://example.com"},
                output_example=MARKDOWN_EXAMPLE,
                service_name="AlphaPipeline AI Markdown",
                tags=["ai-tools", "web-scraping", "markdown", "llm"],
            ),
        ),
    }
    if dump_risk_enabled:
        routes["GET /v1/unlocks/dump-risk"] = RouteConfig(
            accepts=[option],
            mime_type="application/json",
            description="D-7 이내 유통량 3% 이상 락업 해제 이벤트와 거래량 대비 매도 충격 위험도",
            resource=_resource_url("/v1/unlocks/dump-risk"),
            extensions=_bazaar_extension(
                method="GET",
                query_params={},
                output_example=DUMP_RISK_EXAMPLE,
                service_name="AlphaPipeline Dump Risk",
                tags=["crypto", "token-unlock", "risk"],
            ),
        )
    return routes
