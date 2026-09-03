"""
MCP tools/call 경로로 실제 x402 결제까지 진행하는 예제 클라이언트.

examples/client_example.py(REST GET 엔드포인트용)와 완전히 동일한 결제
서명/재시도/정산 로직(x402 공식 SDK - x402Client/EthAccountSigner/
x402HttpxClient)을 그대로 재사용한다 - 다른 점은 GET REST 경로 대신
POST /mcp에 JSON-RPC tools/call 본문을 보낸다는 것뿐이다. x402HttpxClient는
GET/POST 등 메서드에 상관없이 402 -> 서명 -> 재요청 -> 정산 흐름을 똑같이
처리하므로, 결제 관련 코드는 한 줄도 안 바뀌었다.

사용 전 준비물은 client_example.py와 동일:
  pip install "x402[https]" eth_account httpx
  EVM_PRIVATE_KEY 또는 EVM_MNEMONIC 환경변수 중 하나 설정.
  실제 결제는 CDP Facilitator(메인넷, eip155:8453)로 나가므로 진짜 USDC가
  차감된다 - 기본 대상 도구는 macro_dday($0.01, 가장 저렴하고 외부 API
  의존이 없어 가장 안정적으로 확인된 도구)로 잡아뒀다.

로컬(uvicorn)은 PAYMENT_BYPASS_FOR_TESTING=true라 결제 없이 다 통과되므로
실결제 검증은 반드시 배포된 서버(ALPHAPIPELINE_API_BASE)를 대상으로 해야
의미가 있다 - 기본값을 프로덕션 URL로 잡아뒀다.
"""
import asyncio
import json
import os

from eth_account import Account
from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

API_BASE = os.getenv("ALPHAPIPELINE_API_BASE", "https://alphapipeline-eu.onrender.com")
MCP_ENDPOINT = "/mcp"
TOOL_NAME = os.getenv("MCP_TOOL_NAME", "macro_dday")
TOOL_ARGUMENTS = json.loads(os.getenv("MCP_TOOL_ARGUMENTS", "{}"))
MNEMONIC_HD_PATH = "m/44'/60'/0'/0/0"

_RPC_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": TOOL_NAME, "arguments": TOOL_ARGUMENTS},
}


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


async def call_mcp_tool() -> None:
    account = _load_account()
    if account is None:
        print(
            "EVM_PRIVATE_KEY 또는 EVM_MNEMONIC 중 하나가 설정되지 않았습니다. "
            "결제 없이 402 응답만 확인합니다 (402가 나오면 프로토콜은 정상 동작 중)."
        )
        import httpx

        async with httpx.AsyncClient() as http:
            resp = await http.post(f"{API_BASE}{MCP_ENDPOINT}", json=_RPC_BODY)
            print(f"상태 코드: {resp.status_code}")
            print(resp.text[:1000])
            return

    print(f"사용 지갑 주소: {account.address}")
    print(f"호출 대상: POST {API_BASE}{MCP_ENDPOINT}  tools/call name={TOOL_NAME} arguments={TOOL_ARGUMENTS}")
    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))
    http_client = x402HTTPClient(client)
    async with x402HttpxClient(client) as http:
        response = await http.post(f"{API_BASE}{MCP_ENDPOINT}", json=_RPC_BODY)
        await response.aread()
        print(f"상태 코드: {response.status_code}")
        print(f"본문: {response.text}")
        if response.is_success:
            body = response.json()
            if "error" in body:
                print(f"JSON-RPC 에러: {body['error']}")
            else:
                content_text = body["result"]["content"][0]["text"]
                print(f"tools/call 결과(파싱됨): {json.loads(content_text)}")
            settle_response = http_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            print(f"결제 정산 결과: {settle_response}")


if __name__ == "__main__":
    asyncio.run(call_mcp_tool())
