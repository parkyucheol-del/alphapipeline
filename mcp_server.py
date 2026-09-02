"""
AlphaPipeline MCP 서버.

기존 FastAPI 파이프라인(app/*)의 로직을 재사용해서, Claude Desktop이나
Cursor 같은 MCP 클라이언트가 로컬 도구로 바로 인식할 수 있게 래핑한다.

제공 도구 2개:
  1. convert_to_markdown(url)     -> app/markdown_tool.py의 url_to_markdown 재사용
  2. get_token_dump_risk(symbol)  -> app/logic.py의 get_symbol_dump_risk 재사용

주의 (중요, 정직하게 밝혀둠):
  - 이 MCP 서버는 x402 유료 HTTP API(main.py)와는 별개의 "배포판"이다.
    로컬에서 Claude Desktop에 연결해 쓰는 MCP 도구 호출 자체는 x402 결제를
    거치지 않는다 - 즉 이 경로로는 0.01 USDC 과금이 발생하지 않는다.
    이 MCP 서버의 목적은 수익화가 아니라 "배포/노출(distribution)":
    AI 에이전트 생태계에 우리 도구를 노출시켜 존재를 알리고, 나중에
    유료 x402 API(main.py) 쪽 트래픽으로 이어지게 하는 것이다.
  - get_token_dump_risk는 DUMP_RISK_ENABLED=false 또는 DROPSTAB_API_KEY
    미설정 상태에서는 에러 없이 "아직 준비 중"이라는 구조화된 응답을
    돌려준다 (사업 결정: 유료 DropsTab 플랜은 수요 검증 후 결제하기로 함).

로컬 실행:
    python mcp_server.py
Claude Desktop 연결:
    claude_desktop_config.json 예시 참고 (프로젝트 루트에 포함됨).
"""
import logging
import httpx

from mcp.server.fastmcp import FastMCP

from app.logic import get_symbol_dump_risk
from app.markdown_tool import url_to_markdown

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alphapipeline-mcp")

mcp = FastMCP("alphapipeline")


def _error(error_type: str, message: str) -> dict:
    """AI 에이전트가 파싱하기 쉬운 구조화된 에러 형식으로 통일."""
    return {"success": False, "error": {"type": error_type, "message": message}}


@mcp.tool()
async def convert_to_markdown(url: str) -> dict:
    """
    임의의 웹페이지 URL을 받아 광고/네비게이션/스크립트를 제거하고
    본문만 AI 친화적인 순수 마크다운으로 변환해서 반환한다.

    Args:
        url: 변환할 웹페이지의 전체 URL (예: "https://example.com/article")

    Returns:
        성공 시: {"success": true, "url", "title", "markdown", "char_count"}
        실패 시: {"success": false, "error": {"type", "message"}}
    """
    if not url or not str(url).strip():
        return _error("invalid_input", "url 파라미터가 비어 있습니다.")

    url = str(url).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return _error(
            "invalid_url",
            f"'{url}'은(는) 올바른 URL이 아닙니다. http:// 또는 https://로 시작해야 합니다.",
        )

    try:
        data = await url_to_markdown(url)
        return {"success": True, **data}
    except httpx.TimeoutException:
        return _error("timeout", f"'{url}' 요청이 시간 초과되었습니다 (15초).")
    except httpx.HTTPStatusError as e:
        return _error(
            "http_error",
            f"'{url}' 요청이 실패했습니다 (HTTP {e.response.status_code}). "
            "존재하지 않는 페이지이거나 접근이 차단되었을 수 있습니다.",
        )
    except httpx.RequestError as e:
        return _error("network_error", f"'{url}'에 연결할 수 없습니다: {e}")
    except ValueError as e:
        # url_to_markdown이 HTML이 아니거나 3MB 초과일 때 던지는 에러
        return _error("unsupported_content", str(e))
    except Exception as e:
        logger.exception("convert_to_markdown 처리 중 알 수 없는 오류")
        return _error("unknown_error", f"알 수 없는 오류가 발생했습니다: {e}")


@mcp.tool()
async def get_token_dump_risk(symbol: str) -> dict:
    """
    특정 토큰의 베스팅/락업 해제 D-Day, 유통량 대비 해제 비율, 실시간 거래량
    대비 매도 압력 스코어를 계산해서 간결한 요약 리포트로 반환한다.

    Args:
        symbol: 토큰 티커 심볼 (예: "ATH", "AO", "CPOOL"). 대소문자 무관.

    Returns:
        성공 & 데이터 있음: {"success": true, "available": true, "symbol",
            "unlock_date_utc", "days_until_unlock", "unlock_supply_pct",
            "volume_impact_pct", "sell_pressure_risk_level", ...}
        성공이지만 아직 기능 미제공/데이터 없음:
            {"success": true, "available": false, "reason", "message"}
            (예: 사업 결정에 따라 유료 데이터 소스가 아직 연결되지 않은 경우)
        실패: {"success": false, "error": {"type", "message"}}
    """
    if not symbol or not str(symbol).strip():
        return _error("invalid_input", "symbol 파라미터가 비어 있습니다. 예: 'ATH', 'AO', 'CPOOL'")

    try:
        result = await get_symbol_dump_risk(str(symbol))
        return {"success": True, **result}
    except Exception as e:
        logger.exception("get_token_dump_risk 처리 중 알 수 없는 오류")
        return _error("unknown_error", f"알 수 없는 오류가 발생했습니다: {e}")


if __name__ == "__main__":
    # stdio transport: Claude Desktop/Cursor가 로컬 프로세스로 이 스크립트를
    # 직접 실행하고 stdin/stdout으로 통신하는 표준 방식.
    mcp.run(transport="stdio")
