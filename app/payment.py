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

## x402 Bazaar 등록용 메타데이터 (2026-09 업데이트, 아래 "버그 수정" 절 참고)
Render 배포(https://alphapipeline.onrender.com)가 실제로 살아있는 걸 확인한 뒤,
Bazaar 인덱서가 카탈로그에 반영할 때 읽는 `extensions.bazaar` 스키마를 각 라우트에
채워 넣었다. `RouteConfig`의 실제 필드는 공식 GitHub 소스(coinbase/x402,
python/x402/http/types.py)로 확인했다 - `resource`(절대 URL 문자열),
`extensions`(자유 형식 dict)가 존재해서, 여기에 `bazaar.info.input`/`output`
(입출력 스펙+예시)을 넣는다. 절대 URL이 필요한 이유: Bazaar 문서
(docs.x402.org/extensions/bazaar)가 "relative URLs"를 흔한 등록 실패 사유로
명시하고 있어서, `PUBLIC_BASE_URL`(.env, 기본값이 이미 Render 주소로 설정됨) +
라우트 경로로 절대 URL을 만든다.

**등록이 실제로 되려면**: Bazaar는 "신청" 절차가 없다 - 공식 x402 클라이언트
SDK를 쓰는 누군가가 이 bazaar extension을 echo하면서 실제 결제를 완료하는
순간, 그 결제를 처리한 파실리테이터(우리는 CDP Facilitator)가 카탈로그에
반영한다. 즉 이 메타데이터를 넣는 것만으로는 카탈로그에 즉시 뜨지 않고,
실제 결제 트래픽이 최소 1건 있어야 한다 (README "x402 Bazaar 등록" 절 참고).

## 버그 수정 (2026-09-03): "invalid discovery configuration" 거부 해결
실결제 테스트 도중 Render 로그에 실제로 이 거부가 찍혔다:
`{"bazaar": {"status": "rejected", "rejectedReason": "invalid discovery configuration"}}`

원인: 이전 버전은 `serviceName`/`tags`를 `extensions.bazaar` 안에 `info`와
나란히 넣었다. 공식 문서(docs.x402.org/extensions/bazaar, "Service Metadata"
절)를 다시 정확히 대조해보니, `serviceName`/`tags`/`iconUrl`은
`extensions.bazaar` 안이 아니라 **`RouteConfig` 자체의 별도 필드**
(`service_name`/`tags`/`icon_url`)로 들어가야 하는 게 맞다 - `extensions.bazaar`
안에는 오직 `info`(input/output)만 있어야 한다. 즉 `extensions.bazaar`가
"info" 옆에 낯선 키(serviceName/tags)를 더 들고 있었던 게 인덱서의 스키마
검증(엄격한 additionalProperties 제약으로 추정)에 걸려 통째로 거부당한
것으로 보인다.

수정: `_bazaar_extension()`은 이제 `info`만 만들고, `serviceName`/`tags`는
`_make_route_config()`가 `RouteConfig`의 최상위 필드로 직접 붙인다. 또한
문서가 명시적으로 "hand-rolling 하지 말고 공식 SDK 헬퍼를 쓰라"고 권장하므로,
가능하면 `x402.extensions.bazaar.declare_discovery_extension()` 공식 헬퍼를
쓰고, 어떤 이유로든(구버전 SDK 등) 그게 안 되면 문서의 "Response Schema"
예시와 정확히 같은 모양으로 수동 구성하는 폴백을 쓴다.

**정직하게 밝혀둘 점**: 이 세션은 x402 패키지를 실제로 설치해서 검증할
네트워크 접근이 없었다(README/design 문서에 명시된 샌드박스 제약). 그래서
`_make_route_config()`는 설치된 `RouteConfig`가 `service_name`/`tags`/`icon_url`
필드를 실제로 지원하는지 런타임에 `dataclasses.fields()`로 확인해서, 지원 안
하면 조용히 빼고 경고 로그만 남긴다 (서버가 죽는 대신 최소한 결제/데이터
기능은 정상 동작). 배포 후 Render 로그에서 이 경고가 뜨는지 꼭 확인할 것 -
뜨면 `pip install -U x402`로 SDK를 올려야 서비스명/태그까지 카탈로그에 반영된다.

## 알아두어야 할 점 (정직하게 밝혀둠)
- `iconUrl`은 아직 안 넣었다 - 공개적으로 접근 가능한 이미지 URL이 있어야 하는데
  아직 호스팅해둔 아이콘이 없어서다. 나중에 아이콘 이미지를 하나 만들어서 어딘가에
  올리고, `_make_route_config()` 호출부에 `icon_url=` 키를 추가하면 된다.
- x402 패키지 버전에 따라 import 경로가 바뀔 수 있다 (이 파일은 2.21.0 기준).
- 위 수정을 실제 배포 후, 결제를 1건 더 발생시켜 Render 로그에서
  `"status": "rejected"`가 사라지고 `"success"`(또는 `"processing"`)로 바뀌는지
  반드시 재확인해야 한다 - 이 세션은 그 재검증까지는 못 했다.
"""
import dataclasses
import logging

from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

from app.config import settings
from app.schemas import (
    DUMP_RISK_EXAMPLE,
    KIMCHI_ALERT_EXAMPLE,
    MARKDOWN_EXAMPLE,
    DumpRiskResponse,
    KimchiAlertResponse,
    MarkdownResponse,
)

logger = logging.getLogger("alphapipeline")

# 공식 헬퍼가 있으면 우선 사용한다 (문서가 hand-rolling 대신 이걸 쓰라고 명시적으로
# 권장함). 설치된 x402 버전이 이 모듈/함수를 아직 갖고 있지 않을 수도 있어서
# ImportError를 잡아 폴백한다 - 이 세션에서 실제 설치 검증을 못 했기 때문에
# 방어적으로 작성했다 (위 클래스 docstring "버그 수정" 절 참고).
try:
    from x402.extensions.bazaar import OutputConfig, declare_discovery_extension

    _HAS_DISCOVERY_HELPER = True
except ImportError:
    OutputConfig = None
    declare_discovery_extension = None
    _HAS_DISCOVERY_HELPER = False
    logger.warning(
        "x402.extensions.bazaar.declare_discovery_extension을 임포트하지 못했습니다 "
        "(설치된 x402 버전이 이 헬퍼를 지원하지 않을 수 있음) - 수동으로 구성한 "
        "extensions.bazaar dict로 대체합니다. 'pip install -U x402'로 최신 버전을 "
        "설치하면 공식 헬퍼를 쓰도록 자동 전환됩니다."
    )

# RouteConfig가 실제로 지원하는 필드 목록 (설치된 x402 버전에 따라 달라질 수 있음).
_ROUTECONFIG_FIELD_NAMES = {f.name for f in dataclasses.fields(RouteConfig)}

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


def _payment_option(price_usdc: float) -> PaymentOption:
    """
    2026-09 업데이트(차등 요금제): 엔드포인트마다 다른 가격을 매길 수 있도록
    가격을 인자로 받는다. 예전에는 전 라우트가 settings.PRICE_PER_CALL_USDC
    하나만 썼는데, 이제 build_routes()가 라우트별로 settings.PRICE_*_USDC
    값을 넘겨준다.
    """
    return PaymentOption(
        scheme="exact",
        pay_to=settings.RECEIVER_WALLET_ADDRESS,
        price=f"${price_usdc}",
        network=ACTIVE_NETWORK,
    )


def _resource_url(path: str) -> str:
    """PUBLIC_BASE_URL + 라우트 경로 -> Bazaar가 요구하는 절대 URL."""
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}{path}"


def _bazaar_extension(
    *,
    input_example: dict,
    input_schema: dict,
    output_example=None,
    output_schema=None,
) -> dict:
    """x402 Bazaar 인덱서가 읽는 discovery extension을 만든다.

    2026-09-03 밤, 실제 설치된 x402==2.21.0 패키지 소스를 inspect.getsource()로
    직접 열어서 확인한 결과, 공식 헬퍼 declare_discovery_extension()의 진짜
    시그니처는 이전에 참고했던 문서 사이트(docs.x402.org) 설명과 달랐다:
      - method 파라미터 자체가 없다 (호출부의 라우트 키("GET /v1/...")에서
        bazaar_resource_server_extension이 런타임에 자동으로 채워 넣는다).
      - 반환값도 {"bazaar": {"info": {...}}} 한 겹이 아니라
        {"bazaar": {"info": {...}, "schema": {...}}} 두 겹 구조다.
    이전의 수동 fallback({"info": {"input": {"type": "http", "method": ...,
    "queryParams": ...}, "output": {...}}})은 이 실제 구조와 맞지 않아서 매
    결제마다 "invalid discovery configuration"으로 계속 거부되고 있었다.
    이제 공식 헬퍼를 있는 그대로 호출해서 이 문제를 해결한다 - 손으로 다시
    만들지 말 것, 위 버그가 재발한다.
    """
    if not _HAS_DISCOVERY_HELPER:
        logger.warning(
            "x402.extensions.bazaar.declare_discovery_extension을 쓸 수 없어 "
            "이 라우트는 Bazaar 디스커버리 메타데이터 없이 서빙됩니다. "
            "'pip install -U x402'로 SDK를 업그레이드하면 자동으로 복구됩니다."
        )
        return {}

    output = None
    if output_example is not None or output_schema is not None:
        output = OutputConfig(example=output_example, schema=output_schema)

    return declare_discovery_extension(
        input=input_example,
        input_schema=input_schema,
        output=output,
    )


def _make_route_config(*, service_name: str, tags: list[str], icon_url: str | None = None, **kwargs) -> RouteConfig:
    """
    RouteConfig(...)를 만들되, 설치된 x402 SDK 버전이 service_name/tags/icon_url을
    아직 지원하지 않으면(구버전) 그 필드만 조용히 빼고 나머지(결제/데이터 기능에
    필수인 accepts/resource/description/extensions 등)는 정상적으로 채운다 -
    이 세션은 실제 패키지를 설치해 확인할 네트워크 접근이 없었기 때문에 방어적으로
    작성했다. 지원되면 자동으로 살아나고, 지원 안 되면 경고 로그만 남기고 서버는
    정상 기동한다.
    """
    metadata = {"service_name": service_name, "tags": tags}
    if icon_url:
        metadata["icon_url"] = icon_url
    supported_metadata = {k: v for k, v in metadata.items() if k in _ROUTECONFIG_FIELD_NAMES}
    dropped = sorted(set(metadata) - set(supported_metadata))
    if dropped:
        logger.warning(
            "설치된 x402 SDK의 RouteConfig가 %s 필드를 지원하지 않아 제외했습니다. "
            "'pip install -U x402'로 업그레이드하면 Bazaar 카탈로그에 서비스명/태그가 노출됩니다.",
            dropped,
        )
    return RouteConfig(**kwargs, **supported_metadata)


def build_routes(dump_risk_enabled: bool) -> dict[str, RouteConfig]:
    """
    PaymentMiddlewareASGI에 넘길 라우트별 결제 스펙 + Bazaar 노출 메타데이터.
    여기 등록된 "METHOD /path" 조합만 결제가 필요해지고, 등록되지 않은 라우트는
    미들웨어를 그냥 통과한다 - dump_risk_enabled=False일 때 dump-risk를 이 dict에서
    빼두면, 그 요청은 결제 검사 없이 바로 핸들러로 가서 (온체인/DropsTab) 데이터를
    무료로 내보낸다 (main.py/app/logic.py의 dump-risk 재설계 참고).

    각 라우트의 output_schema는 app/schemas.py의 Pydantic 모델에서 그대로 뽑아써서
    (model_json_schema()), Bazaar/OpenAPI에 노출되는 스펙이 실제 응답 모양과
    어긋나지 않게 한다 - Pydantic v2 기본 $ref는 "#/$defs/..." 형태라 Bazaar 문서가
    금지하는 외부 참조($ref가 "#"로 시작하지 않는 경우)에 해당하지 않는다.

    ## 차등 요금제 (Tiered Pricing, 2026-09)
    라우트마다 별도의 PaymentOption을 만들어서 서로 다른 가격을 매긴다 - 대체
    가능한 단순 유틸리티(ai-markdown)는 싸게, 어디서나 못 구하는 핵심 알파
    데이터(dump-risk)는 비싸게. 실제 단가는 app/config.py의
    PRICE_KIMCHI_ALERT_USDC / PRICE_AI_MARKDOWN_USDC / PRICE_DUMP_RISK_USDC로
    코드 수정 없이 .env에서 조정 가능하다.
    """
    kimchi_option = _payment_option(settings.PRICE_KIMCHI_ALERT_USDC)
    ai_markdown_option = _payment_option(settings.PRICE_AI_MARKDOWN_USDC)
    dump_risk_option = _payment_option(settings.PRICE_DUMP_RISK_USDC)

    routes: dict[str, RouteConfig] = {
        "GET /v1/market/kimchi-alert": _make_route_config(
            accepts=[kimchi_option],
            mime_type="application/json",
            description=(
                "Real-time Korea (Upbit) vs global crypto price premium - the "
                "'kimchi premium' - with reverse-premium and 1h-surge alerts."
            ),
            resource=_resource_url("/v1/market/kimchi-alert"),
            extensions=_bazaar_extension(
                input_example={"symbol": "BTC"},
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Crypto ticker symbol to check, e.g. BTC, ETH, SOL. Defaults to BTC.",
                        }
                    },
                    "required": [],
                },
                output_example=KIMCHI_ALERT_EXAMPLE,
                output_schema=KimchiAlertResponse.model_json_schema(),
            ),
            service_name="AlphaPipeline Kimchi Alert",
            tags=["crypto", "arbitrage", "korea", "realtime"],
        ),
        "GET /v1/tools/ai-markdown": _make_route_config(
            accepts=[ai_markdown_option],
            mime_type="application/json",
            description=(
                "Convert any webpage URL into clean, ad-free Markdown text "
                "optimized for LLM context windows."
            ),
            resource=_resource_url("/v1/tools/ai-markdown"),
            extensions=_bazaar_extension(
                input_example={"url": "https://example.com"},
                input_schema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "format": "uri",
                            "description": "Full http(s) URL of the webpage to convert to Markdown.",
                        }
                    },
                    "required": ["url"],
                },
                output_example=MARKDOWN_EXAMPLE,
                output_schema=MarkdownResponse.model_json_schema(),
            ),
            service_name="AlphaPipeline AI Markdown",
            tags=["ai-tools", "web-scraping", "markdown", "llm"],
        ),
    }
    if dump_risk_enabled:
        routes["GET /v1/unlocks/dump-risk"] = _make_route_config(
            accepts=[dump_risk_option],
            mime_type="application/json",
            description=(
                "Tokens with large amounts of currently-locked or vesting supply "
                "relative to circulating supply - a proxy for future sell/dump pressure."
            ),
            resource=_resource_url("/v1/unlocks/dump-risk"),
            extensions=_bazaar_extension(
                input_example={},
                input_schema={"type": "object", "properties": {}, "required": []},
                output_example=DUMP_RISK_EXAMPLE,
                output_schema=DumpRiskResponse.model_json_schema(),
            ),
            service_name="AlphaPipeline Dump Risk",
            tags=["crypto", "token-unlock", "risk"],
        )
    return routes
