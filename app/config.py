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
    PRICE_PER_CALL_USDC: float = float(os.getenv("PRICE_PER_CALL_USDC", "0.01"))

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

    # 김치프리미엄/시세 캐시 TTL(초) - 짧을수록 실시간성은 올라가지만
    # 업비트/바이낸스 API 호출 빈도가 늘어남. 2초면 봇의 연타 호출을 막으면서도
    # 사람이 체감하기엔 사실상 실시간.
    KIMCHI_CACHE_TTL_SECONDS: int = int(os.getenv("KIMCHI_CACHE_TTL_SECONDS", "2"))

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
