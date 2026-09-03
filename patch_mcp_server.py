"""
AlphaPipeline - 원격 MCP 서버 패치 스크립트.

적용 내용:
  1) 새 파일 app/mcp_server.py 생성 - 기존 7개 x402 유료 REST 엔드포인트를
     MCP(Model Context Protocol) tools/call로 감싸는 어댑터. payment.py는
     전혀 건드리지 않고, 이미 검증된 PaymentMiddlewareASGI 결제 게이트를
     내부 self-call(httpx.ASGITransport)로 재사용한다.
  2) main.py 맨 끝(`if __name__ == "__main__":` 직전)에 이 어댑터를 등록하는
     두 줄만 추가한다 - 기존 import 블록/라우트 정의는 전혀 건드리지 않는다
     (실제 main.py의 정확한 서식을 이 세션이 볼 수 없으므로, 가장 안전하고
     보편적인 단일 라인 - 모든 파이썬 엔트리포인트에 있는 `if __name__ ==
     "__main__":` - 을 앵커로 골랐다).

검증 절차 (이번 세션에서 실제로 실행 후 확인):
  - 생성될 app/mcp_server.py 내용을 `compile()`로 문법 검사.
  - 목업 저장소에 실제로 적용해서 python3 -m py_compile 통과 확인.
  - JSON-RPC 디스패치/툴 목록/402 릴레이/에러코드/알 수 없는 툴/배치 거부/
    등록된 핸들러(JSON 파싱 실패, GET 405)까지 20개 항목을 검증하는
    test_mcp_server.py를 별도로 실행해 전부 통과 확인(샌드박스가 fastapi/
    x402 신규 설치를 막아둬서, 최소 스텁 위에서 이 패치 스크립트가 만들
    "실제 소스"를 그대로 실행해 검증했다 - x402 SDK 자체를 이 세션에서
    설치 검증할 수 없다는 기존 제약과 동일).
  - 이 패치 스크립트 자체도 python3 -m py_compile로 검사 완료.
"""
import re
import sys
from pathlib import Path

