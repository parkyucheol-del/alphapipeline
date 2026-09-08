"""
전역 설정 로더.
.env 파일에서 지갑 주소, RPC, 요금 등을 읽어온다.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # 결제 수신 지갑 (Base 체인). 공식 x402 SDK 예제의 X402_PAY_TO와 동일한 역할이며,
    # 기존 .env 값을 그대로 재사용하기 위해 변수명은 유지했다.
    RECEIVER_WALLET_ADDRESS: str = os.getenv(
        "RECEIVER_WALLET_ADDRESS", "0xYOUR_WALLET_ADDRESS_HERE"
    )

    # 호출당 가격 (USD 단위, 사람이 읽는 값. 예: 0.01 -> x402 SDK에 "$0.01"로 전달됨.
    # 메인넷에서는 이 달러 가격이 Base의 기본 USDC로 자동 환산되므로 컨트랙트 주소를
    # 직접 지정할 필요가 없다 - CDP Facilitator가 처리)
    # 2026-09 업데이트: 더 이상 전 엔드포인트에 똑같이 쓰이지 않는다 - 아래
    # PRICE_*_USDC로 엔드포인트별 차등 요금을 매기고, 이 값은 신규 엔드포인트
    # 추가 시 임시 기본값/legacy 폴백으로만 남겨둔다 (app/payment.py 참고).
    PRICE_PER_CALL_USDC: float = float(os.getenv("PRICE_PER_CALL_USDC", "0.01"))

    # ===== 엔드포인트별 차등 요금 (Tiered Pricing, 2026-09) =====
    # "데이터 가치 기반 차등 요금제" - 대체하기 쉬운 단순 유틸리티(ai-markdown)는
    # 싸게, 아무 데서나 못 구하는 핵심 알파 데이터(dump-risk)는 비싸게 매겨서
    # 마진 곡선을 데이터의 실제 가치에 맞춘다. 세 값 다 독립적으로 .env에서
    # 조정 가능 - 가격 실험(A/B, 수요 반응 테스트)을 코드 수정 없이 할 수 있다.
    PRICE_KIMCHI_ALERT_USDC: float = float(os.getenv("PRICE_KIMCHI_ALERT_USDC", "0.01"))
    PRICE_AI_MARKDOWN_USDC: float = float(os.getenv("PRICE_AI_MARKDOWN_USDC", "0.005"))
    PRICE_DUMP_RISK_USDC: float = float(os.getenv("PRICE_DUMP_RISK_USDC", "0.03"))
    PRICE_FUNDING_RATE_USDC: float = float(os.getenv("PRICE_FUNDING_RATE_USDC", "0.01"))
    PRICE_DEX_SLIPPAGE_USDC: float = float(os.getenv("PRICE_DEX_SLIPPAGE_USDC", "0.02"))
    PRICE_MACRO_DDAY_USDC: float = float(os.getenv("PRICE_MACRO_DDAY_USDC", "0.01"))
    PRICE_TOKEN_RISK_USDC: float = float(os.getenv("PRICE_TOKEN_RISK_USDC", "0.02"))
    PRICE_ARB_SPREAD_USDC: float = float(os.getenv("PRICE_ARB_SPREAD_USDC", "0.02"))
    PRICE_FUNDING_APR_USDC: float = float(os.getenv("PRICE_FUNDING_APR_USDC", "0.01"))
    PRICE_CONTRACT_HEALTH_USDC: float = float(os.getenv("PRICE_CONTRACT_HEALTH_USDC", "0.02"))
    PRICE_WHALE_AUDIT_USDC: float = float(os.getenv("PRICE_WHALE_AUDIT_USDC", "0.02"))
    PRICE_TOKEN_DIAGNOSTIC_USDC: float = float(os.getenv("PRICE_TOKEN_DIAGNOSTIC_USDC", "0.03"))
    # 2026-09 예측시장(Polymarket) 확장 Wave 1. neg_risk_arbitrage는 여러 outcome을
    # 동시에 계산하고 실행 가능성(유동성 병목)까지 판정하는 고부가 시그널이라
    # dump-risk/token-diagnostic과 같은 최고가 티어로, exit_capacity_audit은
    # 단일 오더북 조회라 token-risk/dex-slippage와 같은 중간 티어로 매겼다.
    PRICE_NEG_RISK_ARBITRAGE_USDC: float = float(os.getenv("PRICE_NEG_RISK_ARBITRAGE_USDC", "0.03"))
    PRICE_EXIT_CAPACITY_AUDIT_USDC: float = float(os.getenv("PRICE_EXIT_CAPACITY_AUDIT_USDC", "0.02"))

    # ===== x402 공식 결제 레이어 (Coinbase CDP Facilitator) =====
    # Coinbase Developer Platform(https://portal.cdp.coinbase.com)에서 발급받는 API 키.
    # 둘 다 채워지면 CDP Facilitator(메인넷 실결제 검증/정산)를 쓰고,
    # 비어있으면 공개 테스트넷 파실리테이터(x402.org/facilitator)로 자동 대체된다
    # (이 상태에서는 실제 메인넷 USDC가 정산되지 않으니 실서비스 전에 반드시 채워야 함).
    CDP_API_KEY_ID: str = os.getenv("CDP_API_KEY_ID", "")
    CDP_API_KEY_SECRET: str = os.getenv("CDP_API_KEY_SECRET", "")

    # CAIP-2 네트워크 식별자. 기본값은 Base 메인넷(eip155:8453).
    # CDP 키가 없어서 테스트넷 파실리테이터로 대체되는 경우, 이 값과 무관하게
    # Base Sepolia(eip155:84532)로 강제 전환된다 (app/payment.py 참고).
    X402_NETWORK: str = os.getenv("X402_NETWORK", "eip155:8453")

    # 이 서비스가 실제로 서빙되는 공개 절대 URL (프로토콜+도메인, 끝에 / 없이).
    # x402 Bazaar 인덱서는 각 라우트의 절대 URL(RouteConfig.resource)을 요구하고
    # 상대경로는 등록 실패 사유로 명시되어 있어서, Render 배포 주소를 여기 넣는다.
    # (app/payment.py의 build_routes()가 이 값 + 각 라우트 경로로 절대 URL을 만든다)
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "https://alphapipeline.onrender.com")

    # 김치프리미엄/시세 캐시 TTL(초) - 짧을수록 실시간성은 올라가지만
    # 업비트/코인베이스/CoinGecko API 호출 빈도가 늘어남.
    # 2026-09 업데이트: 기존 2초에서 30초로 늘렸다 - 봇들이 초 단위로 같은 심볼을
    # 반복 결제 호출할 때, x402 결제는 요청마다 그대로 징수되면서(캐시와 무관)
    # 외부 API 호출만 캐시 히트만큼 아낄 수 있어 원가가 0에 수렴한다("순마진 100%
    # 방어" - app/cache.py의 ttl_cached() 참고). 김치프리미엄은 30초 안에 급변하는
    # 지표가 아니라 실시간성 손실도 체감상 미미하다.
    KIMCHI_CACHE_TTL_SECONDS: int = int(os.getenv("KIMCHI_CACHE_TTL_SECONDS", "30"))

    # GoPlus token_security 캐시 TTL(초). security.token_risk / contract_health_audit /
    # token_diagnostic 세 유료 엔드포인트가 전부 같은 GoPlus 무료 쿼터를 공유하므로,
    # 같은 (chain_id, contract_address) 조합이 짧은 시간 안에 반복 조회되면(봇의
    # 재시도, token_diagnostic의 내부 asyncio.gather 등) 캐시로 흡수해서 무료
    # 쿼터 소진/레이트리밋으로 인한 502를 방지한다. x402 결제는 캐시 히트와
    # 무관하게 매 호출 그대로 징수되므로 마진에는 영향 없다(app/cache.py 참고).
    GOPLUS_CACHE_TTL_SECONDS: int = int(os.getenv("GOPLUS_CACHE_TTL_SECONDS", "120"))

    # Polymarket CLOB 오더북 캐시 TTL(초). 짧게 잡아서 neg_risk_arbitrage가 여러
    # outcome 레그를 병렬 조회할 때/exit_capacity_audit이 직후 같은 토큰을 다시
    # 조회할 때만 중복 호출을 흡수하고, 오더북 실시간성은 거의 그대로 유지한다.
    POLYMARKET_BOOK_CACHE_TTL_SECONDS: int = int(os.getenv("POLYMARKET_BOOK_CACHE_TTL_SECONDS", "3"))

    # 락업 해제(dump-risk) 스캔 주기(시간). 언락 일정은 몇 주 전에 미리 확정되는
    # 경우가 대부분이라 실시간일 필요가 없음 - 기본값 24시간(하루 1회)이 이미
    # "필요한 최소" 수준이며, 필요하면 이 값만 늘려서 더 낮출 수 있음.
    UNLOCK_REFRESH_INTERVAL_HOURS: int = int(os.getenv("UNLOCK_REFRESH_INTERVAL_HOURS", "24"))

    # dump-risk 엔드포인트를 실제로 서빙할지 여부. DropsTab 유료 플랜(월 $59~)을 결제하기 전까지는
    # False로 두고 나머지 두 무료-원가 엔드포인트(kimchi-alert, ai-markdown)만으로 먼저 출시해서
    # 실제 수요/매출을 검증한 뒤, 검증되면 이 값을 true로 바꾸고 DROPSTAB_API_KEY를 넣어 켠다.
    DUMP_RISK_ENABLED: bool = _get_bool("DUMP_RISK_ENABLED", False)

    # DropsTab API 키 (락업 해제 데이터 소스). Builders Program(무료, 승인제) 신청하거나
    # 유료 Advanced 플랜(월 $59~, Basic $19에는 tokenUnlocks 미포함)으로 발급받아 넣는다. 없으면 dump-risk 엔드포인트는
    # "데이터 소스 미설정" 상태로 빈 결과를 반환한다.
    DROPSTAB_API_KEY: str = os.getenv("DROPSTAB_API_KEY", "")
    DROPSTAB_API_BASE_URL: str = os.getenv("DROPSTAB_API_BASE_URL", "https://public-api.dropstab.com/api/v1")

    # 락업 스캔 시 한 페이지에 몇 개씩, 최대 몇 페이지까지 가져올지
    # (전체 코인 수가 많을 수 있어 과금/시간 보호용으로 상한을 둠)
    DROPSTAB_PAGE_SIZE: int = int(os.getenv("DROPSTAB_PAGE_SIZE", "100"))
    MAX_UNLOCK_SCAN_PAGES: int = int(os.getenv("MAX_UNLOCK_SCAN_PAGES", "20"))

    # 테스트용 결제 우회 스위치 (실서비스에서는 반드시 False로)
    PAYMENT_BYPASS_FOR_TESTING: bool = _get_bool(
        "PAYMENT_BYPASS_FOR_TESTING", True
    )

    PORT: int = int(os.getenv("PORT", "8000"))

    def is_wallet_configured(self) -> bool:
        addr = self.RECEIVER_WALLET_ADDRESS or ""
        return addr.startswith("0x") and len(addr) == 42 and "YOUR_WALLET" not in addr


settings = Settings()
