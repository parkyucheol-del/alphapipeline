"""
API에게 결제 신호를 보내는 예제 클라이언트 (x402 공식 SDK 사용).

이 스크립트는 x402 프로토콜을 통해 AlphaPipeline의 유료 엔드포인트를 실제로
호출하고 결제까지 진행하는 예시다. 결제는 EIP-3009 서명(transferWithAuthorization)
방식이라 온체인 트랜잭션을 직접 브로드캐스트하지 않고, x402 SDK가 서명 생성 →
서버 재요청 → 정산까지 자동으로 처리한다.

사용 전 준비물:
  pip install "x402[https]" eth_account httpx
  아래 둘 중 하나를 환경변수로 설정:
    - EVM_PRIVATE_KEY: 결제를 보낼 지갑의 raw 개인키 (0x로 시작하는 64자리 hex)
    - EVM_MNEMONIC: 니모닉(복구구문, 보통 12/24 단어) - 모바일 지갑 앱(Uniswap
      Wallet 등)은 대부분 raw 개인키 대신 니모닉만 보여주므로 이 경로가 필요함.
      니모닉에서 파생되는 계정은 표준 EVM HD path m/44'/60'/0'/0/0을 사용한다
      (대부분의 지갑 앱이 첫 번째 계정에 쓰는 경로와 동일).
  둘 다 없으면 결제 없이 402 응답만 확인하는 드라이런으로 동작한다.

  - 실제 결제가 나가는 곳은 CDP Facilitator(메인넷, eip155:8453)이므로 진짜
    USDC가 소액(엔드포인트별 단가) 차감된다. 테스트넷(Base Sepolia)으로 시험해보고
    싶다면 서버 쪽 CDP 설정을 그쪽으로 바꿔야 한다 - 이 스크립트 자체는 서버가
    반환하는 결제 조건을 그대로 따른다.

호출 대상 엔드포인트는 아래 ENDPOINT 상수로 바꿀 수 있다 (기본값: kimchi-alert,
가장 가격이 저렴하고 안정적으로 동작 확인된 엔드포인트).
"""
import asyncio
import os

from eth_account import Account

from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

API_BASE = os.getenv("ALPHAPIPELINE_API_BASE", "http://localhost:8000")
ENDPOINT = "/v1/dex/liquidity-slippage?network=base&trade_size_usd=5000&pool_address=0x6c561b446416e1a00e8e93e221854d6ea4171372"

MNEMONIC_HD_PATH = "m/44'/60'/0'/0/0"


def _load_account() -> Account | None:
    """EVM_PRIVATE_KEY 또는 EVM_MNEMONIC 중 있는 걸로 계정을 만든다. 둘 다 없으면 None."""
    private_key = os.getenv("EVM_PRIVATE_KEY")
    if private_key:
        return Account.from_key(private_key)

    mnemonic = os.getenv("EVM_MNEMONIC")
    if mnemonic:
        # 모바일 키보드 자동대문자화 등으로 니모닉 단어가 대문자로 들어오는 경우가
        # 있어서(실제로 겪었던 이슈), 파싱 전에 소문자로 정규화한다.
        normalized_mnemonic = mnemonic.strip().lower()
        Account.enable_unaudited_hdwallet_features()
        return Account.from_mnemonic(normalized_mnemonic, account_path=MNEMONIC_HD_PATH)

    return None


async def call_paid_endpoint() -> None:
    account = _load_account()

    if account is None:
        print(
            "EVM_PRIVATE_KEY 또는 EVM_MNEMONIC 중 하나가 설정되지 않았습니다. "
            "결제 없이 402 응답만 확인합니다 (402가 나오면 프로토콜은 정상 동작 중)."
        )
        import httpx

        async with httpx.AsyncClient() as http:
            resp = await http.get(f"{API_BASE}{ENDPOINT}")
            print(f"상태 코드: {resp.status_code}")
            print(resp.text[:1000])
            return

    print(f"사용 지갑 주소: {account.address}")

    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))
    http_client = x402HTTPClient(client)

    async with x402HttpxClient(client) as http:
        response = await http.get(f"{API_BASE}{ENDPOINT}")
        await response.aread()

        print(f"상태 코드: {response.status_code}")
        print(f"본문: {response.text}")

        if response.is_success:
            settle_response = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print(f"결제 정산 결과: {settle_response}")


if __name__ == "__main__":
    asyncio.run(call_paid_endpoint())
