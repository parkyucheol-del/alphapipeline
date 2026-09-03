"""
AlphaPipeline - 방금 적용한 patch_mcp_server.py의 버그 하나를 고치는 후속 패치.

## 왜 필요한가
방금 curl/Invoke-RestMethod로 확인해보니 tools/list에 dump_risk가 안 떴다 -
로컬 .env에서 DUMP_RISK_ENABLED가 false라서다. 그런데 app/payment.py의
build_routes() 주석을 보면 dump_risk_enabled=False의 실제 의미는 "기능
꺼짐"이 아니라 "결제 게이트 없이 무료로 서빙 중"이다(REST 엔드포인트 자체는
여전히 동작함) - 방금 만든 app/mcp_server.py는 이걸 "숨김/거부"로 잘못
해석해서 tools/list에서 통째로 빼고 tools/call도 거부하고 있었다. REST가
무료로 응답하는데 MCP가 "이 도구는 없다"고 하면 두 표면 사이에 사실이
어긋난다.

## 무엇을 고치나
app/mcp_server.py 안의 두 부분만 고친다(다른 파일은 전혀 안 건드림):
  1) _build_tool_list(): dump_risk를 항상 목록에 포함시키되, DUMP_RISK_ENABLED가
     false일 때는 price_usdc를 0으로 정확히 표시하고 description에 "현재
     무료"라고 명시한다.
  2) _call_tool(): "비활성화됐으니 거부" 로직을 없앤다 - 원래 REST 셀프
     호출이 알아서 그 상태(무료)에 맞게 응답하므로 막을 이유가 없었다.

이번에도 쓰기 전에 생성될 내용을 compile()로 문법 검사하고, 목업 저장소에
실제로 적용해 20개 항목 테스트를 재실행해서 통과를 확인했다.
"""
import sys
from pathlib import Path

MCP_SERVER_PY = Path("app/mcp_server.py")

OLD_BUILD_TOOL_LIST = '''def _visible_tools() -> list[dict]:
    return [t for t in _TOOLS if not t.get("dump_risk_only") or _dump_risk_enabled()]


def _build_tool_list() -> list[dict]:
    tools = []
    for t in _visible_tools():
        tools.append(
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["input_schema"],
                "_meta": {
                    "x402": {
                        "price_usdc": getattr(settings, t["price_attr"]),
                        "network": ACTIVE_NETWORK,
                        "asset": "USDC",
                        "pay_to": settings.RECEIVER_WALLET_ADDRESS,
                        "rest_equivalent": f"GET {t['path']}",
                    }
                },
            }
        )
    return tools'''

NEW_BUILD_TOOL_LIST = '''def _build_tool_list() -> list[dict]:
    # 2026-09 수정: dump_risk_enabled=False는 "기능 꺼짐"이 아니라 app/payment.py
    # build_routes()의 실제 의미대로 "결제 게이트 없이 무료로 서빙 중"이다(REST와
    # 동일). 그래서 도구를 목록에서 숨기거나 tools/call을 거부하지 않고, 항상
    # 노출하되 가격만 0으로 정확히 표시한다 - REST가 무료로 응답하는데 MCP가
    # "이 도구는 없다"고 하면 표면 간에 사실이 어긋난다.
    tools = []
    for t in _TOOLS:
        is_free_now = bool(t.get("dump_risk_only")) and not _dump_risk_enabled()
        if is_free_now:
            price = 0.0
            description = t["description"].replace(
                "Paid in USDC on Base.",
                "Currently offered FREE (no payment required) - the x402 payment gate "
                "is temporarily disabled for this endpoint.",
            )
        else:
            price = getattr(settings, t["price_attr"])
            description = t["description"]
        tools.append(
            {
                "name": t["name"],
                "description": description,
                "inputSchema": t["input_schema"],
                "_meta": {
                    "x402": {
                        "price_usdc": price,
                        "network": ACTIVE_NETWORK,
                        "asset": "USDC",
                        "pay_to": settings.RECEIVER_WALLET_ADDRESS,
                        "rest_equivalent": f"GET {t['path']}",
                        "currently_free": is_free_now,
                    }
                },
            }
        )
    return tools'''

OLD_CALL_TOOL_GUARD = '''    tool = _TOOL_BY_NAME.get(name)
    if tool is None or (tool.get("dump_risk_only") and not _dump_risk_enabled()):
        return JSONResponse(
            content=_jsonrpc_error(req_id, -32602, f"알 수 없거나 비활성화된 tool 이름: {name}")
        )'''

NEW_CALL_TOOL_GUARD = '''    tool = _TOOL_BY_NAME.get(name)
    if tool is None:
        return JSONResponse(content=_jsonrpc_error(req_id, -32602, f"알 수 없는 tool 이름: {name}"))'''


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
        fail(f"{MCP_SERVER_PY}가 없습니다 - patch_mcp_server.py를 먼저 적용해주세요.")

    src = MCP_SERVER_PY.read_text(encoding="utf-8")

    if "currently_free" in src:
        fail("이미 수정이 적용된 것 같습니다 (currently_free 마커 발견) - 중복 적용 방지.")

    new_src = must_replace(src, OLD_BUILD_TOOL_LIST, NEW_BUILD_TOOL_LIST, "_build_tool_list()/_visible_tools() 블록")
    new_src = must_replace(new_src, OLD_CALL_TOOL_GUARD, NEW_CALL_TOOL_GUARD, "_call_tool()의 tool 이름 검사 블록")

    try:
        compile(new_src, str(MCP_SERVER_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {MCP_SERVER_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    MCP_SERVER_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {MCP_SERVER_PY} 수정 (dump_risk는 이제 비활성화 상태에서도 목록에 남고, 가격만 0으로 표시됩니다).")
    print("다음 단계:")
    print("  1) python -m py_compile app/mcp_server.py")
    print("  2) uvicorn 창은 --reload라 자동 재기동됨 - 다시 tools/list 호출해서 dump_risk가 뜨는지, price_usdc가 뭔지 확인")
    print("  3) 확인되면 git add -A && git commit && git push")


if __name__ == "__main__":
    main()
