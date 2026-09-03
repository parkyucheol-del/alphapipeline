"""
AlphaPipeline - 방금 확인된 MCP 402 릴레이 버그를 고치는 후속 패치 (두 번째).

## 왜 필요한가
curl.exe로 실서비스를 직접 찍어보니, REST 402 응답의 실제 결제 조건(accepts
배열, price, network 등)은 응답 "본문"이 아니라 "payment-required"라는
응답 헤더에 base64(JSON)로 실려 온다 - 본문은 그냥 "{}"였다. 방금 만든
app/mcp_server.py의 _call_tool()은 내부 self-call 결과에서 본문만
릴레이하고 헤더는(content-type 하나만 빼고) 전부 버렸기 때문에, MCP
tools/call로 결제 없이 호출하면 402는 뜨는데 정작 결제에 필요한 정보가
하나도 안 담겨 왔다.

## 무엇을 고치나
app/mcp_server.py 안의 _call_tool() 함수 하나만 고친다:
  - 새 모듈 상수 _HOP_BY_HOP_RESPONSE_HEADERS 추가 (릴레이하면 안 되는
    hop-by-hop/길이 관련 헤더 목록).
  - 402 분기에서 upstream 응답의 헤더를(hop-by-hop 제외하고) 전부 그대로
    릴레이 - "payment-required" 헤더 이름을 하드코딩하지 않고 일반화해서,
    이 SDK가 나중에 헤더를 더 추가해도 자동으로 같이 넘어가게 했다.

쓰기 전에 결과 문자열을 compile()로 문법 검사했고, 목업 저장소에 실제로
적용해서 20개 항목 테스트(402 헤더 릴레이 검증 포함)를 재실행해 통과를
확인했다.
"""
import sys
from pathlib import Path

MCP_SERVER_PY = Path("app/mcp_server.py")

OLD_BLOCK = '''def _jsonrpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _call_tool(body: dict, request: Request, client: httpx.AsyncClient) -> Response:
    req_id = body.get("id")
    params = body.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}

    tool = _TOOL_BY_NAME.get(name)
    if tool is None:
        return JSONResponse(content=_jsonrpc_error(req_id, -32602, f"알 수 없는 tool 이름: {name}"))

    query = {k: v for k, v in arguments.items() if v is not None}
    forward_headers = {}
    payment_header = request.headers.get("x-payment")
    if payment_header:
        forward_headers["X-PAYMENT"] = payment_header

    try:
        upstream = await client.get(tool["path"], params=query, headers=forward_headers)
    except Exception as e:
        logger.exception("MCP tools/call 내부 self-call 실패: %s", name)
        return JSONResponse(content=_jsonrpc_error(req_id, -32000, f"내부 호출 실패: {e}"))

    if upstream.status_code == 402:
        # x402 결제 필요 - MCP에는 402에 대응하는 JSON-RPC 에러 코드가 없으므로,
        # 이 요청 자체의 HTTP 응답을 그대로 402로 릴레이한다(모듈 docstring 참고).
        return Response(
            content=upstream.content,
            status_code=402,
            media_type=upstream.headers.get("content-type", "application/json"),
        )'''

NEW_BLOCK = '''def _jsonrpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# 2026-09-03 실제 배포본에 curl로 직접 확인한 결과: 이 세션이 쓰는 x402 SDK
# 버전은 402 응답의 실제 결제 조건(accepts 배열 등)을 본문이 아니라
# "payment-required" 응답 헤더에 base64(JSON)로 싣는다 - 본문은 그냥 "{}"다.
# 그래서 본문만 릴레이하면 클라이언트가 결제 조건을 전혀 못 받는다 - 아래
# hop-by-hop/길이 관련 헤더만 빼고 나머지는 전부 그대로 릴레이해서
# payment-required가 반드시 같이 넘어가게 한다(방향은 바뀔 수 있는 SDK
# 세부사항이라 특정 헤더 이름 하나만 하드코딩하지 않았다).
_HOP_BY_HOP_RESPONSE_HEADERS = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "content-length", "content-encoding", "date", "server",
}


async def _call_tool(body: dict, request: Request, client: httpx.AsyncClient) -> Response:
    req_id = body.get("id")
    params = body.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}

    tool = _TOOL_BY_NAME.get(name)
    if tool is None:
        return JSONResponse(content=_jsonrpc_error(req_id, -32602, f"알 수 없는 tool 이름: {name}"))

    query = {k: v for k, v in arguments.items() if v is not None}
    forward_headers = {}
    payment_header = request.headers.get("x-payment")
    if payment_header:
        forward_headers["X-PAYMENT"] = payment_header

    try:
        upstream = await client.get(tool["path"], params=query, headers=forward_headers)
    except Exception as e:
        logger.exception("MCP tools/call 내부 self-call 실패: %s", name)
        return JSONResponse(content=_jsonrpc_error(req_id, -32000, f"내부 호출 실패: {e}"))

    if upstream.status_code == 402:
        # x402 결제 필요 - 실제 결제 조건은 본문이 아니라 payment-required
        # 헤더에 실려 있다(위 _HOP_BY_HOP_RESPONSE_HEADERS 주석 참고) - 본문과
        # 헤더를 함께 그대로 릴레이한다. MCP에는 402에 대응하는 JSON-RPC
        # 에러 코드가 없으므로, 이 요청 자체의 HTTP 응답을 그대로 402로
        # 릴레이한다(모듈 docstring 참고).
        relay_headers = {
            k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS
        }
        return Response(content=upstream.content, status_code=402, headers=relay_headers)'''


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def main() -> None:
    if not MCP_SERVER_PY.exists():
        fail(f"{MCP_SERVER_PY}가 없습니다 - patch_mcp_server.py를 먼저 적용해주세요.")

    src = MCP_SERVER_PY.read_text(encoding="utf-8")

    if "_HOP_BY_HOP_RESPONSE_HEADERS" in src:
        fail("이미 수정이 적용된 것 같습니다 (_HOP_BY_HOP_RESPONSE_HEADERS 마커 발견) - 중복 적용 방지.")

    count = src.count(OLD_BLOCK)
    if count != 1:
        fail(f"_call_tool() 402 분기 블록: 예상한 앵커 텍스트를 1번이 아니라 {count}번 찾았습니다 - 파일이 예상과 다른 상태인 것 같습니다.")

    new_src = src.replace(OLD_BLOCK, NEW_BLOCK, 1)

    try:
        compile(new_src, str(MCP_SERVER_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {MCP_SERVER_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    MCP_SERVER_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {MCP_SERVER_PY} 수정 (402 응답의 payment-required 헤더가 이제 그대로 릴레이됩니다).")
    print("다음 단계:")
    print("  1) python -m py_compile app/mcp_server.py")
    print("  2) uvicorn --reload 자동 재기동 후, curl.exe -s -i -X POST 로 tools/call 402 다시 확인 (이번엔 payment-required 헤더가 보여야 함)")
    print("  3) 확인되면 git add -A && git commit && git push")


if __name__ == "__main__":
    main()
