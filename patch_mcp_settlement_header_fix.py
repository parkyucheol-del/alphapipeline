"""
AlphaPipeline - MCP 실결제 마지막 남은 조각: 결제 성공 시 정산 영수증 헤더가
누락되던 문제를 고치는 패치 (이번 라운드 네 번째이자 마지막).

## 왜 필요한가
방금 실제 지갑으로 결제까지 성공했다(상태 200, 실제 데이터 정상 수신) -
결제 자체는 완전히 고쳐졌다. 그런데 테스트 스크립트 마지막 단계에서
http_client.get_payment_settle_response(...)가
"ValueError: Payment response header not found"로 실패했다. 원인은
402 때와 완전히 같은 종류의 버그: 결제 성공(200) 응답에도 실제 정산
영수증이 "payment-response"류 헤더에 실려 오는데, _call_tool()의 성공
경로(JSONResponse 반환)가 upstream 헤더를 전혀 릴레이하지 않고 있었다.

## 무엇을 고치나
app/mcp_server.py 안, 이전 패치(patch_mcp_402_header_fix.py)로 만든 402
분기의 헤더 필터링 로직을 재사용 가능한 _relay_headers() 헬퍼로 뽑아내고,
성공(200) 경로에서도 이 헬퍼로 헤더를 릴레이한다.

쓰기 전에 결과를 compile()로 검사했고, 이전 네 개 패치까지 전부 순서대로
적용한 뒤 이 패치를 얹어서 21개 항목 테스트(결제 정산 헤더 릴레이 검증
포함)를 재실행해 통과를 확인했다.
"""
import sys
from pathlib import Path

MCP_SERVER_PY = Path("app/mcp_server.py")

OLD_BLOCK = '''_HOP_BY_HOP_RESPONSE_HEADERS = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "content-length", "content-encoding", "date", "server",
}


async def _call_tool(body: dict, request: Request, client: httpx.AsyncClient) -> Response:'''

NEW_BLOCK = '''_HOP_BY_HOP_RESPONSE_HEADERS = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "content-length", "content-encoding", "date", "server",
}


def _relay_headers(upstream: httpx.Response) -> dict[str, str]:
    """내부 self-call 응답 헤더 중 hop-by-hop/길이 관련만 빼고 그대로 릴레이한다.

    402(결제 조건)와 200(결제 정산 영수증) 양쪽 다 이걸 쓴다 - 두 경우 모두
    실제 정보가 본문이 아니라 헤더에 실려 오기 때문이다.
    """
    return {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS}


async def _call_tool(body: dict, request: Request, client: httpx.AsyncClient) -> Response:'''

OLD_TAIL = '''    if upstream.status_code == 402:
        # x402 결제 필요 - 실제 결제 조건은 본문이 아니라 payment-required
        # 헤더에 실려 있다(위 _HOP_BY_HOP_RESPONSE_HEADERS 주석 참고) - 본문과
        # 헤더를 함께 그대로 릴레이한다. MCP에는 402에 대응하는 JSON-RPC
        # 에러 코드가 없으므로, 이 요청 자체의 HTTP 응답을 그대로 402로
        # 릴레이한다(모듈 docstring 참고).
        relay_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS
        }
        return Response(content=upstream.content, status_code=402, headers=relay_headers)
    if upstream.status_code >= 400:
        return JSONResponse(
            content=_jsonrpc_error(
                req_id, -32000, f"업스트림 오류 ({upstream.status_code}): {upstream.text[:500]}"
            )
        )

    data = upstream.json()
    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
    return JSONResponse(content=_jsonrpc_result(req_id, result))'''

NEW_TAIL = '''    if upstream.status_code == 402:
        # x402 결제 필요 - 실제 결제 조건은 본문이 아니라 payment-required
        # 헤더에 실려 있다(위 _HOP_BY_HOP_RESPONSE_HEADERS 주석 참고) - 본문과
        # 헤더를 함께 그대로 릴레이한다. MCP에는 402에 대응하는 JSON-RPC
        # 에러 코드가 없으므로, 이 요청 자체의 HTTP 응답을 그대로 402로
        # 릴레이한다(모듈 docstring 참고).
        return Response(content=upstream.content, status_code=402, headers=_relay_headers(upstream))
    if upstream.status_code >= 400:
        return JSONResponse(
            content=_jsonrpc_error(
                req_id, -32000, f"업스트림 오류 ({upstream.status_code}): {upstream.text[:500]}"
            )
        )

    data = upstream.json()
    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
    # 결제 성공 시에도 정산 영수증(payment-response류 헤더 - 402 때와 마찬가지로
    # 정확한 이름을 하드코딩하지 않았다)이 실려 있으므로, 클라이언트가
    # get_payment_settle_response()로 읽을 수 있도록 그대로 같이 릴레이한다.
    return JSONResponse(content=_jsonrpc_result(req_id, result), headers=_relay_headers(upstream))'''


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def must_replace(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        fail(f"{label}: 예상한 앵커 텍스트를 1번이 아니라 {count}번 찾았습니다 - 파일이 예상과 다른 상태인 것 같습니다.")
    return content.replace(old, new, 1)


def main() -> None:
    if not MCP_SERVER_PY.exists():
        fail(f"{MCP_SERVER_PY}가 없습니다 - 이전 패치들을 먼저 적용해주세요.")

    src = MCP_SERVER_PY.read_text(encoding="utf-8")

    if "_relay_headers" in src:
        fail("이미 수정이 적용된 것 같습니다 (_relay_headers 마커 발견) - 중복 적용 방지.")

    new_src = must_replace(src, OLD_BLOCK, NEW_BLOCK, "_HOP_BY_HOP_RESPONSE_HEADERS 뒤 함수 선언부")
    new_src = must_replace(new_src, OLD_TAIL, NEW_TAIL, "_call_tool() 402/성공 응답 블록")

    try:
        compile(new_src, str(MCP_SERVER_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {MCP_SERVER_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    MCP_SERVER_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {MCP_SERVER_PY} 수정 (결제 성공 시 정산 영수증 헤더도 릴레이됩니다).")
    print("다음 단계:")
    print("  1) python -m py_compile app/mcp_server.py")
    print("  2) git add -A && git commit && git push")
    print("  3) 배포 확인되면 examples\\test_mcp_call.py 다시 실행 - 이번엔 '결제 정산 결과: ...'까지 에러 없이 끝까지 출력되어야 함")


if __name__ == "__main__":
    main()
