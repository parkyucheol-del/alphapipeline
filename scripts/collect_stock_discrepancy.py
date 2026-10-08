"""
Hourly collector for the "Base B20 vs Hyperliquid HIP-3 stock perp" weekend
discrepancy experiment (Daily Alpha brief, 2026-10-07/08).

What this writes, once per run, one JSON line per tracked symbol, to
data-public/stock_discrepancy_log.jsonl (not data/ - that directory is
gitignored for HIP-4 webhook secrets, see app/events.py; this data has
nothing sensitive in it so it lives somewhere that actually gets committed):
    {
      "symbol": "NVDA",
      "timestamp_utc": "...", "timestamp_kst": "...",
      "last_regular_close_usd": 123.45,
      "b20_price_usd": 123.10, "b20_source": "geckoterminal", "b20_ok": true,
      "hl_mark_price_usd": 123.30, "hl_funding_rate": 0.0001, "hl_dex": "xyz", "hl_ok": true,
      "b20_vs_close_pct": -0.28, "hl_vs_close_pct": -0.12, "b20_vs_hl_pct": -0.16,
      "data_gap": false
    }

IMPORTANT — read before relying on this:
1. The Daily Alpha brief's example symbol list was NVDA/TSLA/AAPL/MSTR/COIN,
   but a web search this session (2026-10-08) only turned up confirmed
   Coinbase "B20"/cbStock tokens on Aerodrome for NVDA, AAPL, META, GOOGL
   (tickers NVDAc/AAPLc/METAc/GOOGLc) — no TSLA/MSTR/COIN B20 token was
   found. SYMBOLS below defaults to the 4 confirmed names. Check Aerodrome/
   Coinbase yourself before adding TSLA/MSTR/COIN back in — don't assume
   they exist just because the brief named them.
2. This sandbox's egress policy blocks api.hyperliquid.xyz and
   api.elections.kalshi.com directly (same block that hit Base RPC and
   Basescan earlier in this project), so NONE of the network calls below
   have been live-tested from here. Run this once by hand
   (`python collect_stock_discrepancy.py`) on your own machine or in CI
   before trusting the hourly cron, and fix anything that doesn't match
   reality (dex name, Kalshi path, GeckoTerminal response shape).
3. BASE_TOKEN_ADDRESS is left blank for every symbol on purpose — fill in
   the real Base contract address for each *c token (from the Aerodrome
   app, Coinbase's asset page, or Basescan) before running this for real.
   A blank address is skipped (data_gap=true, b20_ok=false), not guessed.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
KALSHI_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"

KST = timezone(timedelta(hours=9))

# symbol -> (Base B20 token address, Hyperliquid HIP-3 CoinRaw identifier)
# Fill in base_token_address once you've confirmed it. Leave "" to skip.
SYMBOLS = {
    "NVDA": {"base_token_address": "0xb20000000000000000000078ee7ce2fE4908108C", "hl_coin_raw": "xyz:NVDA"},
    "AAPL": {"base_token_address": "0xb200000000000000000000C2e324d24d7eEcd1fb", "hl_coin_raw": "xyz:AAPL"},
    "META": {"base_token_address": "0xb2000000000000000000008bC8786B856E61707C", "hl_coin_raw": "xyz:META"},
    "GOOGL": {"base_token_address": "0xb2000000000000000000002D0BA3164cc74f58B7", "hl_coin_raw": "xyz:GOOGL"},
    # Add TSLA / MSTR / COIN here only after confirming a B20 token exists
    # for them — see note 1 above.
}

KALSHI_SERIES_TICKER = "KXBTC15M"

DATA_DIR = Path(__file__).resolve().parent.parent / "data-public"
STOCK_LOG_PATH = DATA_DIR / "stock_discrepancy_log.jsonl"
KALSHI_LOG_PATH = DATA_DIR / "kalshi_btc15m_log.jsonl"

TIMEOUT = httpx.Timeout(20.0)


def now_stamps() -> tuple[str, str]:
    now_utc = datetime.now(timezone.utc)
    now_kst = now_utc.astimezone(KST)
    return now_utc.isoformat(), now_kst.isoformat()


async def fetch_last_regular_close(client: httpx.AsyncClient, symbol: str) -> float | None:
    """Last completed regular-session close, via Yahoo's public chart endpoint.

    No API key. range=5d/interval=1d so weekends/holidays still return the
    most recent trading day's candle.
    """
    try:
        r = await client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"interval": "1d", "range": "5d"},
            # Yahoo's chart endpoint 429s on httpx's default User-Agent
            # (no UA at all reads as a bot) - a plain desktop-browser UA is
            # enough to get through, confirmed against this exact 429 on
            # 2026-10-08.
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        r.raise_for_status()
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return closes[-1] if closes else None
    except Exception as exc:
        print(f"[warn] last_regular_close failed for {symbol}: {exc}", file=sys.stderr)
        return None


async def fetch_b20_price(client: httpx.AsyncClient, token_address: str) -> float | None:
    """B20 token USD price on Base, via GeckoTerminal (same upstream the main
    app already uses for dex.liquidity_slippage — see app/data_sources.py).
    """
    if not token_address:
        return None
    try:
        r = await client.get(
            f"{GECKOTERMINAL_BASE}/networks/base/tokens/{token_address}",
        )
        r.raise_for_status()
        data = r.json()
        price = data["data"]["attributes"]["price_usd"]
        return float(price) if price is not None else None
    except Exception as exc:
        print(f"[warn] b20_price failed for {token_address}: {exc}", file=sys.stderr)
        return None


_PERP_DEX_CACHE: dict[str, dict] = {}


async def fetch_hl_perp_dex_universe(client: httpx.AsyncClient, dex_name: str) -> dict | None:
    """metaAndAssetCtxs scoped to one HIP-3 perp dex. Cached per run so N
    symbols on the same dex only cost one HTTP call.
    """
    if dex_name in _PERP_DEX_CACHE:
        return _PERP_DEX_CACHE[dex_name]
    try:
        r = await client.post(
            HYPERLIQUID_INFO_URL,
            json={"type": "metaAndAssetCtxs", "dex": dex_name},
        )
        r.raise_for_status()
        data = r.json()
        _PERP_DEX_CACHE[dex_name] = data
        return data
    except Exception as exc:
        print(f"[warn] metaAndAssetCtxs failed for dex={dex_name}: {exc}", file=sys.stderr)
        return None


async def fetch_hl_mark_and_funding(
    client: httpx.AsyncClient, coin_raw: str
) -> tuple[float | None, float | None]:
    """mark price + funding rate for one HIP-3 stock perp, e.g. coin_raw="xyz:NVDA".

    metaAndAssetCtxs returns [{"universe": [...]}, [asset_ctx, ...]] where
    asset_ctx[i] lines up positionally with universe[i] - this mirrors the
    allMids/outcomeMeta pairing pattern already used for prediction.hip4_snapshot
    in app/data_sources.py, just for a perp dex instead of the default one.
    """
    dex_name, _, coin = coin_raw.partition(":")
    data = await fetch_hl_perp_dex_universe(client, dex_name)
    if not data:
        return None, None
    try:
        universe = data[0]["universe"]
        asset_ctxs = data[1]
        for i, asset in enumerate(universe):
            # Confirmed live 2026-10-08: on a non-default perp dex, the
            # asset's "name" field is the full dex-prefixed CoinRaw
            # ("xyz:NVDA"), not the bare coin ("NVDA") - matching against
            # the bare coin silently matched nothing and looked like the
            # coin didn't exist, when it actually did.
            if asset.get("name") == coin_raw:
                ctx = asset_ctxs[i]
                mark = ctx.get("markPx")
                funding = ctx.get("funding")
                return (
                    float(mark) if mark is not None else None,
                    float(funding) if funding is not None else None,
                )
        print(f"[warn] {coin_raw} not found in dex {dex_name} universe", file=sys.stderr)
        return None, None
    except Exception as exc:
        print(f"[warn] parsing metaAndAssetCtxs failed for {coin_raw}: {exc}", file=sys.stderr)
        return None, None


def pct_diff(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100, 4)


async def collect_stock_rows(client: httpx.AsyncClient) -> list[dict]:
    ts_utc, ts_kst = now_stamps()
    rows = []
    for symbol, cfg in SYMBOLS.items():
        close = await fetch_last_regular_close(client, symbol)
        b20_price = await fetch_b20_price(client, cfg["base_token_address"])
        hl_mark, hl_funding = await fetch_hl_mark_and_funding(client, cfg["hl_coin_raw"])

        b20_ok = b20_price is not None
        hl_ok = hl_mark is not None
        rows.append(
            {
                "symbol": symbol,
                "timestamp_utc": ts_utc,
                "timestamp_kst": ts_kst,
                "last_regular_close_usd": close,
                "b20_price_usd": b20_price,
                "b20_source": "geckoterminal",
                "b20_ok": b20_ok,
                "hl_mark_price_usd": hl_mark,
                "hl_funding_rate": hl_funding,
                "hl_dex": cfg["hl_coin_raw"].split(":")[0],
                "hl_ok": hl_ok,
                "b20_vs_close_pct": pct_diff(b20_price, close),
                "hl_vs_close_pct": pct_diff(hl_mark, close),
                "b20_vs_hl_pct": pct_diff(b20_price, hl_mark),
                # data_gap feeds the launch-criteria "<=10% missing data" check directly.
                "data_gap": not (b20_ok and hl_ok and close is not None),
            }
        )
    return rows


async def collect_kalshi_snapshot(client: httpx.AsyncClient) -> dict:
    ts_utc, ts_kst = now_stamps()
    try:
        r = await client.get(
            KALSHI_MARKETS_URL,
            params={"series_ticker": KALSHI_SERIES_TICKER, "status": "open"},
        )
        r.raise_for_status()
        markets = r.json().get("markets", [])
        return {
            "timestamp_utc": ts_utc,
            "timestamp_kst": ts_kst,
            "series_ticker": KALSHI_SERIES_TICKER,
            "open_market_count": len(markets),
            "markets": [
                {
                    "ticker": m.get("ticker"),
                    "yes_bid": m.get("yes_bid"),
                    "yes_ask": m.get("yes_ask"),
                    "close_time": m.get("close_time"),
                }
                for m in markets
            ],
            "ok": True,
        }
    except Exception as exc:
        print(f"[warn] kalshi snapshot failed: {exc}", file=sys.stderr)
        return {
            "timestamp_utc": ts_utc,
            "timestamp_kst": ts_kst,
            "series_ticker": KALSHI_SERIES_TICKER,
            "ok": False,
            "error": str(exc),
        }


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def main() -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        stock_rows = await collect_stock_rows(client)
        kalshi_row = await collect_kalshi_snapshot(client)

    append_jsonl(STOCK_LOG_PATH, stock_rows)
    append_jsonl(KALSHI_LOG_PATH, [kalshi_row])

    gaps = sum(1 for r in stock_rows if r["data_gap"])
    print(f"wrote {len(stock_rows)} stock rows ({gaps} with data_gap=true) to {STOCK_LOG_PATH}")
    print(f"wrote 1 kalshi row (ok={kalshi_row['ok']}) to {KALSHI_LOG_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
