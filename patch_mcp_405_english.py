"""
AlphaPipeline - GET /mcp의 405 응답 본문을 영어 병기로 바꾸는 패치.

## 왜 필요한가
외부 개발자가 실제로 테스트해보고 지적한 내용: dump-risk의 coverage_notice는
좋은 설계인데, 그 notice 자체와 GET /mcp의 405 응답 메시지가 한국어 전용이라
영어 기반 AI 에이전트가 못 읽는다는 지적. "에이전트가 못 읽는 경고문은 안
붙인 것과 같다"는 정확한 지적이라 이 부분(405 응답)부터 고친다 - 코드를
이미 갖고 있어서 바로 고칠 수 있는 부분.

## 무엇을 고치나
mcp_streaming_not_supported()가 반환하는 message 필드를 영어를 앞에 두고
한국어를 뒤에 병기하는 문장으로 교체한다. 다른 로직/상태코드/필드명은 전혀
안 건드린다 - 순수 텍스트 교체.

(참고: _SERVER_INSTRUCTIONS도 한국어 전용이라 같은 카테고리의 문제인데,
이번 리뷰가 콕 집은 부분은 아니라서 이 패치엔 포함 안 했음 - 필요하면 별도
패치로 처리하는 게 나을 듯)
"""
import sys
from pathlib import Path

MCP_SERVER_PY = Path("app/mcp_server.py")

OLD = '''                "message": (
                    "이 MCP 서버는 상태 비저장(stateless) Streamable HTTP - POST만 지원합니다. "
                    "서버가 먼저 보내는 알림이 없어 SSE 스트리밍(GET)은 제공하지 않습니다."
                ),'''

NEW = '''                "message": (
                    "This MCP server is stateless Streamable HTTP - POST only. There is no "
                    "SSE streaming (GET) because the server never sends unsolicited "
                    "notifications. / 이 MCP 서버는 상태 비저장(stateless) Streamable HTTP - "
                    "POST만 지원합니다. 서버가 먼저 보내는 알림이 없어 SSE 스트리밍(GET)은 "
                    "제공하지 않습니다."
                ),'''


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def main() -> None:
    if not MCP_SERVER_PY.exists():
        fail(f"{MCP_SERVER_PY}가 없습니다 - 이전 MCP 서버 패치들을 먼저 적용해주세요.")

    src = MCP_SERVER_PY.read_text(encoding="utf-8")

    if "There is no " in src and "SSE streaming (GET)" in src:
        fail("이미 이 패치가 적용된 것 같습니다 - 중복 적용 방지.")

    count = src.count(OLD)
    if count != 1:
        fail(f"예상한 앵커 텍스트를 1번이 아니라 {count}번 찾았습니다 - 파일이 예상과 다른 상태인 것 같습니다.")
    new_src = src.replace(OLD, NEW, 1)

    try:
        compile(new_src, str(MCP_SERVER_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {MCP_SERVER_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    MCP_SERVER_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {MCP_SERVER_PY}의 GET /mcp 405 응답 메시지를 영어+한국어 병기로 바꿨습니다.")
    print("다음 단계:")
    print("  1) python -m py_compile app/mcp_server.py")
    print('  2) git add -A && git commit -m "Add English text to GET /mcp 405 response" && git push')
    print("  3) 배포 확인되면 브라우저나 curl로 GET https://alphapipeline-eu.onrender.com/mcp 호출해서 메시지 확인")


if __name__ == "__main__":
    main()
