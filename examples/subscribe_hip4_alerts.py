"""
HIP-4 확률 급변 웹훅 구독을 실제로 결제해서 걸어보는 예제 클라이언트.

examples/client_example.py와 거의 동일한 패턴(x402 공식 SDK로 실제 결제까지
진행)이고, 차이는 쿼리 파라미터(webhook_url/secret 등)를 같이 보낸다는 점뿐이다.

2026-09-30: 원래는 POST + JSON body였는데, CDP Facilitator가 POST 기반 x402
리소스의 결제 검증을 지원하지 않는 것으로 확인되어(여러 페이로드 변형을
다 테스트해봤지만 동일하게 거부됨, GET 라우트는 가격에 상관없이 항상 성공)
GET + 쿼리 파라미터 방식으로 바꿨다 (app/payment.py의 build_routes() 주석
참고). secret이 URL 쿼리스트링에 실리게 되는 트레이드오프가 있다 - HTTPS라
전송 중엔 암호화되지만, 서버/CDN 접근 로그에는 남을 수 있다.

사용 전 준비물 (client_example.py와 동일):
  pip install "x402[https]" eth_account httpx
  아래 둘 중 하나를 환경변수로 설정:
    - EVM_PRIVATE_KEY: 결제를 보낼 지갑의 raw 개인키 (0x로 시작하는 64자리 hex)
    - EVM_MNEMONIC: 니모닉(복구구문) - 표준 HD path m/44'/60'/0'/0/0 사용
  둘 다 없으면 결제 없이 402 응답만 확인하는 드라이런으로 동작한다.

  - 실제 결제가 나가는 곳은 CDP Facilitator(메인넷, eip155:8453)이므로 진짜
    USDC가 차감된다(기본 $0.05). 이 스크립트는 개인키/니모닉을 절대 화면에
    출력하지 않고, 환경변수에서 읽기만 한다 - 터미널에 직접 입력하고, 다른
    곳(채팅 등)에는 절대 붙여넣지 말 것.

그리고 이 구독 전용으로 추가 환경변수 2개가 필요하다:
    - HIP4_WEBHOOK_URL: 알림을 받을 https:// 웹훅 주소
    - HIP4_WEBHOOK_SECRET: 서명 검증용 비밀키 (16자 이상, 직접 생성 -
      PowerShell 예시: `-join ((48..57)+(65..90)+(97..122)|Get-Random -Count 32|%{[char]$_})`)

호출 대상 서버는 ALPHAPIPELINE_API_BASE로 바꿀 수 있다 (기본값: localhost:8000 -
실서비스에 걸려면 https://alphapipeline-eu.onrender.com 로 지정할 것).
"""
import asyncio
import base64
import json
import os

from eth_account import Account

from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

API_BASE = os.getenv("ALPHAPIPELINE_API_BASE", "http://localhost:8000")
ENDPOINT = "/v1/prediction/hip4-alerts/subscribe"

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


def _load_subscribe_query() -> dict:
    webhook_url = os.getenv("HIP4_WEBHOOK_URL")
    secret = os.getenv("HIP4_WEBHOOK_SECRET")
    if not webhook_url or not secret:
        raise SystemExit(
            "HIP4_WEBHOOK_URL / HIP4_WEBHOOK_SECRET 환경변수를 먼저 설정하세요 "
            "(secret은 16자 이상, 직접 생성한 임의 문자열)."
        )
    if len(secret) < 16:
        raise SystemExit("HIP4_WEBHOOK_SECRET은 16자 이상이어야 합니다.")
    return {"webhook_url": webhook_url, "secret": secret}


async def subscribe() -> None:
    query = _load_subscribe_query()
    account = _load_account()

    if account is None:
        print(
            "EVM_PRIVATE_KEY 또는 EVM_MNEMONIC 중 하나가 설정되지 않았습니다. "
            "결제 없이 402 응답만 확인합니다 (402가 나오면 프로토콜은 정상 동작 중)."
        )
        import httpx

        async with httpx.AsyncClient() as http:
            resp = await http.get(f"{API_BASE}{ENDPOINT}", params=query)
            print(f"상태 코드: {resp.status_code}")
            print(resp.text[:1000])
            return

    print(f"사용 지갑 주소: {account.address}")

    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))
    http_client = x402HTTPClient(client)

    async with x402HttpxClient(client) as http:
        response = await http.get(f"{API_BASE}{ENDPOINT}", params=query)
        await response.aread()

        print(f"상태 코드: {response.status_code}")
        print(f"본문: {response.text}")
        print("응답 헤더:")
        for key, value in response.headers.items():
            print(f"  {key}: {value}")

        debug_header = response.headers.get("x-debug-payload-sent")
        if debug_header:
            try:
                decoded = json.loads(base64.b64decode(debug_header))
                print("\n[디버그] 서버가 CDP Facilitator에 실제로 보낸 payload/requirements:")
                print(json.dumps(decoded, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"\n[디버그] X-Debug-Payload-Sent 디코딩 실패: {e}")

        if response.is_success:
            settle_response = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print(f"결제 정산 결과: {settle_response}")
        else:
            print(
                "\n결제가 완료되지 못했습니다. 위 헤더에 원인(예: 잔액 부족, "
                "네트워크 불일치)이 담겨 있을 수 있습니다."
            )


if __name__ == "__main__":
    asyncio.run(subscribe())
