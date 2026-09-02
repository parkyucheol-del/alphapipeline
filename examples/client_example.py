"""
API를 호출하는 봇/클라이언트 예시 코드 (공식 x402 파이썬 SDK 사용).

이전 버전은 web3.py로 직접 USDC transfer 트랜잭션을 만들고 tx_hash를
X-PAYMENT 헤더에 담아 보내는 자체(비표준) 방식이었다. 서버(app/payment.py)가
공식 CDP Facilitator 기반으로 바뀌면서, 클라이언트도 EIP-3009 서명 기반의
표준 "exact" 결제 스킴을 말해야 하므로 공식 `x402` 클라이언트 SDK로 교체했다.
이 SDK는 402 응답을 감지하고, 필요한 서명을 만들고, 결제 헤더를 붙여 자동으로
재시도해준다 - 아래 코드에는 그 흐름을 직접 구현하는 코드가 없다.

실행 전 준비물:
  pip install "x402[httpx]" eth_account
  EVM_PRIVATE_KEY 환경변수에 결제를 보낼 지갑의 개인키 설정
    - 서버가 CDP Facilitator(메인넷)로 떠 있으면 실제 메인넷 USDC가 필요합니다.
    - 서버가 CDP 키 없이(테스트넷 파실리테이터) 떠 있으면 Base Sepolia
      테스트넷 지갑/테스트 USDC로 시험해볼 수 있습니다.

주의: 이 파일도 실제 네트워크가 막힌 샌드박스에서 작성되어 실행 테스트는
못 해봤습니다 - 로컬에서 pip install 후 직접 돌려서 확인해주세요.
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
ENDPOINT = "/v1/market/kimchi-alert?symbol=BTC"


async def call_paid_endpoint() -> None:
    private_key = os.getenv("EVM_PRIVATE_KEY")
    if not private_key:
        print(
            "EVM_PRIVATE_KEY가 설정되지 않았습니다. "
            "결제 없이 그냥 호출해보고 서버가 402를 돌려주는지만 확인합니다."
        )
        import httpx

        async with httpx.AsyncClient() as http:
            resp = await http.get(f"{API_BASE}{ENDPOINT}")
        print(f"상태 코드: {resp.status_code}")
        print(resp.text[:1000])
        return

    client = x402Client()
    account = Account.from_key(private_key)
    register_exact_evm_client(client, EthAccountSigner(account))
    http_client = x402HTTPClient(client)

    async with x402HttpxClient(client) as http:
        response = await http.get(f"{API_BASE}{ENDPOINT}")
        await response.aread()

        print(f"상태 코드: {response.status_code}")
        print(f"응답: {response.text}")

        if response.is_success:
            settle_response = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print(f"결제 정산 결과: {settle_response}")


if __name__ == "__main__":
    asyncio.run(call_paid_endpoint())
