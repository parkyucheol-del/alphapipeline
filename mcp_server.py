"""
AlphaPipeline MCP server (legacy, free, stdio transport).

Reuses the logic from the existing FastAPI pipeline (app/*), wrapped so MCP
clients like Claude Desktop or Cursor can pick it up directly as a local tool.
Also the exact server Glama's automated Docker build test introspects for
this listing's "Server" score, so its tool count should track app/mcp_server.py
(the paid remote server) 1:1 even though calls here bypass payment entirely.

28 tools provided (same coverage as the paid remote /mcp server, just called
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
  15. get_hip4_snapshot(template, underlying, limit)         -> app/logic.py: get_hip4_snapshot
  16. get_hip4_price_ladder(underlying, limit)                -> app/logic.py: get_hip4_price_ladder
  17. get_btc_cycle_compare()                                 -> app/logic.py: get_btc_cycle_compare
  18. get_oracle_gap(coin)                                    -> app/logic.py: get_oracle_gap
  19. get_funding_rate_value(symbol)                          -> app/logic.py: get_funding_rate_value
  20. get_oracle_gap_bp(coin)                                  -> app/logic.py: get_oracle_gap_bp
  21. get_hip4_price(underlying, strike)                       -> app/logic.py: get_hip4_price
  22. get_hip4_mispricing(underlying)                           -> app/logic.py: get_hip4_mispricing
  23. get_order_split_guide(position_size_shares, ...)            -> app/logic.py: get_order_split_guide
  24. get_bid_ask_spread_pct(token_id, ...)                        -> app/logic.py: get_bid_ask_spread_pct
  25. get_orderbook_imbalance_ratio(token_id, ...)                 -> app/logic.py: get_orderbook_imbalance_ratio
  26. get_liquidity_depth_after_haircut(token_id, ...)             -> app/logic.py: get_liquidity_depth_after_haircut
  27. get_polymarket_fee_estimate(shares, ...)                       -> app/logic.py: get_polymarket_fee_estimate
  28. get_polymarket_mispricing(strikes, ...)                        -> app/logic.py: get_polymarket_mispricing

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
  - The other 11 tools call the same read-only app/logic.py functions the
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
    get_btc_cycle_compare as _logic_btc_cycle_compare,
    get_contract_health_audit as _logic_contract_health_audit,
    get_dex_liquidity_slippage as _logic_dex_liquidity_slippage,
    get_exit_capacity_audit as _logic_exit_capacity_audit,
    get_funding_apr_matrix as _logic_funding_apr_matrix,
    get_funding_rate as _logic_funding_rate,
    get_funding_rate_value as _logic_funding_rate_value,
    get_hip4_mispricing as _logic_hip4_mispricing,
    get_hip4_price as _logic_hip4_price,
    get_hip4_price_ladder as _logic_hip4_price_ladder,
    get_hip4_snapshot as _logic_hip4_snapshot,
    get_kimchi_alert as _logic_kimchi_alert,
    get_macro_calendar_dday as _logic_macro_calendar_dday,
    get_neg_risk_arbitrage as _logic_neg_risk_arbitrage,
    get_bid_ask_spread_pct as _logic_bid_ask_spread_pct,
    get_liquidity_depth_after_haircut as _logic_liquidity_depth_after_haircut,
    get_polymarket_fee_estimate as _logic_polymarket_fee_estimate,
    get_polymarket_mispricing as _logic_polymarket_mispricing,
    get_oracle_gap as _logic_oracle_gap,
    get_oracle_gap_bp as _logic_oracle_gap_bp,
    get_orderbook_imbalance_ratio as _logic_orderbook_imbalance_ratio,
    get_order_split_guide as _logic_order_split_guide,
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

    Use this to evaluate token unlock/vesting supply overhang risk before
    taking mid-to-long term positions. Default data source (on-chain Sablier
    vesting, the current configuration - no DropsTab key set) does NOT classify
    VC/team vs. other holders and does NOT provide exact unlock timing
    (days_until_unlock stays null) - it only reports the currently-locked
    supply ratio; treat a null value as "unknown," never as "no risk." On this
    on-chain path it also reports vesting_deposit_amount/vesting_withdrawn_amount
    (Sablier's own depositAmount/withdrawnAmount, aggregated across streams) and
    vesting_progress_pct (withdrawn/deposit * 100) showing how far along the
    vesting schedule already is - null when deposit data wasn't available. Do
    NOT use it for intra-day slippage or real-time transaction simulation - use
    dex.liquidity_slippage for that instead. This tool is free (no payment) as
    an onboarding check; every other tool here is a normal read-only call
    against app/logic.py.

    Args:
        symbol: Token ticker symbol (e.g. "ATH", "AO", "CPOOL"). Case-insensitive.

    Returns:
        Success & data available: {"success": true, "available": true, "symbol",
            "unlock_date_utc", "days_until_unlock", "unlock_supply_pct",
            "vesting_progress_pct", "volume_impact_pct", "sell_pressure_risk_level", ...}
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
    holder count, individual GoPlus risk signals (cannot_buy, cannot_sell_all,
    hidden_owner, transfer_pausable, selfdestruct, is_blacklisted,
    slippage_modifiable, owner_percent - all feed into risk_flags), is_proxy
    and trading_cooldown (informational only, not flagged - both are common in
    legitimate contracts), and a summarized risk_level (LOW/MEDIUM/HIGH/UNKNOWN).
    Data source: GoPlus Security, falling back to Honeypot.is (the GoPlus-only
    fields above are always null on the fallback path).

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
    lp_burned_pct, top_unlocked_holder_pct (largest single non-locked holder)
    and top5_unlocked_holder_pct (top 5 non-locked holders summed, catches
    multi-wallet concentration the single-holder field misses), rolled up
    into a liquidity_health category (LOCKED / PARTIALLY_LOCKED / UNLOCKED /
    NO_LP_DATA). A key rug-pull signal that get_token_risk does not cover.

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

    Use this right before buying or swapping an unfamiliar token when you want
    both honeypot/tax risk AND LP lock/burn status in one call, instead of
    calling get_token_risk and get_contract_health_audit separately. Do NOT
    use it if you only need one of the two - call that single tool directly
    to save a round trip. Does not cover token unlock/vesting risk - use
    get_token_dump_risk separately for that.

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
    size, leverage, max_leverage, unrealized PnL, return_on_equity_pct,
    liquidation price, and distance-to-liquidation percentage for every open
    position (max_leverage and return_on_equity_pct are Hyperliquid's own
    reported fields, not derived by this tool).

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
    separate forecast field (not a bug). Also returns mark_price/index_price
    (populated on both the Bybit and Binance paths) and open_interest_usd
    (Bybit path only - always null on the Binance fallback, since Binance's
    premiumIndex endpoint doesn't report open interest).

    Args:
        symbol: e.g. "BTC", "ETH", or "BTCUSDT" (non-USDT symbols are
            normalized to USDT pairs).

    Returns:
        On success: {"success": true, "funding_rate_percentage",
            "funding_interval_hours", "mark_price", "index_price",
            "open_interest_usd", "data_source", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_funding_rate", _logic_funding_rate(symbol))


@mcp.tool(name="derivatives.funding_rate_value")
async def get_funding_rate_value(symbol: str) -> dict:
    """
    Cheap-tier thin wrapper around derivatives.funding_rate: same call, same
    cache, but trims the response to just the funding rate numbers (no
    mark/index price, no open_interest_usd). No new upstream call. Use this
    instead of get_funding_rate when all you need is the raw funding rate
    value/percentage.

    Args:
        symbol: e.g. "BTC", "ETH", or "BTCUSDT" (non-USDT symbols are
            normalized to USDT pairs).

    Returns:
        On success: {"success": true, "funding_rate", "funding_rate_percentage",
            "data_source", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_funding_rate_value", _logic_funding_rate_value(symbol))


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
    Upbit-specific case). net_spread_pct does NOT subtract the DEX pool's own
    swap fee (see pool_fee_pct, typically 0.05-1%), CEX trading fees, CEX
    deposit/withdrawal availability, or slippage beyond trade_size_usd - a
    spread that clears the threshold before those costs may not clear it after,
    so always re-verify with live quotes before executing. Requires exactly one
    of pool_address or token_address; passing neither raises "invalid_input".

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
    this isn't just a top-of-book mirage. Note *_capacity_notional_usd still
    prices that size at top-of-book (optimistic beyond the first price level) -
    use *_capacity_vwap_notional_usd for the realistic average fill cost. Also
    returns oldest_book_snapshot_time, the staleness bottleneck across all legs
    (the oldest of each leg's own order-book snapshot timestamp). Polymarket only.

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
    live, point-in-time snapshot, not historical/average liquidity. Also
    returns book_snapshot_time (the book's own reported snapshot timestamp)
    and Polymarket's own tick_size/min_order_size for this market (null if the
    book response didn't include them).

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


@mcp.tool(name="prediction.hip4_snapshot")
async def get_hip4_snapshot(
    template: str | None = None,
    underlying: str | None = None,
    limit: int = 100,
) -> dict:
    """
    Real-time probability snapshot of Hyperliquid's HIP-4 outcome (prediction)
    markets - crypto price binaries, sports game winners, tournament winners,
    Fed rate decisions, and any other market type Hyperliquid adds, all in one
    call. No curated market list: every field under "fields" on each row is
    parsed verbatim from Hyperliquid's own "key:value|key2:value2" description
    string, so new HIP-4 market types appear automatically without any code
    change here. standalone_markets covers two-sided markets (most crypto/
    sports games); grouped_questions covers mutually-exclusive multi-outcome
    groups (e.g. a league winner) with a fallback price for "none of the
    above".

    Do NOT use for Polymarket data (use prediction.neg_risk_arbitrage /
    prediction.exit_capacity_audit instead). No wallet or API key needed
    upstream - always check the response's `notice` field, since the mapping
    used to join Hyperliquid's market metadata to its live prices is not
    documented by Hyperliquid and was reverse-engineered from live data.

    Args:
        template: Optional substring filter on the market's template name
            (e.g. "sportsContestWinner", "priceBinary").
        underlying: Optional asset symbol filter for crypto markets (e.g.
            "BTC", "ETH", "SOL", "HYPE").
        limit: Max rows per list (standalone_markets / grouped_questions
            each). Defaults to 100, max 500.

    Returns:
        On success: {"success": true, "standalone_markets", "grouped_questions",
            "standalone_count", "grouped_question_count", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_hip4_snapshot",
        _logic_hip4_snapshot(template=template, underlying=underlying, limit=limit),
    )


@mcp.tool(name="prediction.hip4_price_ladder")
async def get_hip4_price_ladder(underlying: str, limit: int = 100) -> dict:
    """
    One underlying's Hyperliquid HIP-4 "above $X" price-binary markets laid
    out as a strike-sorted ladder, each rung with its live Yes probability -
    e.g. to read the market's implied BTC price distribution at a glance.
    Reuses the same cached data as prediction.hip4_snapshot - no new upstream
    call. No volume field: Hyperliquid's public API does not expose
    per-outcome trading volume for HIP-4 markets.

    Do NOT use for mutually-exclusive multi-outcome groups (use
    prediction.hip4_snapshot's grouped_questions instead) or for Polymarket
    data. Always check the response's `notice` field - same
    reverse-engineered-mapping caveat as prediction.hip4_snapshot.

    Args:
        underlying: Asset symbol for crypto price-binary markets (e.g. "BTC",
            "ETH", "SOL", "HYPE").
        limit: Max rungs returned. Defaults to 100, max 500.

    Returns:
        On success: {"success": true, "underlying", "rung_count",
            "skipped_unparsable_count", "rungs", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_hip4_price_ladder",
        _logic_hip4_price_ladder(underlying=underlying, limit=limit),
    )


@mcp.tool(name="prediction.hip4_price")
async def get_hip4_price(underlying: str, strike: float) -> dict:
    """
    Cheap-tier single-rung lookup: reuses prediction.hip4_price_ladder's
    cached data and returns only the one rung whose strike is closest to
    the requested strike (no new upstream call). Always check
    matched_strike against your requested strike - a large gap means no
    rung exists near your target, not an exact match. Use
    prediction.hip4_price_ladder instead if you need the whole ladder.

    Args:
        underlying: Asset symbol for crypto price-binary markets (e.g. "BTC",
            "ETH", "SOL", "HYPE").
        strike: Target price level to find the closest HIP-4 rung for.

    Returns:
        On success: {"success": true, "underlying", "requested_strike",
            "matched_strike", "yes_price", "expiry", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_hip4_price",
        _logic_hip4_price(underlying=underlying, strike=strike),
    )


@mcp.tool(name="prediction.hip4_mispricing")
async def get_hip4_mispricing(underlying: str) -> dict:
    """
    Finds monotonicity violations among Hyperliquid HIP-4 "above $X"
    price-binary markets: within the same underlying/expiry/period, a
    higher strike's Yes price should never exceed a lower strike's - any
    reversal is a logical inconsistency, quantified as violation_bp and a
    textbook max_riskless_profit_per_usd (before fees/slippage/execution
    risk - not investment advice). Reuses prediction.hip4_price_ladder's
    cache (no new upstream call). Only rungs sharing the exact same
    (expiry, period) are ever compared - see settlement_condition_note.

    Args:
        underlying: Asset symbol for crypto price-binary markets (e.g. "BTC",
            "ETH", "SOL", "HYPE").

    Returns:
        On success: {"success": true, "underlying", "expiry_groups_checked",
            "total_rungs_checked", "violation_count", "violations",
            "settlement_condition_note", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_hip4_mispricing",
        _logic_hip4_mispricing(underlying=underlying),
    )


@mcp.tool(name="prediction.order_split_guide")
async def get_order_split_guide(
    position_size_shares: float,
    token_id: str | None = None,
    market_slug: str | None = None,
    outcome: str = "yes",
    side: str = "sell",
    max_price_impact_pct_per_slice: float = 1.0,
) -> dict:
    """
    Walks a Polymarket outcome's live order book to recommend how many clips
    to split a large position into, so each clip's own price impact stays at
    or under max_price_impact_pct_per_slice (default 1.0%). Also returns
    full_size_price_impact_pct - the baseline impact if filled in one shot
    right now, for comparison. Single-snapshot calculation - it has no idea
    whether the book refills between clips in real trading, so treat the
    recommended slice count as "if this exact snapshot repeated", not a
    guarantee about live execution. Not investment advice - no confidence or
    go/no-go field is returned, only the raw numbers.

    Reuses the same order-book walking helpers as
    prediction.exit_capacity_audit - no new upstream call. market_slug only
    resolves an EXACT Polymarket market slug - no fuzzy keyword search.

    Args:
        position_size_shares: Total number of outcome shares to sell (or
            buy). Must be positive.
        token_id: The outcome's CLOB token_id / asset_id, if already known.
            Provide either this OR market_slug.
        market_slug: Exact Polymarket market slug, used to resolve token_id
            automatically when not already known.
        outcome: "yes" (default) or "no" - which side to resolve when using
            market_slug. Ignored if token_id is given directly.
        side: "sell" (default) or "buy".
        max_price_impact_pct_per_slice: Target ceiling on each clip's own
            price impact, in percent. Defaults to 1.0.

    Returns:
        On success: {"success": true, "executable_now",
            "full_size_price_impact_pct", "max_single_clip_shares",
            "recommended_slice_count", "recommended_slice_size_shares", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_order_split_guide",
        _logic_order_split_guide(
            position_size_shares=position_size_shares,
            token_id=token_id,
            market_slug=market_slug,
            outcome=outcome,
            side=side,
            max_price_impact_pct_per_slice=max_price_impact_pct_per_slice,
        ),
    )


@mcp.tool(name="prediction.bid_ask_spread_pct")
async def get_bid_ask_spread_pct(
    token_id: str | None = None,
    market_slug: str | None = None,
    outcome: str = "yes",
) -> dict:
    """
    Cheap tier - a single Polymarket outcome's live top-of-book bid/ask spread
    as a percentage of mid_price. Not a liquidity or executability measure
    (use prediction.exit_capacity_audit/order_split_guide for that). Reuses
    the same order-book fetch as those tools - no new upstream call.

    Args:
        token_id: The outcome's CLOB token_id / asset_id, if already known.
            Provide either this OR market_slug.
        market_slug: Exact Polymarket market slug, used to resolve token_id
            automatically when not already known.
        outcome: "yes" (default) or "no" - which side to resolve when using
            market_slug. Ignored if token_id is given directly.

    Returns:
        On success: {"success": true, "best_bid", "best_ask", "mid_price",
            "spread_abs", "spread_pct", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_bid_ask_spread_pct",
        _logic_bid_ask_spread_pct(
            token_id=token_id,
            market_slug=market_slug,
            outcome=outcome,
        ),
    )


@mcp.tool(name="prediction.orderbook_imbalance_ratio")
async def get_orderbook_imbalance_ratio(
    token_id: str | None = None,
    market_slug: str | None = None,
    outcome: str = "yes",
    depth_band_pct: float = 5.0,
) -> dict:
    """
    A single Polymarket outcome's bid-side vs ask-side order-book depth
    ratio, summed within depth_band_pct (default 5.0%) of each side's own
    best quote. 0.5 = balanced, closer to 1.0 = bid-side depth dominant,
    closer to 0.0 = ask-side dominant. A descriptive statistic only - not a
    prediction of price direction, no confidence or verdict field. Reuses
    the same order-book fetch as prediction.exit_capacity_audit - no new
    upstream call.

    Args:
        token_id: The outcome's CLOB token_id / asset_id, if already known.
            Provide either this OR market_slug.
        market_slug: Exact Polymarket market slug, used to resolve token_id
            automatically when not already known.
        outcome: "yes" (default) or "no" - which side to resolve when using
            market_slug. Ignored if token_id is given directly.
        depth_band_pct: How far into the book, as a percent of each side's
            own best quote, to sum depth. Defaults to 5.0.

    Returns:
        On success: {"success": true, "bid_depth_shares", "ask_depth_shares",
            "imbalance_ratio", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_orderbook_imbalance_ratio",
        _logic_orderbook_imbalance_ratio(
            token_id=token_id,
            market_slug=market_slug,
            outcome=outcome,
            depth_band_pct=depth_band_pct,
        ),
    )


@mcp.tool(name="prediction.liquidity_depth_after_haircut")
async def get_liquidity_depth_after_haircut(
    token_id: str | None = None,
    market_slug: str | None = None,
    outcome: str = "yes",
    side: str = "sell",
    haircut_pct: float = 10.0,
    max_price_impact_pct: float = 5.0,
) -> dict:
    """
    A single Polymarket outcome's executable depth within
    max_price_impact_pct (default 5.0%) of the best quote, both before and
    after discounting the top-of-book level's size by haircut_pct (default
    10.0%) to model the risk that the very best resting order gets cancelled
    before you can trade against it. Does not detect or flag actual spoofing
    (out of scope - see architecture-roadmap.md Phase 3). Reuses the same
    order-book fetch as prediction.exit_capacity_audit/order_split_guide -
    no new upstream call.

    Args:
        token_id: The outcome's CLOB token_id / asset_id, if already known.
            Provide either this OR market_slug.
        market_slug: Exact Polymarket market slug, used to resolve token_id
            automatically when not already known.
        outcome: "yes" (default) or "no" - which side to resolve when using
            market_slug. Ignored if token_id is given directly.
        side: "sell" (default) or "buy".
        haircut_pct: Percent to discount the top-of-book level's size by,
            modeling cancellation risk. Defaults to 10.0.
        max_price_impact_pct: Price-impact bound (percent of best quote)
            within which depth is summed. Defaults to 5.0.

    Returns:
        On success: {"success": true, "best_quote",
            "depth_shares_before_haircut", "depth_shares_after_haircut", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_liquidity_depth_after_haircut",
        _logic_liquidity_depth_after_haircut(
            token_id=token_id,
            market_slug=market_slug,
            outcome=outcome,
            side=side,
            haircut_pct=haircut_pct,
            max_price_impact_pct=max_price_impact_pct,
        ),
    )


@mcp.tool(name="prediction.polymarket_fee_estimate")
async def get_polymarket_fee_estimate(
    shares: float,
    token_id: str | None = None,
    market_slug: str | None = None,
    outcome: str = "yes",
    side: str = "sell",
    price: float | None = None,
) -> dict:
    """
    Polymarket's own taker fee (NOT gas, slippage, or AlphaPipeline's own
    charge for this call) for a hypothetical fill of `shares` at `price`,
    using the official bell-curve formula shares * fee_rate * price *
    (1 - price). fee_rate is read live from Polymarket's own GET /fee-rate
    for the exact token_id - never a hardcoded category table, since
    per-category rates have changed before (see architecture-roadmap.md
    Engineering Precision Backlog item 3). Makers pay 0 across every
    category - this is the taker fee only. Omit price to use the live best
    book quote for side (reuses the same order-book fetch as
    prediction.exit_capacity_audit/order_split_guide - no new upstream call
    in that case).

    Args:
        shares: Hypothetical fill size, in shares. Must be > 0.
        token_id: The outcome's CLOB token_id / asset_id, if already known.
            Provide either this OR market_slug.
        market_slug: Exact Polymarket market slug, used to resolve token_id
            automatically when not already known.
        outcome: "yes" (default) or "no" - which side to resolve when using
            market_slug. Ignored if token_id is given directly.
        side: "sell" (default) or "buy" - which side of the book to read
            price from when price is omitted.
        price: Hypothetical fill price (0-1 exclusive). Omit to use the
            live best book quote for side.

    Returns:
        On success: {"success": true, "fee_rate", "fee_usd", "price",
            "base_fee_bp", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_polymarket_fee_estimate",
        _logic_polymarket_fee_estimate(
            shares=shares,
            token_id=token_id,
            market_slug=market_slug,
            outcome=outcome,
            side=side,
            price=price,
        ),
    )


@mcp.tool(name="prediction.polymarket_mispricing")
async def get_polymarket_mispricing(
    strikes: list[float],
    token_ids: list[str] | None = None,
    market_slugs: list[str] | None = None,
    outcomes: list[str] | None = None,
) -> dict:
    """
    Monotonicity check (higher strike must not be priced above a lower
    strike) across a set of Polymarket markets that YOU have already
    confirmed share the same underlying asset and expiry/resolution date.
    Does NOT verify or infer that settlement-condition equivalence itself -
    Polymarket's API has no reliable field for it (see
    architecture-roadmap.md Engineering Precision Backlog item 4); passing
    unrelated markets produces a meaningless or false arbitrage signal.
    Provide either token_ids or market_slugs (+ optional outcomes, default
    all "yes"), same length and order as strikes. Not investment advice.

    Args:
        strikes: Strike for each rung, same order as token_ids/market_slugs.
            At least 2 required.
        token_ids: CLOB token_id per rung, if already known. Mutually
            exclusive with market_slugs.
        market_slugs: Polymarket market slug per rung, used to resolve
            token_ids automatically.
        outcomes: "yes"/"no" per rung when using market_slugs (default: all
            "yes").

    Returns:
        On success: {"success": true, "violation_count", "violations", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call(
        "get_polymarket_mispricing",
        _logic_polymarket_mispricing(
            strikes=strikes,
            token_ids=token_ids,
            market_slugs=market_slugs,
            outcomes=outcomes,
        ),
    )


@mcp.tool(name="market.cycle_compare")
async def get_btc_cycle_compare() -> dict:
    """
    BTC/ETH's own USD price position vs the same day-offset in the 2016 and
    2020 halving cycles, plus today's real BTC dominance. NOT a historical
    dominance comparison (no free data source exists for that - see the
    response's notice field). Derived from a cache refreshed once daily (see
    scripts/collect_btc_cycle_metrics.py), so prices can be up to ~24h stale.
    No input params.

    Returns:
        On success: {"success": true, "btc", "eth", "btc_dominance_pct_today",
            "dxy_level", "us10y_yield_pct", "days_since_halving", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_btc_cycle_compare", _logic_btc_cycle_compare())


@mcp.tool(name="market.oracle_gap")
async def get_oracle_gap(coin: str) -> dict:
    """
    Single-snapshot gap between Hyperliquid's mid price and its mark/oracle
    prices for one perpetual - the exact failure mode behind the 2026-10-10
    Papertrade incident on HyperEVM, where a protocol priced off Hyperliquid's
    raw mid instead of mark/oracle and an attacker moved ~$2.31B notional on
    Hyperliquid itself to swing the mid ~12bp, netting ~$1.28M from Papertrade.
    Returns mid_oracle_gap_bp and mark_oracle_gap_bp in basis points, plus an
    `elevated` flag.

    `elevated` is a fixed-threshold boolean (abs(mid_oracle_gap_bp) >=
    alert_threshold_bp), NOT a manipulation determination - it only says the
    gap is currently wide, not why. This is a single point-in-time snapshot
    with no trade-flow or volume data (Hyperliquid's public API does not
    expose per-market volume), so it cannot by itself distinguish organic
    volatility from an attempted price push. Always check the response's
    `notice` field.

    Args:
        coin: Hyperliquid perpetual ticker, e.g. "ETH" or "BTC".

    Returns:
        On success: {"success": true, "coin", "mid_price", "mark_price",
            "oracle_price", "mid_oracle_gap_bp", "mark_oracle_gap_bp",
            "elevated", "alert_threshold_bp", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_oracle_gap", _logic_oracle_gap(coin))


@mcp.tool(name="market.oracle_gap_bp")
async def get_oracle_gap_bp(coin: str) -> dict:
    """
    Cheap-tier thin wrapper around market.oracle_gap: same call, same cache,
    but trims the response to just the bp gap numbers and the elevated flag
    (no absolute mid/mark/oracle prices). No new upstream call. Use this
    instead of get_oracle_gap when all you need is the gap size for a
    threshold check.

    Args:
        coin: Hyperliquid perpetual ticker, e.g. "ETH" or "BTC".

    Returns:
        On success: {"success": true, "coin", "mid_oracle_gap_bp",
            "mark_oracle_gap_bp", "elevated", "alert_threshold_bp", ...}
        On failure: {"success": false, "error": {"type", "message"}}
    """
    return await _safe_call("get_oracle_gap_bp", _logic_oracle_gap_bp(coin))


if __name__ == "__main__":
    # stdio transport: the standard way Claude Desktop/Cursor runs this script
    # directly as a local process and talks to it over stdin/stdout.
    mcp.run(transport="stdio")
