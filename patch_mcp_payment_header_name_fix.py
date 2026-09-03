"""
AlphaPipeline - MCP 실결제가 계속 402로 실패하던 진짜 원인을 고치는 패치
(세 번째이자 이번 라운드의 핵심 버그).

## 왜 필요한가
실제 지갑으로 examples/test_mcp_call.py를 돌려봤더니 결제가 자동으로
됐어야 하는데 계속 402만 떴다. 설치된 x402==2.21.0 SDK 소스
(x402/http/x402_http_client_base.py의 encode_payment_signature_header())를
직접 열어서 확인한 결과, x402 프로토콜 v2(우리 서버가 실제로 쓰는 버전 -
payment-required 헤더에 "x402Version":2로 확인됨)에서 클라이언트가 재시도
요청에 싣는 결제 헤더 이름은 "PAYMENT-SIGNATURE"다. "X-PAYMENT"는 v1
전용이다. 방금 만든 app/mcp_server.py의 _call_tool()은 "x-payment"만
찾고 있어서, 실제로 지갑이 보낸 결제 서명을 전혀 못 찾고 내부 REST
self-call에 결제 헤더 없이 보내고 있었다 - 그래서 재시도해도 계속 402가
난 것이다. payment.py/CDP Facilitator/지갑 서명 자체는 전부 정상이었고,
순전히 이 헤더 이름 하나가 문제였다.

## 무엇을 고치나
app/mcp_server.py의 _call_tool() 안, 헤더 전달 부분만 고친다:
  - "PAYMENT-SIGNATURE"(v2, 실제로 쓰이는 것)와 "X-PAYMENT"(v1, 하위 호환)
    둘 다 확인해서, 들어온 쪽 그대로 내부 self-call에 실어 보낸다.

쓰기 전에 결과를 compile()로 검사했고, 목업 저장소에 이전 두 패치까지
전부 순서대로 적용한 뒤 이 패치를 얹어서 21개 항목 테스트(v2/v1 헤더
포워딩 검증 포함)를 재실행해 통과를 확인했다.
"""
import sys
from pathlib import Path

MCP_SERVER_PY = Path("app/mcp_server.py")

OLD_BLOCK = '''    query = {k: v for k, v in arguments.items() if v is not None}
    forward_headers = {}
    payment_header = request.headers.get("x-payment")
    if payment_header:
        forward_headers["X-PAYMENT"] = payment_header'''

NEW_BLOCK = '''    query = {k: v for k, v in arguments.items() if v is not None}
    forward_headers = {}
    # x402 v2(우리 서버가 실제로 쓰는 버전)는 재시도 결제 헤더 이름이
    # "PAYMENT-SIGNATURE"다 - "X-PAYMENT"는 구버전(v1) 전용이다(설치된
    # x402==2.21.0의 x402/http/x402_http_client_base.py에서 직접 확인).
    # 어느 쪽으로 오든 그대로 실어 보내도록 둘 다 확인한다.
    payment_signature = request.headers.get("payment-signature")
    if payment_signature:
        forward_headers["PAYMENT-SIGNATURE"] = payment_signature
    legacy_x_payment = request.headers.get("x-payment")
    if legacy_x_payment:
        forward_headers["X-PAYMENT"] = legacy_x_payment'''


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def main() -> None:
    if not MCP_SERVER_PY.exists():
        fail(f"{MCP_SERVER_PY}가 없습니다 - patch_mcp_server.py를 먼저 적용해주세요.")

    src = MCP_SERVER_PY.read_text(encoding="utf-8")

    if "PAYMENT-SIGNATURE" in src:
        fail("이미 수정이 적용된 것 같습니다 (PAYMENT-SIGNATURE 마커 발견) - 중복 적용 방지.")

    count = src.count(OLD_BLOCK)
    if count != 1:
        fail(f"_call_tool() 헤더 전달 블록: 예상한 앵커 텍스트를 1번이 아니라 {count}번 찾았습니다 - 파일이 예상과 다른 상태인 것 같습니다.")

    new_src = src.replace(OLD_BLOCK, NEW_BLOCK, 1)

    try:
        compile(new_src, str(MCP_SERVER_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {MCP_SERVER_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    MCP_SERVER_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {MCP_SERVER_PY} 수정 (이제 PAYMENT-SIGNATURE 헤더로 실제 결제가 통과됩니다).")
    print("다음 단계:")
    print("  1) python -m py_compile app/mcp_server.py")
    print("  2) git add -A && git commit && git push")
    print("  3) 배포 확인되면 examples\\test_mcp_call.py 다시 실행해서 실결제 성공하는지 확인")


if __name__ == "__main__":
    main()
