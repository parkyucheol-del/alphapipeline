"""
AlphaPipeline MCP server (legacy, free, stdio transport).

Reuses the logic from the existing FastAPI pipeline (app/*), wrapped so MCP
clients like Claude Desktop or Cursor can pick it up directly as a local tool.

2 tools provided:
  1. convert_to_markdown(url)     -> reuses url_to_markdown from app/markdown_tool.py
  2. get_token_dump_risk(symbol)  -> reuses get_symbol_dump_risk from app/logic.py

Note (important, stated honestly):
  - This MCP server is a separate "distribution build" from the paid x402 HTTP
    API (main.py). Calling its tools locally via Claude Desktop does not go
    through x402 payment at all - no USDC is charged through this path.
    The point of this MCP server isn't monetization but distribution/exposure:
    getting our tools in front of the AI agent ecosystem so people discover
    them, with the expectation that it eventually drives traffic to the paid
    x402 API (main.py).
  - When DUMP_RISK_ENABLED=false or DROPSTAB_API_KEY is unset,
    get_token_dump_risk returns a structured "not yet available" response
    instead of an error (a business decision: pay for the DropsTab plan only
    after demand is validated).

Run locally:
    python mcp_server.py
Connect Claude Desktop:
    see the claude_desktop_config.json example included in the project root.
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
    """Unified structured error shape that's easy for an AI agent to parse."""
    return {"success": False, "error": {"type": error_type, "message": message}}


@mcp.tool()
async def convert_to_markdown(url: str) -> dict:
    """
    Convert any webpage URL into clean, AI-friendly markdown by stripping
    ads, navigation, and scripts and keeping only the main content.

    Args:
        url: The full URL of the webpage to convert (e.g. "https://example.com/article")

    Returns:
        On success: {"success": true, "url", "title", "markdown", "char_count"}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    if not url or not str(url).strip():
        return _error("invalid_input", "The url parameter is empty.")

    url = str(url).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return _error(
            "invalid_url",
            f"'{url}' is not a valid URL. It must start with http:// or https://.",
        )

    try:
        data = await url_to_markdown(url)
        return {"success": True, **data}
    except httpx.TimeoutException:
        return _error("timeout", f"Request to '{url}' timed out (15s).")
    except httpx.HTTPStatusError as e:
        return _error(
            "http_error",
            f"Request to '{url}' failed (HTTP {e.response.status_code}). "
            "The page may not exist or access may be blocked.",
        )
    except httpx.RequestError as e:
        return _error("network_error", f"Could not connect to '{url}': {e}")
    except ValueError as e:
        # Error raised by url_to_markdown when content isn't HTML or exceeds 3MB
        return _error("unsupported_content", str(e))
    except Exception as e:
        logger.exception("Unknown error while processing convert_to_markdown")
        return _error("unknown_error", f"An unknown error occurred: {e}")


@mcp.tool()
async def get_token_dump_risk(symbol: str) -> dict:
    """
    Calculate a token's vesting/unlock D-Day, unlock ratio relative to
    circulating supply, and a sell-pressure score against real-time volume,
    returned as a concise summary report.

    Args:
        symbol: Token ticker symbol (e.g. "ATH", "AO", "CPOOL"). Case-insensitive.

    Returns:
        Success & data available: {"success": true, "available": true, "symbol",
            "unlock_date_utc", "days_until_unlock", "unlock_supply_pct",
            "volume_impact_pct", "sell_pressure_risk_level", ...}
        Success but not yet available:
            {"success": true, "available": false, "reason", "message"}
            (e.g. the paid data source isn't connected yet, by business decision)
        Failure: {"success": false, "error": {"type", "message"}}
    """
    if not symbol or not str(symbol).strip():
        return _error("invalid_input", "The symbol parameter is empty. e.g. 'ATH', 'AO', 'CPOOL'")

    try:
        result = await get_symbol_dump_risk(str(symbol))
        return {"success": True, **result}
    except Exception as e:
        logger.exception("Unknown error while processing get_token_dump_risk")
        return _error("unknown_error", f"An unknown error occurred: {e}")


if __name__ == "__main__":
    # stdio transport: the standard way Claude Desktop/Cursor runs this script
    # directly as a local process and talks to it over stdin/stdout.
    mcp.run(transport="stdio")
