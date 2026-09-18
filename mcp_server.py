"""
AlphaPipeline MCP server (legacy, free, stdio transport).

Reuses the logic from the existing FastAPI pipeline (app/*), wrapped so MCP
clients like Claude Desktop or Cursor can pick it up directly as a local tool.
Also the exact server Glama's automated Docker build test introspects for
this listing's "Server" score, so its tool count should track app/mcp_server.py
(the paid remote server) 1:1 even though calls here bypass payment entirely.

14 tools provided (same coverage as the paid remote /mcp server, just called
directly against app/logic.py instead of going through x402 payment):
  1.  convert_to_markdown(url)                              -> app/markdown_tool.py: url_to_markdown
  2.  get_token_dump_risk(symbol)                            -> app/logic.py: get_symbol_dump_risk
  3.  get_kimchi_alert(symbol)                               -> app/logic.py: get_kimchi_alert
  4.  get_token_risk(chain_id, contract_address)             -> app/logic.py: get_token_risk
  5.  get_contract_health_audit(chain_id, contract_address)  -> app/logic.py: get_contract_health_audit
  6.  get_token_diagnostic(chain_id, contract_address)       -> app/logic.py: get_token_diagnostic
  7.  get_whale_position_audit(address)                      -> app/logic.py: get_whale_position_audit
  8.  get_funding_rate(symbol)                               -> app/logic.py: get_funding_rate
  9.  get_funding_apr_matrix(symbol, ...)                    -> app/logic.py: get_funding_apr_matrix
  10. get_dex_liquidity_slippage(...)                        -> app/logic.py: get_dex_liquidity_slippage
  11. get_arb_spread_matrix(...)                             -> app/logic.py: get_arb_spread_matrix
  12. get_macro_dday()                                       -> app/logic.py: get_macro_calendar_dday
  13. get_neg_risk_arbitrage(event_slug, ...)                -> app/logic.py: get_neg_risk_arbitrage
  14. get_exit_capacity_audit(position_size_shares, ...)     -> app/logic.py: get_exit_capacity_audit

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
  - The other 10 tools call the same read-only app/logic.py functions the
    paid REST/remote-MCP endpoints call - no separate implementation to keep
    in sync, and no extra upstream cost beyond what those functions already do.

Run locally:
    python mcp_server.py
Connect Claude Desktop:
    see the claude_desktop_config.json example included in the project root.
"""
import logging

import httpx

from mcp.server.fastmcp import FastMCP

from app.logic import (
    get_arb_spread_matrix as _logic_arb_spread_matrix,
    get_contract_health_audit as _logic_contract_health_audit,
    get_dex_liquidity_slippage as _logic_dex_liquidity_slippage,
    get_exit_capacity_audit as _logic_exit_capacity_audit,
    get_funding_apr_matrix as _logic_funding_apr_matrix,
    get_funding_rate as _logic_funding_rate,
    get_kimchi_alert as _logic_kimchi_alert,
    get_macro_calendar_dday as _logic_macro_calendar_dday,
    get_neg_risk_arbitrage as _logic_neg_risk_arbitrage,
    get_symbol_dump_risk,
    get_token_diagnostic as _logic_token_diagnostic,
    get_token_risk as _logic_token_risk,
    get_whale_position_audit as _logic_whale_position_audit,
)
from app.markdown_tool import url_to_markdown

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("alphapipeline-mcp")

mcp = FastMCP("alphapipeline")


def _error(error_type: str, message: str) -> dict:
    """Unified structured error shape that's easy for an AI agent to parse."""
    return {"success": False, "error": {"type": error_type, "message": message}}


async def _safe_call(op_name: str, coro) -> dict:
    """Shared try/except wrapper so every tool below reports failures the same way."""
    try:
        data = await coro
        return {"success": True, **data}
    except ValueError as e:
        return _error("invalid_input", str(e))
    except httpx.TimeoutException:
        return _error("timeout", f"{op_name}: upstream request timed out.")
    except httpx.HTTPStatusError as e:
        return _error("http_error", f"{op_name}: upstream returned HTTP {e.response.status_code}.")
    except httpx.RequestError as e:
        return _error("network_error", f"{op_name}: could not reach upstream: {e}")
    except Exception as e:
        logger.exception("Unknown error while processing %s", op_name)
        return _error("unknown_error", f"An unknown error occurred: {e}")


