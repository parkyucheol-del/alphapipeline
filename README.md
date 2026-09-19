<p align="center">
  <img src="docs/banner.png" alt="AlphaPipeline banner" width="100%">
</p>

# AlphaPipeline

**A pre-flight safety gate for on-chain trading agents — rug-pull/honeypot checks before you buy, plus market/derivatives data — pay-per-call ($0.005–$0.03 USDC), no signup, no API key, no OAuth.** Check a token for honeypot/tax/liquidity risk before a swap, or pull funding rates, DEX slippage, Korea price premiums, and Polymarket arbitrage signals. Authenticate and pay in a single request via the [x402 protocol](https://docs.x402.org) (HTTP 402) on Base, or call it as a remote MCP server from Claude Desktop, Cursor, or any MCP client.

[![Smithery](https://img.shields.io/badge/Smithery-Listed-orange)](https://smithery.ai/servers/parkyucheol/alphapipeline)
[![x402 Bazaar](https://img.shields.io/badge/x402-Bazaar-blue)](https://www.x402bazaar.org/)
[![Glama](https://img.shields.io/badge/Glama-Verified%20A%20(4.4%2F5)-brightgreen)](https://glama.ai/mcp/connectors/com.onrender.alphapipeline/alpha-pipeline-agent-mcp)

---

## Why AlphaPipeline

AlphaPipeline is a **machine-first data API**. Every endpoint is metered per call using x402: a request without a payment header gets back a standard HTTP 402 response describing exactly how to pay (asset, amount, network, recipient). No account creation, no dashboard, no API key issuance — sign, retry the request with the payment attached, and you get the data back in the same request/response cycle.

It is also exposed as a **remote MCP server** (`POST /mcp`) so agent frameworks (Claude Desktop, Cursor, LangChain, CrewAI, and anything else that speaks MCP) can discover and call it with zero custom integration code.

**The security cluster is the most battle-tested part of this API.** `security.token_risk`, `security.contract_health_audit`, and `security.token_diagnostic` have been run as a live pre-buy safety gate ahead of real on-chain swaps on Base — not just informational data, but an actual pass/fail input wired into a trading pipeline before it risked funds. None of the three return a qualitative verdict field (no `safe_to_execute: true/false`) — they return raw GoPlus/Honeypot.is numbers and leave the buy/no-buy decision to your own code.

- **Discovery is always free.** `initialize` and `tools/list` never require payment — browse the full tool catalog before you decide to pay.
- **Two tools are permanent free onboarding endpoints** (`unlocks.dump_risk` and `market.kimchi_alert`, see below) so an agent can verify connectivity, latency, and response schema before it starts paying for the security cluster and the rest.
- **Settlement is Coinbase CDP (Developer Platform) Facilitator** on Base mainnet (`eip155:8453`), asset USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`).

> The endpoints have not been reviewed by Coinbase — please ensure you trust them prior to sending funds.

---

## Quickstart — Remote MCP

Add this to your MCP client's config (Claude Desktop `claude_desktop_config.json`, Cursor `.cursor/mcp.json`, or equivalent):

```json
{
  "mcpServers": {
    "alphapipeline": {
      "url": "https://alphapipeline-eu.onrender.com/mcp"
    }
  }
}
```

`tools/list` works immediately with no payment. `tools/call` on a priced tool requires an x402-aware client that can sign and retry a payment (a wallet-holding bot/agent client — most stock chat UIs cannot pay automatically). See [`examples/`](./examples) for a working Python client using the official `x402` SDK.

### Quickstart — Plain REST / x402

```bash
curl -s "https://alphapipeline-eu.onrender.com/v1/security/token-risk?chain_id=8453&contract_address=0x..."
# -> HTTP 402, payment terms in the `payment-required` response header (base64 JSON)
# sign a payment, retry with the PAYMENT-SIGNATURE header attached -> 200 + data
```

> **Note for client authors:** this server speaks x402 v2 — the 402 response body is an empty `{}`; the actual payment terms (accepts array, price, asset, pay-to address) are base64-JSON in the `payment-required` response header, not the body. A client written against the older body-shaped convention will get an empty object with no error, not a loud failure. Decode the header instead.

> **Note on `unlocks.dump_risk` and `market.kimchi_alert`:** both are intentionally free (no x402 payment required) as onboarding tools — they're informational market-context signals, not part of the validated pre-trade security cluster (`security.token_risk`, `security.contract_health_audit`, `security.token_diagnostic`), which remains paid. If a third-party discovery catalog (e.g. x402 Bazaar) still shows a stale non-zero price for either from before this change, treat this server's own `/` response and MCP `tools/list` output as the source of truth — they always reflect the live price.
>
> **Note on tool names:** MCP tool names use a `domain.tool_name` dot-notation (e.g. `market.kimchi_alert`) so the tool list forms a navigable tree by domain. The REST paths under `/v1/<domain>/...` are unaffected by this and remain stable.

Full protocol reference: [`/llms.txt`](https://alphapipeline-eu.onrender.com/llms.txt) · [x402 docs](https://docs.x402.org)

### Try it now — no wallet, no signup, no payment

Before wiring up a paying client, run `check-my-slippage` to see this service return real, live data against a production endpoint:

```bash
npx check-my-slippage
```

Real output (captured 2026-09-18, straight from production — not a mocked sample):

```
Checking live Polymarket exit-liquidity via AlphaPipeline...

Market:            will-the-fed-decrease-interest-rates-by-25-bps-after-the-october-2026-meeting-... (sell)
Position size:     500 shares
Executable:        true
Best quote:        $0.0060
Avg exit price:    $0.0060
Price impact:      0%
Data source:       polymarket-clob
Latency:           88ms

Want this for any market/size, in your own bot?
-> https://github.com/parkyucheol-del/alphapipeline
```

This calls the free, rate-limited (10/min per IP) `GET /v1/prediction/preview-slippage` — no payment header, no query params, always runs against a fixed benchmark market so you can judge data quality before paying for [`prediction.exit_capacity_audit`](#tools--endpoints), which takes any market/size you supply. If `npx check-my-slippage` doesn't resolve on your machine, call the endpoint directly instead: `curl https://alphapipeline-eu.onrender.com/v1/prediction/preview-slippage`, or run it from source: `node check-my-slippage/bin/index.js` from a clone of this repo.

### Copy-paste client — pre-flight safety gate before a DEX buy

If you're building a sniping/discovery bot that buys new tokens on Base, this is the pattern our own dogfooding bot (`alphapipeline-dogfood-bot`) uses as a hard, non-bypassable gate before every on-chain purchase: pay $0.03 for `security.token-diagnostic`, then apply your own thresholds to the raw fields it returns (this server never returns a `safe_to_buy: true/false` verdict — see [Why AlphaPipeline](#why-alphapipeline)). Adapted from [`examples/client_example.py`](./examples/client_example.py):

```python
# pip install "x402[https]" eth_account httpx
import asyncio, os
from eth_account import Account
from x402 import x402Client
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

API = "https://alphapipeline-eu.onrender.com"

async def is_safe_to_buy(contract_address: str, chain_id: int = 8453) -> bool:
    account = Account.from_key(os.environ["EVM_PRIVATE_KEY"])  # dedicated small wallet only
    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))
    async with x402HttpxClient(client) as http:
        r = await http.get(f"{API}/v1/security/token-diagnostic",
                            params={"contract_address": contract_address, "chain_id": chain_id})
        await r.aread()
        diag = r.json()  # $0.03 USDC charged on Base mainnet — no free tier for this one

    return (
        diag.get("is_honeypot") is not True
        and (diag.get("buy_tax_pct") or 0) <= 10
        and (diag.get("sell_tax_pct") or 0) <= 10
        and diag.get("liquidity_health") in ("LOCKED", "PARTIALLY_LOCKED")
    )

if __name__ == "__main__":
    print(asyncio.run(is_safe_to_buy("0xYourTargetTokenContract")))
```

Tune the thresholds (buy/sell tax %, which `liquidity_health` values you accept, `risk_flags_count`, etc.) to your own risk tolerance — the full field list is in the `security.token-diagnostic` schema below. This is exactly the logic our dogfooding bot runs on every candidate before it will spend real USDC.

---

## Tools / Endpoints

| Tool (MCP) | REST endpoint | Price | What it does |
|---|---|---|---|
| `security.token_risk` | `GET /v1/security/token-risk` | $0.02 | **Pre-trade check** — call before buying or swapping: GoPlus/Honeypot.is-backed contract security audit (honeypot flag, buy/sell tax, mintability, ownership renouncement, plus individual GoPlus signals — cannot_sell_all, hidden_owner, transfer_pausable, selfdestruct, is_blacklisted, slippage_modifiable, owner concentration — folded into risk_flags). |
| `security.contract_health_audit` | `GET /v1/security/contract-health-audit` | $0.02 | **Pre-trade check** — LP (liquidity pool) lock/burn audit reusing the same GoPlus data as token-risk: flags whether liquidity is locked, burned, or freely held by a single wallet before you trust it. |
| `security.token_diagnostic` | `GET /v1/security/token-diagnostic` | $0.03 | **Pre-trade check** — bundles token_risk + contract_health_audit into one call (same GoPlus data, no new upstream calls) for a single go/no-go input before a swap. No composite score or letter grade — just both tools' fields plus a deduped risk_flags union. Cheaper than calling both separately. |
| `dex.liquidity_slippage` | `GET /v1/dex/liquidity-slippage` | $0.02 | GeckoTerminal-backed DEX pool liquidity and estimated trade slippage, plus fixed $1k/$5k/$10k `slippage_tiers`, `pool_fee_pct`, an `assumed_gas_cost_usd` estimate, and `quote_token_is_stablecoin` (flags when the picked pool isn't USD-quoted and an extra hop is needed) for at-a-glance depth checks. |
| `derivatives.whale_position_audit` | `GET /v1/derivatives/whale-position-audit` | $0.02 | Audits a Hyperliquid wallet address you supply: open positions, leverage, `max_leverage`, liquidation price, PnL, and `return_on_equity_pct` (the latter two Hyperliquid's own reported fields). Does not discover or rank wallets - Hyperliquid's public API has no leaderboard endpoint. |
| `derivatives.funding_rate` | `GET /v1/derivatives/funding-rate` | $0.01 | Bybit (primary) / Binance (fallback) perpetual futures funding rate, plus `mark_price`/`index_price` (both paths) and `open_interest_usd` (Bybit path only, null on the Binance fallback). |
| `derivatives.funding_apr_matrix` | `GET /v1/derivatives/funding-apr-matrix` | $0.01 | Annualizes the current funding rate into an APR and computes carry-trade breakeven days against an assumed round-trip trading cost. |
| `arb.spread_matrix` | `GET /v1/arb/spread-matrix` | $0.02 | CEX (Coinbase spot) vs DEX (GeckoTerminal) spread calculator with gas-adjusted profitability flag, plus `pool_fee_pct` (informational - not subtracted from the spread). |
| `prediction.neg_risk_arbitrage` | `GET /v1/prediction/neg-risk-arbitrage` | $0.03 | Detects basket arbitrage in a Polymarket neg-risk (mutually-exclusive, multi-outcome) event — a full YES basket always settles to $1, so a basket price away from $1 (after costs) is a near risk-free edge. Also returns the actual liquidity-bottleneck size executable right now (VWAP-priced, not just top-of-book), and `oldest_book_snapshot_time` (staleness bottleneck across legs). Polymarket only. |
| `prediction.exit_capacity_audit` | `GET /v1/prediction/exit-capacity-audit` | $0.02 | Walks a Polymarket outcome's live order book to check whether a given position size can actually be filled right now, at what average price and price impact, plus `book_snapshot_time`/`tick_size`/`min_order_size`. Resolves by `token_id` or an exact `market_slug`. |
| `calendar.macro_dday` | `GET /v1/calendar/macro-dday` | $0.01 | Countdown to the nearest major US macro event (FOMC, CPI, NFP) from a static, pre-loaded calendar — no external API call, never fails on an upstream outage. |
| `tools.ai_markdown` | `GET /v1/tools/ai-markdown` | $0.005 | Converts any webpage URL into clean, ad-free Markdown optimized for LLM context windows. |
| `market.kimchi_alert` | `GET /v1/market/kimchi-alert` | **Free** | Real-time Korea (Upbit) vs global reference price (Coinbase spot, CoinGecko fallback — not a live Binance orderbook) premium — the "kimchi premium" — with reverse-premium and 1h-surge alerts. Kept free by default as an onboarding tool: informational market context, not part of the paid pre-trade security cluster. |
| `unlocks.dump_risk` | `GET /v1/unlocks/dump-risk` | **Free** | On-chain (Sablier) proxy for token unlock/vesting dump risk, including `vesting_progress_pct` (withdrawn/deposit) showing how far along vesting already is. Kept free by default as an onboarding tool so agents can verify the service before paying for the rest. |

Every response is timestamped in both UTC and KST, and every priced endpoint's payment prompt reads "Paid in USDC on Base." so a human looking at the 402 screen in a browser isn't left guessing which chain's USDC to send.

### Data sources

Every number returned is either passed through unchanged from one of these upstreams, or a deterministic calculation on top of them — never a third-party estimate presented as our own:

| Domain | Upstream(s) | Notes |
|---|---|---|
| `market.kimchi_alert`, `arb.spread_matrix` (CEX leg) | Upbit (KRW) + Coinbase spot, CoinGecko fallback | Not a live Binance orderbook, despite the legacy `binance_price_usdt` field name kept for backward compatibility — see `cex_reference_price_usdt`/`cex_price_source`. |
| `security.token_risk`, `security.contract_health_audit`, `security.token_diagnostic` | GoPlus Security API, Honeypot.is fallback | Same underlying GoPlus data reused across all three; no independent second opinion. |
| `derivatives.funding_rate`, `derivatives.funding_apr_matrix` | Bybit v5 (primary), Binance premiumIndex (fallback) | `open_interest_usd` is Bybit-only — always null on the Binance fallback path. |
| `derivatives.whale_position_audit` | Hyperliquid public API (`clearinghouseState`) | No leaderboard/discovery endpoint exists upstream — you must supply the wallet address. |
| `dex.liquidity_slippage`, `arb.spread_matrix` (DEX leg) | GeckoTerminal | Liquidity depth is a constant-product (50:50) approximation, not per-token real reserves — disclosed via the response's own `notice` field. |
| `unlocks.dump_risk` | On-chain Sablier vesting streams (default, free) | VC/team classification and exact unlock timing require a paid DropsTab key (disabled by default) and are otherwise always `null` — never treat `null` as a safety signal. |
| `calendar.macro_dday` | Static, pre-loaded calendar | No external API call — never fails on an upstream outage, but needs manual updates as events roll off the calendar. |
| `prediction.neg_risk_arbitrage`, `prediction.exit_capacity_audit` | Polymarket Gamma API (event/market metadata) + Polymarket CLOB API (order book) | `book_snapshot_time`/`tick_size`/`min_order_size` are Polymarket's own reported values, passed through as-is. |
| `tools.ai_markdown` | The URL you pass in | No third-party data provider — we fetch and convert the page you give us. |

---

## Disclaimer

AlphaPipeline provides quantitative market data and analytics for informational and research purposes only. It does not execute trades, place orders, hold custody of user funds, or provide brokerage/betting/gambling services of any kind — it is a read-only data layer. Users are solely responsible for ensuring their use of this data complies with the laws and regulations applicable in their own jurisdiction.

---

## Links

- Live service: https://alphapipeline-eu.onrender.com
- Agent-readable spec: https://alphapipeline-eu.onrender.com/llms.txt
- OpenAPI docs: https://alphapipeline-eu.onrender.com/docs
- x402 Bazaar listing: https://www.x402bazaar.org/
- Glama listing (verified, TDQS A / 4.4): https://glama.ai/mcp/connectors/com.onrender.alphapipeline/alpha-pipeline-agent-mcp
- x402 protocol: https://docs.x402.org

## License

See [LICENSE](./LICENSE).
