"""
API에게 결제 신호를 보내는 예제 클라이언트 (x402 공식 SDK 사용).

`examples/test_macro_dday.py`를 복제해서 ENDPOINT만 신규 엔드포인트
(/v1/prediction/hip4-price-ladder)로 바꾼 1회성 테스트 스크립트다.
hip4-snapshot/hip4-alerts 때와 동일하게, CDP Facilitator가 이 라우트의
실제 결제 정산(verify+settle)을 정상 처리하는지 확인하는 용도 - 402 응답
헤더만 보는 것으로는 이게 검증되지 않는다(2026-09-30 CDP 설명문 길이 버그가
바로 이 차이 때문에 두 세션 동안 안 잡혔던 사례 참고).

사용 전 준비물은 client_example.py / test_macro_dday.py와 동일:
  pip install "x402[https,evm]" eth_account httpx
  아래 둘 중 하나를 환경변수로 설정:
    - EVM_PRIVATE_KEY: 결제를 보낼 지갑의 raw 개인키 (0x로 시작하는 64자리 hex)
    - EVM_MNEMONIC: 니모닉(복구구문)
  둘 다 없으면 결제 없이 402 응답만 확인하는 드라이런으로 동작한다.

실행:
  python examples\\test_hip4_price_ladder.py
"""
import asyncio
import os

from eth_account import Account

from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

API_BASE = os.getenv("ALPHAPIPELINE_API_BASE", "https://alphapipeline-eu.onrender.com")
ENDPOINT = "/v1/prediction/hip4-price-ladder?underlying=BTC"

MNEMONIC_HD_PATH = "m/44'/60'/0'/0/0"


def _load_account() -> Account | None:
    """EVM_PRIVATE_KEY 또는 EVM_MNEMONIC 중 있는 걸로 계정을 만든다. 둘 다 없으면 None."""
    private_key = os.getenv("EVM_PRIVATE_KEY")
    if private_key:
        return Account.from_key(private_key)

    mnemonic = os.getenv("EVM_MNEMONIC")
    if mnemonic:
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

    # httpx's default timeout (5s for connect/read/write/pool each) is too
    # tight for this route's first real call: cold hip4_cache (TTL 15s) means
    # a fresh Hyperliquid outcomeMeta+allMids fetch (~205 outcomes) plus the
    # CDP Facilitator settle() round trip, both inside the same request. Give
    # it real headroom (60s) instead of risking a client-side ReadTimeout
    # that looks like a payment failure but isn't.
    async with x402HttpxClient(client, timeout=60.0) as http:
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