MAIN_PY = Path("main.py")
NEW_FILE = Path("app/mcp_server.py")


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def main() -> None:
    if not MAIN_PY.exists():
        fail("main.py를 찾을 수 없습니다 - 저장소 루트에서 실행해주세요.")
    if NEW_FILE.exists():
        fail(f"{NEW_FILE}가 이미 존재합니다 - 이 패치가 이미 적용된 것 같습니다 (중복 적용 방지).")

    main_src = MAIN_PY.read_text(encoding="utf-8")
    if "register_mcp_routes" in main_src:
        fail("main.py에 register_mcp_routes가 이미 있습니다 - 이 패치가 이미 적용된 것 같습니다 (중복 적용 방지).")

    # main.py 끝부분의 `if __name__ == "__main__":` (또는 작은따옴표) 한 줄만
    # 앵커로 쓴다 - 모든 파이썬 엔트리포인트에 있는 가장 보편적이고 주석/
    # 문서가 끼어들 여지가 거의 없는 한 줄이라, 실제 main.py의 정확한 서식을
    # 몰라도 안전하게 맞출 수 있다 (기존 import 블록 등은 전혀 건드리지 않음).
    main_pattern = re.compile(r'^if __name__ == ["\']__main__["\']:\s*\n', re.MULTILINE)
    main_matches = list(main_pattern.finditer(main_src))
    if len(main_matches) != 1:
        fail(
            f'main.py `if __name__ == "__main__":` 앵커: 예상한 패턴을 1번이 아니라 '
            f"{len(main_matches)}번 찾았습니다 - 파일이 예상과 다른 상태인 것 같습니다."
        )
    m = main_matches[0]
    insertion = (
        "from app.mcp_server import register_mcp_routes as _register_mcp_routes  # noqa: E402\n"
        "\n"
        "_register_mcp_routes(app)\n"
        "\n"
        "\n"
    )
    new_main_src = main_src[: m.start()] + insertion + main_src[m.start() :]

    new_mcp_server_src = MCP_SERVER_SOURCE

    # 쓰기 전에 두 결과물 모두 실제로 컴파일해본다 (지난 매크로 캘린더 패치
    # 때, 스크립트 자체가 아니라 결과물을 손으로 고쳐서 "검증"한 게 실제
    # 문법 오류를 놓친 원인이었음 - 이번엔 생성될 소스 문자열 자체를 쓰기
    # 전에 compile()로 검사해서 그 문제를 원천 차단한다).
    try:
        compile(new_mcp_server_src, str(NEW_FILE), "exec")
    except SyntaxError as e:
        fail(f"생성될 {NEW_FILE} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")
    try:
        compile(new_main_src, str(MAIN_PY), "exec")
    except SyntaxError as e:
        fail(f"패치될 main.py 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    NEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    NEW_FILE.write_text(new_mcp_server_src, encoding="utf-8")
    MAIN_PY.write_text(new_main_src, encoding="utf-8")

    print(f"완료: {NEW_FILE} 생성 + main.py 패치.")
    print("다음 단계:")
    print("  1) pip show httpx  (이미 app/data_sources.py가 쓰고 있어서 보통 이미 설치돼 있음)")
    print("  2) python -m py_compile app/mcp_server.py main.py")
    print("  3) uvicorn main:app --reload 로 로컬 기동 후,")
    print('     curl -s -X POST http://localhost:8000/mcp -H "Content-Type: application/json" \\')
    print('       -d \'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\'')
    print("     로 7개 도구 목록이 뜨는지 확인")
    print("  4) git add app/mcp_server.py main.py && git commit && git push")


MCP_SERVER_SOURCE = r'''"""
AlphaPipeline 원격 MCP 서버 (Streamable HTTP, stateless) - 기존 7개 x402 유료
REST 엔드포인트를 Cursor/Claude Desktop/커스텀 에이전트가 MCP tools/call로 바로
호출할 수 있게 감싸는 얇은 어댑터.

## 설계 요약 (왜 이렇게 만들었나)
- MCP 표준 자체에는 402에 대응하는 개념이 없다 - 결제는 HTTP 트랜스포트
  레벨에서 처리된다(공식 x402 MCP 통합 가이드들이 공통으로 문서화한 관례:
  docs.x402.org/guides/mcp-server-with-x402, systemprompt.io, eco.com 2026
  가이드 참고). 그래서 `tools/call`이 유료 도구를 가리키면, 이 어댑터는
  **이미 검증된 기존 PaymentMiddlewareASGI 결제 게이트를 그대로 재사용**하기
  위해 같은 프로세스 안에서 해당 REST GET 엔드포인트로 셀프 ASGI 호출을 한다
  (httpx.ASGITransport) - payment.py는 전혀 건드리지 않는다.
- `PaymentMiddlewareASGI`는 URL 경로(`"METHOD /path"`)로만 가격을 구분하므로,
  도구마다 다른 단가를 유지하려면 `/mcp` 한 경로에 전부 때려박을 수 없다.
  대신 이미 가격이 매겨져 있는 기존 GET REST 경로를 그대로 재호출해서, 단가
  기준이 REST/MCP 두 표면 사이에서 절대 어긋나지 않게(single source of
  truth) 만들었다.
- `/mcp` 자체는 결제 라우트 dict(app/payment.py의 build_routes())에 등록하지
  않는다 - `initialize`/`tools/list`는 항상 무료로 열려 있어야 에이전트가
  "뭘 살 수 있는지" 먼저 확인할 수 있다("free discovery, paid execution"
  원칙, 위 가이드들이 공통으로 강조).
- 업스트림(내부 self-call) 응답이 402면 그 상태코드/본문을 그대로 이 요청의
  HTTP 응답으로 릴레이한다 - JSON-RPC 에러 객체로 감싸지 않는다. 이게
  x402-aware 클라이언트(x402-httpx/x402-fetch류 페이먼트 인터셉터를 쓰는
  봇 개발자 코드)가 기대하는 정확한 신호다. 참고: 순정 Claude Desktop/Cursor
  같은 사람 대상 앱은 자체 지갑이 없어서 이 402를 자동으로 결제하지
  못한다 - 이 MCP 서버는 "자체 지갑을 가진 에이전트/봇 프레임워크"를 위한
  것이지, 사람이 쓰는 채팅 UI에서 자동 결제가 되는 게 아니다(2026-09
  리서치로 확인 - Coinbase AgentKit MCP 확장 문서에도 x402 자동결제 언급
  없음, 여러 서드파티 "에이전트 지갑" MCP 서버가 별도로 존재하는 이유).
- MCP 스펙은 2026-07-28 리비전에서 프로토콜 코어 자체가 완전히 stateless로
  바뀌었다(Mcp-Session-Id 제거) - 우리 도구는 원래 세션 상태가 필요 없는
  단순 읽기 전용 데이터 조회라서, 처음부터 세션을 아예 추적하지 않는
  방식으로 만들었다 (모든 버전의 클라이언트와 호환).

## 알아두어야 할 점 (정직하게 밝혀둠)
- `tools/list`에 넣은 `_meta.x402` 필드(가격/네트워크/수신주소)는 MCP
  코어 스펙의 공식 필드가 아니라, `_meta`(스펙이 명시적으로 허용하는 확장
  지점)를 이용한 우리 자체 표기다 - 아직 업계 공통 표준(`x-x402` 등)이
  굳어지지 않아서, 이해 못 하는 클라이언트는 그냥 무시하는 안전한 방식을
  택했다.
- GET(SSE 스트리밍) 요청은 지원하지 않는다 - 도구들이 전부 짧은 동기 조회라
  서버가 먼저 보낼 알림이 없다. 스펙이 명시적으로 허용하는 대로 405를
  반환한다.
- JSON-RPC 배치(여러 요청을 배열로 한 번에)는 지원하지 않는다 - 2025-06-18
  스펙에서 이미 제거되었다.
"""
import json
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.payment import ACTIVE_NETWORK

logger = logging.getLogger("alphapipeline")

_PROTOCOL_VERSION_FALLBACK = "2025-06-18"

_SERVER_INSTRUCTIONS = (
    "AlphaPipeline은 각 도구를 Base 메인넷 USDC(x402 'exact' 스킴)로 건당 결제해야 "
    "쓸 수 있습니다. tools/list의 각 도구 _meta.x402 필드에 가격/네트워크/수신주소가 "
    "있습니다. 결제 없이 tools/call을 호출하면 이 요청에 대한 HTTP 응답 자체가 402가 "
    "되고, 본문에 x402 accepts 배열(가격/자산/수신주소/논스 등 결제에 필요한 정보)이 "
    "담겨 옵니다 - EIP-3009 transferWithAuthorization 서명을 만들어 X-PAYMENT 헤더에 "
    "실어 같은 요청을 재시도하세요. 사람이 쓰는 순정 Claude Desktop/Cursor 채팅 UI는 "
    "자체 지갑이 없어 이 결제를 자동으로 처리하지 못합니다 - 이 서버는 x402 결제 "
    "인터셉터(x402-httpx, x402-fetch 등)를 갖춘 에이전트/봇 코드를 위한 것입니다."
)

# 각 도구 -> 기존에 이미 결제가 걸려 있는 REST GET 엔드포인트로 매핑한다.
# input_schema는 app/payment.py의 build_routes()에 등록된 Bazaar discovery
# extension의 input_schema와 동일한 내용으로 맞췄다(단가/스펙이 REST와 항상
# 일치하도록 - 이 파일이 별도로 값을 정의하지 않고 그대로 베낀 이유).
_TOOLS: list[dict] = [
    {
        "name": "kimchi_alert",
        "path": "/v1/market/kimchi-alert",
        "price_attr": "PRICE_KIMCHI_ALERT_USDC",
        "description": (
            "Real-time Korea (Upbit) vs global crypto price premium - the 'kimchi "
            "premium' - with reverse-premium and 1h-surge alerts. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Crypto ticker symbol to check, e.g. BTC, ETH, SOL. Defaults to BTC.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "ai_markdown",
        "path": "/v1/tools/ai-markdown",
        "price_attr": "PRICE_AI_MARKDOWN_USDC",
        "description": (
            "Convert any webpage URL into clean, ad-free Markdown text optimized for "
            "LLM context windows. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Full http(s) URL of the webpage to convert to Markdown.",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "token_risk",
        "path": "/v1/security/token-risk",
        "price_attr": "PRICE_TOKEN_RISK_USDC",
        "description": (
            "GoPlus/Honeypot.is-backed token security check - honeypot flag, buy/sell "
            "tax, mintability, and ownership renouncement for a given contract address, "
            "so a bot can decide before it buys. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chain_id": {
                    "type": "integer",
                    "description": "EVM chain id, e.g. 8453 for Base.",
                },
                "contract_address": {
                    "type": "string",
                    "description": "Token contract address (0x...).",
                },
            },
            "required": ["chain_id", "contract_address"],
        },
    },
    {
        "name": "funding_rate",
        "path": "/v1/derivatives/funding-rate",
        "price_attr": "PRICE_FUNDING_RATE_USDC",
        "description": (
            "Bybit (primary) / Binance (fallback) perpetual futures funding rate - the "
            "key signal for long/short crowding that traders use to time or hedge "
            "positions before the next funding settlement. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. BTC, ETH, or BTCUSDT.",
                }
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "dex_liquidity_slippage",
        "path": "/v1/dex/liquidity-slippage",
        "price_attr": "PRICE_DEX_SLIPPAGE_USDC",
        "description": (
            "GeckoTerminal-backed DEX pool liquidity and estimated trade slippage - size "
            "a trade or compare pools before swapping, with a clearly-flagged "
            "constant-product approximation model. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "network": {
                    "type": "string",
                    "description": "GeckoTerminal network id, e.g. base, eth. Defaults to base.",
                },
                "pool_address": {
                    "type": "string",
                    "description": "Specific DEX pool contract address (optional if token_address is given).",
                },
                "token_address": {
                    "type": "string",
                    "description": "Token contract address - the most liquid pool is auto-selected (optional if pool_address is given).",
                },
                "trade_size_usd": {
                    "type": "number",
                    "description": "Hypothetical trade size in USD to estimate slippage for.",
                },
            },
            "required": ["trade_size_usd"],
        },
    },
    {
        "name": "macro_dday",
        "path": "/v1/calendar/macro-dday",
        "price_attr": "PRICE_MACRO_DDAY_USDC",
        "description": (
            "Countdown to the nearest major US macro event (Fed FOMC rate decision, "
            "CPI, or NFP) from a static, pre-loaded 2026 calendar - no live external API "
            "call, never fails on an upstream outage. No input parameters. Paid in USDC "
            "on Base."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dump_risk",
        "path": "/v1/unlocks/dump-risk",
        "price_attr": "PRICE_DUMP_RISK_USDC",
        "description": (
            "Tokens with large amounts of currently-locked or vesting supply relative to "
            "circulating supply - a proxy for future sell/dump pressure. Paid in USDC on "
            "Base."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "dump_risk_only": True,
    },
]
_TOOL_BY_NAME = {t["name"]: t for t in _TOOLS}


def _dump_risk_enabled() -> bool:
    # 실제 config.py에 이미 존재하는 플래그(REST build_routes()가 이미 씀) -
    # 혹시 몰라 getattr로 방어적으로 읽는다.
    return bool(getattr(settings, "DUMP_RISK_ENABLED", True))


def _visible_tools() -> list[dict]:
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
    return tools


def _jsonrpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def _call_tool(body: dict, request: Request, client: httpx.AsyncClient) -> Response:
    req_id = body.get("id")
    params = body.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}

    tool = _TOOL_BY_NAME.get(name)
    if tool is None or (tool.get("dump_risk_only") and not _dump_risk_enabled()):
        return JSONResponse(
            content=_jsonrpc_error(req_id, -32602, f"알 수 없거나 비활성화된 tool 이름: {name}")
        )

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
        )
    if upstream.status_code >= 400:
        return JSONResponse(
            content=_jsonrpc_error(
                req_id, -32000, f"업스트림 오류 ({upstream.status_code}): {upstream.text[:500]}"
            )
        )

    data = upstream.json()
    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
    return JSONResponse(content=_jsonrpc_result(req_id, result))


async def _dispatch(body, request: Request, client: httpx.AsyncClient) -> Response:
    if isinstance(body, list):
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_error(
                None, -32600, "JSON-RPC 배치 요청은 지원하지 않습니다 (MCP 2025-06-18+에서 제거됨) - 요청을 하나씩 보내세요."
            ),
        )
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "method" not in body:
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_error(body.get("id") if isinstance(body, dict) else None, -32600, "Invalid Request"),
        )

    method = body["method"]
    req_id = body.get("id")
    is_notification = "id" not in body

    if method == "initialize":
        client_params = body.get("params") or {}
        protocol_version = client_params.get("protocolVersion") or _PROTOCOL_VERSION_FALLBACK
        result = {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "alphapipeline-mcp", "version": "1.0.0"},
            "instructions": _SERVER_INSTRUCTIONS,
        }
        return JSONResponse(content=_jsonrpc_result(req_id, result))

    if is_notification or method == "notifications/initialized":
        # JSON-RPC 알림(id 없음)에는 응답 본문을 보내지 않는다.
        return Response(status_code=202)

    if method == "ping":
        return JSONResponse(content=_jsonrpc_result(req_id, {}))

    if method == "tools/list":
        return JSONResponse(content=_jsonrpc_result(req_id, {"tools": _build_tool_list()}))

    if method == "tools/call":
        return await _call_tool(body, request, client)

    return JSONResponse(content=_jsonrpc_error(req_id, -32601, f"Method not found: {method}"))


def register_mcp_routes(app: FastAPI) -> None:
    """main.py에서 앱 생성 직후 한 번만 호출한다.

    /mcp 는 의도적으로 app/payment.py의 결제 라우트 dict에 등록하지 않는다 -
    initialize/tools/list는 항상 무료로 열려 있어야 하고, tools/call의 실제
    결제 게이팅은 내부 self-call이 이미 결제가 걸려 있는 기존 REST 경로를
    타면서 자동으로 적용된다 (모듈 docstring 참고).
    """
    internal_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp-internal")

    @app.post("/mcp", include_in_schema=False)
    async def mcp_endpoint(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content=_jsonrpc_error(None, -32700, "Parse error"))
        return await _dispatch(body, request, internal_client)

    @app.get("/mcp", include_in_schema=False)
    async def mcp_streaming_not_supported():
        return JSONResponse(
            status_code=405,
            content={
                "error": "method_not_allowed",
                "message": (
                    "이 MCP 서버는 상태 비저장(stateless) Streamable HTTP - POST만 지원합니다. "
                    "서버가 먼저 보내는 알림이 없어 SSE 스트리밍(GET)은 제공하지 않습니다."
                ),
            },
        )
'''


if __name__ == "__main__":
    main()