@mcp.tool()
async def convert_to_markdown(url: str) -> dict:
    """
    Convert a webpage URL into clean, AI-friendly markdown by performing a live
    HTTP GET (15s timeout) and stripping ads, navigation, and scripts, keeping
    only the main content (capped at 3MB of source HTML).

    Use this when you need to read a webpage's actual content but want to avoid
    wasting tokens on HTML tags, ads, navigation menus, and scripts - or when raw
    HTML parsing is causing hallucinations in downstream reasoning. Call it before
    summarizing, extracting facts from, or answering questions about any arbitrary
    public URL. Do NOT use it for: URLs requiring authentication/login (no cookies
    or headers are sent); non-HTML resources such as PDFs, images, or other binary
    files (returns "unsupported_content"); or JavaScript-rendered single-page apps
    (this does a static HTML fetch, not a browser render, so client-side-only
    content may come back sparse or empty). No other tool here does markdown
    conversion - the sibling get_token_dump_risk is unrelated.

    Failure modes returned as structured errors (never raised): "timeout" (15s
    exceeded), "http_error" (non-2xx from the target site), "network_error"
    (DNS/connection failure), "unsupported_content" (not HTML or over 3MB),
    "invalid_url" (missing http(s):// scheme).

    Args:
        url: The full absolute URL of the webpage to convert, including scheme
            (e.g. "https://example.com/article"). Relative paths are not accepted.

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

    Use this to evaluate token unlock schedules, vesting cliffs, and upcoming
    VC/team dump pressure relative to circulating supply before taking mid-to-
    long term positions. Do NOT use it for intra-day slippage or real-time
    transaction simulation - use dex.liquidity_slippage for that instead. This
    tool is free (no payment) as an onboarding check; every other tool here is
    a normal read-only call against app/logic.py.

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


@mcp.tool(name="market.kimchi_alert")
async def get_kimchi_alert(symbol: str = "BTC") -> dict:
    """
    Detect Korea-vs-global crypto price arbitrage (the "kimchi premium"):
    whether a coin trades at a premium or discount on Upbit vs a global
    reference price (Coinbase spot, CoinGecko fallback - NOT a live Binance
    orderbook, despite the legacy binance_price_usdt field name kept for
    backward compatibility), a reverse-premium crash-risk flag (-1.5% or
    below), and a premium-surge flag (+3 percentage points within the last
    hour).

    Use this when asked about cross-exchange arbitrage opportunities in Korean
    crypto markets, to detect a reverse-premium crash risk, or a sudden premium
    surge. This is a live snapshot only - do NOT use it for historical/backtesting
    data, or for non-Korean-exchange comparisons (use arb.spread_matrix for a
    general CEX-DEX spread check instead).

    Args:
        symbol: Ticker symbol, e.g. "BTC", "ETH", "SOL" (default "BTC").

    Returns:
        On success: {"success": true, "symbol", "upbit_price_krw",
            "binance_price_usdt" (legacy name, actually Coinbase/CoinGecko),
            "cex_reference_price_usdt" (same value, honest name),
            "cex_price_source", "kimchi_premium_pct", "premium_change_1h_pct",
            "alerts", "thresholds", "notice", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_kimchi_alert", _logic_kimchi_alert(str(symbol).upper()))


@mcp.tool(name="security.token_risk")
async def get_token_risk(chain_id: int, contract_address: str) -> dict:
    """
    Check a token contract for honeypot/scam risk before buying: is_honeypot,
    buy/sell tax, mintability, open-source status, ownership renouncement,
    holder count, and a summarized risk_level (LOW/MEDIUM/HIGH/UNKNOWN).
    Data source: GoPlus Security, falling back to Honeypot.is.

    Use this right before entering a position on an unfamiliar or newly-listed
    token. Do NOT treat a null field as "safe" - it means that field could not
    be determined; check risk_level and risk_flags instead. This tool does not
    cover LP lock/burn status - use get_contract_health_audit for that, or
    get_token_diagnostic to get both in one call.

    Args:
        chain_id: EVM chain id, e.g. 8453 for Base.
        contract_address: Token contract address (0x...).

    Returns:
        On success: {"success": true, "is_honeypot", "buy_tax_pct", "sell_tax_pct",
            "risk_level", "risk_flags", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_token_risk", _logic_token_risk(chain_id, contract_address))


@mcp.tool(name="security.contract_health_audit")
async def get_contract_health_audit(chain_id: int, contract_address: str) -> dict:
    """
    Audit LP lock/burn status for a token contract (GoPlus): lp_locked_pct,
    lp_burned_pct, top_unlocked_holder_pct, rolled up into a liquidity_health
    category (LOCKED / PARTIALLY_LOCKED / UNLOCKED / NO_LP_DATA). A key
    rug-pull signal that get_token_risk does not cover.

    Use this to check whether a token's liquidity is locked, burned, or freely
    held by a single wallet before trusting it - complements, not replaces,
    get_token_risk (honeypot/tax/mint checks). Deliberately does NOT include any
    qualitative "suspicious transaction" judgment, only GoPlus's own numbers, and
    has no fallback if GoPlus fails (Honeypot.is does not expose LP lock data).

    Args:
        chain_id: EVM chain id, e.g. 8453 for Base.
        contract_address: Token contract address (0x...).

    Returns:
        On success: {"success": true, "lp_locked_pct", "lp_burned_pct",
            "liquidity_health", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_contract_health_audit", _logic_contract_health_audit(chain_id, contract_address)
    )


@mcp.tool(name="security.token_diagnostic")
async def get_token_diagnostic(chain_id: int, contract_address: str) -> dict:
    """
    Bundles get_token_risk + get_contract_health_audit into one combined
    diagnostic report (same GoPlus data, run in parallel, one upstream round
    trip), plus a deduped union of risk_flags. Does not compute a composite
    score/grade - every field is copied unchanged from the two underlying tools.

    Use this instead of calling get_token_risk and get_contract_health_audit
    separately when you want both honeypot/tax risk AND LP lock/burn status in
    one call. Do NOT use it if you only need one of the two - call that single
    tool directly to save a round trip. Does not cover token unlock/vesting
    risk - use get_token_dump_risk separately for that.

    Args:
        chain_id: EVM chain id, e.g. 8453 for Base.
        contract_address: Token contract address (0x...).

    Returns:
        On success: {"success": true, ...combined token_risk + contract_health_audit fields}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_token_diagnostic", _logic_token_diagnostic(chain_id, contract_address)
    )


@mcp.tool(name="derivatives.whale_position_audit")
async def get_whale_position_audit(address: str) -> dict:
    """
    Audit a Hyperliquid wallet's open perpetual futures positions: side,
    size, leverage, unrealized PnL, liquidation price, and distance-to-
    liquidation percentage for every open position.

    Use this only when you already have a specific Hyperliquid/EVM wallet address
    (from an explorer, a screenshot, on-chain sleuthing, etc.) and want to audit
    its current exposure. Do NOT use it to discover or rank "smart money" wallets
    - Hyperliquid's public API has no leaderboard or large-trader disclosure
    endpoint, so this tool cannot identify addresses for you, only audit ones you
    provide. risk_flags (HIGH_LEVERAGE, NEAR_LIQUIDATION) are fixed numeric
    thresholds, never a qualitative judgment.

    Args:
        address: Hyperliquid/EVM wallet address to audit (0x...).

    Returns:
        On success: {"success": true, "positions": [...], "risk_flags", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_whale_position_audit", _logic_whale_position_audit(address))


@mcp.tool(name="derivatives.funding_rate")
async def get_funding_rate(symbol: str) -> dict:
    """
    Get perpetual futures funding rate (Bybit primary, Binance fallback) to
    gauge long/short crowding before entering or hedging a position.

    Use this when asked about funding rate levels or funding-rate arbitrage/carry
    trade opportunities. If you also need the trade annualized into an APR with
    a carry-trade breakeven-days estimate, use get_funding_apr_matrix instead -
    it already calls this internally, so calling both is redundant. Check the
    data_source field to see whether Bybit or the Binance fallback answered;
    predicted_rate equals funding_rate because neither exchange exposes a
    separate forecast field (not a bug).

    Args:
        symbol: e.g. "BTC", "ETH", or "BTCUSDT" (non-USDT symbols are
            normalized to USDT pairs).

    Returns:
        On success: {"success": true, "funding_rate_percentage",
            "funding_interval_hours", "data_source", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_funding_rate", _logic_funding_rate(symbol))


@mcp.tool(name="derivatives.funding_apr_matrix")
async def get_funding_apr_matrix(symbol: str, assumed_round_trip_cost_pct: float = 0.2) -> dict:
    """
    Annualize a perpetual funding rate into APR + carry-trade breakeven days:
    evaluates a spot+perpetual cash-and-carry trade, tells you which side
    collects funding, and how many days of income recoups round-trip costs.
    Pure calculation layer on top of get_funding_rate - no extra upstream call,
    so prefer this over get_funding_rate whenever you need the APR/breakeven
    view rather than the raw rate.

    Use this to evaluate a spot+perpetual carry trade. Does NOT account for
    margin borrow cost, spot-perp basis risk, or perp liquidation risk - treat
    breakeven_days as a rough estimate, not a guaranteed profit timeline.

    Args:
        symbol: e.g. "BTC", "ETH", or "BTCUSDT".
        assumed_round_trip_cost_pct: Combined entry+exit trading fee % across
            both legs (default 0.2). Pass your own fee tier for accuracy.

    Returns:
        On success: {"success": true, "apr_pct", "collects_funding_side",
            "breakeven_days", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_funding_apr_matrix",
        _logic_funding_apr_matrix(symbol, assumed_round_trip_cost_pct=assumed_round_trip_cost_pct),
    )


@mcp.tool(name="dex.liquidity_slippage")
async def get_dex_liquidity_slippage(
    trade_size_usd: float,
    network: str = "base",
    pool_address: str | None = None,
    token_address: str | None = None,
) -> dict:
    """
    Estimate DEX pool liquidity and trade slippage (GeckoTerminal): total USD
    liquidity, 24h volume, an estimated slippage percentage for the given
    trade size, and slippage_tiers at fixed $1k/$5k/$10k sizes. Approximated
    under a documented constant-product (50:50) assumption since GeckoTerminal's
    free API exposes only combined USD liquidity, not per-token reserves.

    Also returns quote_token_is_stablecoin (false/null means this pool isn't
    USD-quoted - an extra hop is needed to reach USD, not accounted for here),
    pool_fee_pct (the pool's swap fee tier, disclosed for reference only - not
    subtracted from the slippage estimate), and assumed_gas_cost_usd (a flat
    per-network estimate, not a live quote).

    Use this before sizing a trade or comparing pools for a given token, to
    check depth before swapping. Do NOT treat estimated_slippage_pct or
    slippage_tiers as an exact on-chain quote, especially for concentrated-
    liquidity or stableswap pools - always re-verify with a live quote before
    executing. Requires exactly one of pool_address or token_address; passing
    neither raises an "invalid_input" error.

    Args:
        trade_size_usd: Hypothetical trade size in USD.
        network: GeckoTerminal network id, e.g. "base", "eth" (default "base").
        pool_address: A specific pool contract address.
        token_address: Token contract address (auto-picks the most liquid pool).
            One of pool_address or token_address is required.

    Returns:
        On success: {"success": true, "total_liquidity_usd",
            "estimated_slippage_pct", "slippage_tiers", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_dex_liquidity_slippage",
        _logic_dex_liquidity_slippage(
            network=network,
            trade_size_usd=trade_size_usd,
            pool_address=pool_address,
            token_address=token_address,
        ),
    )


@mcp.tool(name="arb.spread_matrix")
async def get_arb_spread_matrix(
    symbol: str,
    network: str = "base",
    trade_size_usd: float = 1000.0,
    min_spread_threshold_pct: float = 0.8,
    pool_address: str | None = None,
    token_address: str | None = None,
) -> dict:
    """
    CEX-DEX arbitrage spread calculator: checks whether a global reference
    price (Coinbase spot, CoinGecko fallback - NOT a specific exchange
    orderbook, despite internal naming) and a DEX pool price (GeckoTerminal)
    diverge enough to be worth trading after an assumed flat gas cost. Returns
    gross/net spread, direction, and is_profitable.

    Use this before executing a cross-venue arbitrage trade, or for kimchi-style
    premium checks on non-Korean venues (use market.kimchi_alert instead for the
    Upbit-specific case). Does NOT account for CEX deposit/withdrawal
    availability, trading fees, or slippage beyond trade_size_usd - always
    re-verify with live quotes before executing. Requires exactly one of
    pool_address or token_address; passing neither raises "invalid_input".

    Args:
        symbol: Ticker symbol, e.g. "SUI", "BTC", "ETH".
        network: GeckoTerminal network id (default "base").
        trade_size_usd: Hypothetical trade size in USD (default 1000.0).
        min_spread_threshold_pct: Net spread threshold (%) above which
            is_profitable is true (default 0.8).
        pool_address: A specific DEX pool contract address.
        token_address: Token contract address (auto-picks the most liquid pool).
            One of pool_address or token_address is required.

    Returns:
        On success: {"success": true, "gross_spread_pct", "net_spread_pct",
            "direction", "is_profitable", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_arb_spread_matrix",
        _logic_arb_spread_matrix(
            symbol=symbol,
            network=network,
            trade_size_usd=trade_size_usd,
            pool_address=pool_address,
            token_address=token_address,
            min_spread_threshold_pct=min_spread_threshold_pct,
        ),
    )


@mcp.tool(name="calendar.macro_dday")
async def get_macro_dday() -> dict:
    """
    Countdown to the nearest major US macro event (FOMC/CPI/NFP): event name,
    exact date/time (UTC and KST), a D-Day countdown, impact level, and the
    next few upcoming events. Static, pre-loaded 2026 calendar sourced from
    official Federal Reserve/BLS release schedules - no live external API call,
    so this never fails on an upstream outage and takes no parameters.

    Use this to plan position sizing or avoid holding risk into a high-impact
    macro print. Does NOT cover non-US events (e.g. ECB, BOJ) or company
    earnings - only FOMC/CPI/NFP are tracked. Takes no arguments; calling it
    with any input is unnecessary.

    Returns:
        On success: {"success": true, "nearest_event", "days_until", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_macro_dday", _logic_macro_calendar_dday())


@mcp.tool(name="prediction.neg_risk_arbitrage")
async def get_neg_risk_arbitrage(
    event_slug: str,
    assumed_round_trip_cost_pct: float = 1.5,
    max_slippage_pct: float = 1.0,
    min_net_edge_pct: float = 1.0,
) -> dict:
    """
    Detect basket arbitrage in a Polymarket neg-risk (mutually-exclusive,
    multi-outcome) event: a full YES basket across all outcomes always settles
    to exactly $1 via Polymarket's neg-risk adapter, so a basket price away
    from $1 (after costs) is a near risk-free edge. Also computes
    buy/sell_basket_capacity_shares - the actual liquidity-bottleneck size the
    thinnest outcome's order book can support within max_slippage_pct - so
    this isn't just a top-of-book mirage. Polymarket only.

    Use this to scan a specific multi-outcome event you already know the slug
    for. Do NOT use for binary Yes/No markets (no basket to arbitrage, this
    needs 2+ mutually-exclusive outcomes) or for Kalshi (its Data ToS forbids
    this use of their data). Pair with prediction.exit_capacity_audit before
    sizing a real position on one leg.

    Args:
        event_slug: Polymarket event slug, from the event's URL on polymarket.com.
        assumed_round_trip_cost_pct: Gas + fees + slippage buffer, as a
            percentage of $1 basket notional (default 1.5).
        max_slippage_pct: How far past each leg's best price to walk the book
            when sizing executable basket capacity (default 1.0).
        min_net_edge_pct: Minimum net edge (%) required to flag
            arbitrage_viable: true (default 1.0).

    Returns:
        On success: {"success": true, "basket_ask_sum", "basket_bid_sum",
            "buy_basket_net_edge_usd", "buy_basket_capacity_shares",
            "opportunity", "arbitrage_viable", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_neg_risk_arbitrage",
        _logic_neg_risk_arbitrage(
            event_slug,
            assumed_round_trip_cost_pct=assumed_round_trip_cost_pct,
            max_slippage_pct=max_slippage_pct,
            min_net_edge_pct=min_net_edge_pct,
        ),
    )


@mcp.tool(name="prediction.exit_capacity_audit")
async def get_exit_capacity_audit(
    position_size_shares: float,
    token_id: str | None = None,
    market_slug: str | None = None,
    outcome: str = "yes",
    side: str = "sell",
) -> dict:
    """
    Walk a single Polymarket outcome's live order book to determine how much
    of a given position size can actually be filled right now, at what
    average price, and with how much price impact versus the best quote - a
    live, point-in-time snapshot, not historical/average liquidity.

    Use this to validate one leg of an opportunity found by
    prediction.neg_risk_arbitrage before committing capital, or whenever you
    need real executable liquidity rather than the headline best bid/ask. Do
    NOT use for multi-outcome basket arbitrage detection (use
    prediction.neg_risk_arbitrage instead). market_slug only resolves an
    EXACT Polymarket market slug - no fuzzy keyword search, since a wrong
    silent match would be worse than an error here.

    Args:
        position_size_shares: Number of outcome shares to sell (or buy). Must
            be positive.
        token_id: The outcome's CLOB token_id / asset_id, if already known.
            Provide either this OR market_slug.
        market_slug: Exact Polymarket market slug, used to resolve token_id
            automatically when not already known.
        outcome: "yes" (default) or "no" - which side to resolve when using
            market_slug. Ignored if token_id is given directly.
        side: "sell" (default) to audit exiting a position against the bid
            side, or "buy" to audit entering against the ask side.

    Returns:
        On success: {"success": true, "executable", "best_quote",
            "avg_exit_price", "price_impact_pct", "max_executable_shares", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_exit_capacity_audit",
        _logic_exit_capacity_audit(
            position_size_shares,
            token_id=token_id,
            market_slug=market_slug,
            outcome=outcome,
            side=side,
        ),
    )


if __name__ == "__main__":
    # stdio transport: the standard way Claude Desktop/Cursor runs this script
    # directly as a local process and talks to it over stdin/stdout.
    mcp.run(transport="stdio")
