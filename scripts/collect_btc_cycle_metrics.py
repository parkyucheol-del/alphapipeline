"""
Daily cache builder for the (scoped-down) btc-cycle-metrics endpoint idea,
2026-10-10.

SCOPE NOTE (read this first): the original pitch wanted historical BTC
DOMINANCE compared across cycles ("2020 cycle dominance was 42%, now 58%").
That needs paid data everywhere it was checked (2026-10-10, all confirmed
live): CoinGecko's historical global market-cap chart is Analyst-plan
($103/mo) and up; CoinMetrics Community 401s even on its catalog endpoint;
CoinGecko's free Demo plan (does exist, no card needed - the pricing page
just buries it below the paid plan cards) caps *all* historical data,
including /coins/{id}/market_chart, at the past 365 days - nowhere near the
2016/2020 halving dates; Kraken's free public OHLC endpoint caps at the
720 most recent candles (~2yr of daily bars) with no way to page further
back ("older data cannot be retrieved regardless of the value of since",
per Kraken's own docs) and Kraken itself says to use a third-party
provider for deeper history.

So this is scoped down to what's actually free with real multi-year depth:
  - BTC-USD / ETH-USD price via Yahoo Finance's chart endpoint with
    range=max - the exact same endpoint/UA-header trick already proven in
    scripts/collect_stock_discrepancy.py, just with crypto tickers instead
    of stock ones. Gives BTC/ETH's own price position vs the same
    day-offset in the 2016 and 2020 halving cycles.
  - TODAY's actual BTC dominance % (CoinGecko /global with a free Demo key
    - this one field isn't "historical" so the 365-day cap doesn't apply -
    but only today's value, no cycle comparison on this field).
  - DXY / US10Y (Yahoo Finance, same mechanism).

This sandbox's egress policy blocks api.coingecko.com and
query1.finance.yahoo.com directly (confirmed 2026-10-10, same block as
every other external API in this project) - so the Yahoo-crypto part has
NOT been live-tested from here (the DXY/US10Y part has, via the existing
stock-discrepancy collector). Run this by hand once on a machine with
normal internet access (this script lives in scripts/, same as
collect_stock_discrepancy.py, and writes to repo-root data-public/ the
same way):

    python3 scripts/collect_btc_cycle_metrics.py

and paste the printed output back. There's a real chance Yahoo's crypto
ticker history doesn't actually reach back to 2016 (Yahoo only started
listing major crypto pairs at some point - unconfirmed exactly when) - if
`return_since_halving_pct_2016_cycle_same_day` comes back null for BTC,
that's the likely reason; the 2020 cycle comparison should be safe either
way since every provider's listed crypto that far back.

Design: unlike scripts/collect_stock_discrepancy.py (which APPENDS one
JSONL line per hourly run - an event log), this OVERWRITES a single JSON
file each run - it's a derived snapshot/cache, not a log. The paid endpoint
(once wired in) reads this cached file instead of calling Yahoo/CoinGecko
live on every $0.02 request - keeps response time fast and avoids hitting
either provider's rate limit under load.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
# CoinGecko tightened keyless access at some point - /coins/{id}/market_chart
# now 401s with no key (confirmed live 2026-10-10, even though CoinGecko's
# own "keyless public API" docs page still listed it as keyless - don't
# trust that page over a live 401). The free "Demo" plan fixes this: sign
# up at https://www.coingecko.com/en/api/pricing ("Create Free Account",
# no card needed) -> Developer Dashboard -> API Keys -> "+ Add New Key",
# then set this as an env var (or GitHub Actions secret once this becomes
# a cron job, same pattern as the smoke-test wallet key).
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")
COINGECKO_HEADERS = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_HEADERS = {
    # Yahoo's chart endpoint 429s on httpx's default User-Agent - same fix
    # already proven in scripts/collect_stock_discrepancy.py.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# Well-established historical facts, not fetched from anywhere.
HALVING_DATES = {
    "2016_cycle": date(2016, 7, 9),
    "2020_cycle": date(2020, 5, 11),
    "current_cycle": date(2024, 4, 20),
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data-public"
CACHE_PATH = DATA_DIR / "btc_cycle_metrics_cache.json"

TIMEOUT = httpx.Timeout(30.0)


def fetch_yahoo_full_history(client: httpx.Client, symbol: str) -> list[list[float]]:
    """Returns [[unix_ms, close], ...] for a Yahoo ticker's full available daily
    history (range=max), oldest first. Same endpoint/UA trick already proven
    for stock tickers in scripts/collect_stock_discrepancy.py - crypto tickers
    (BTC-USD, ETH-USD) use the same chart API.
    """
    try:
        r = client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"interval": "1d", "range": "max"},
            headers=YAHOO_HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]  # unix seconds
        closes = result["indicators"]["quote"][0]["close"]
        out = []
        for ts, close in zip(timestamps, closes):
            if close is not None:
                out.append([ts * 1000, close])
        return out
    except Exception as exc:
        print(f"[warn] yahoo full history fetch failed for {symbol}: {exc}", file=sys.stderr)
        return []


def price_on_or_before(prices: list[list[float]], target: date) -> float | None:
    """Closest daily price at or before `target` (CoinGecko's series is daily
    for days=max, but find the nearest point <= target just in case of gaps).
    """
    target_dt = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
    target_ms = target_dt.timestamp() * 1000
    candidates = [p for p in prices if p[0] <= target_ms]
    if not candidates:
        return None
    return candidates[-1][1]


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / old * 100, 2)


def compute_coin_cycle_metrics(prices: list[list[float]], days_since_halving: int) -> dict:
    current_halving = HALVING_DATES["current_cycle"]
    today = date.today()

    price_on_current_halving = price_on_or_before(prices, current_halving)
    price_today = prices[-1][1] if prices else None
    return_current = pct_change(price_today, price_on_current_halving)

    out = {
        "price_usd_today": round(price_today, 2) if price_today is not None else None,
        "price_usd_on_current_halving": (
            round(price_on_current_halving, 2) if price_on_current_halving is not None else None
        ),
        "return_since_halving_pct": return_current,
    }

    for cycle_key in ("2016_cycle", "2020_cycle"):
        halving_date = HALVING_DATES[cycle_key]
        same_day_offset_date = halving_date + timedelta(days=days_since_halving)
        price_on_halving = price_on_or_before(prices, halving_date)
        price_on_same_day_offset = price_on_or_before(prices, same_day_offset_date)
        ret = pct_change(price_on_same_day_offset, price_on_halving)
        out[f"return_since_halving_pct_{cycle_key}_same_day"] = ret
        out[f"return_vs_{cycle_key}_pct"] = (
            round(return_current - ret, 2) if (return_current is not None and ret is not None) else None
        )

    out["data_ok"] = price_today is not None and return_current is not None
    return out


def fetch_btc_dominance_today(client: httpx.Client) -> float | None:
    try:
        r = client.get(f"{COINGECKO_BASE}/global", headers=COINGECKO_HEADERS)
        r.raise_for_status()
        return r.json()["data"]["market_cap_percentage"]["btc"]
    except Exception as exc:
        print(f"[warn] btc_dominance fetch failed: {exc}", file=sys.stderr)
        return None


def fetch_yahoo_last_close(client: httpx.Client, symbol: str) -> float | None:
    try:
        r = client.get(
            YAHOO_CHART_URL.format(symbol=symbol),
            params={"interval": "1d", "range": "5d"},
            headers=YAHOO_HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return round(closes[-1], 2) if closes else None
    except Exception as exc:
        print(f"[warn] yahoo fetch failed for {symbol}: {exc}", file=sys.stderr)
        return None


def main() -> None:
    if not COINGECKO_API_KEY:
        print(
            "[warn] COINGECKO_API_KEY not set - only affects btc_dominance_pct_today "
            "(CoinGecko /global 401s without a free Demo key). BTC/ETH price-cycle "
            "fields now come from Yahoo and don't need this key at all.",
            file=sys.stderr,
        )

    today = date.today()
    days_since_halving = (today - HALVING_DATES["current_cycle"]).days

    with httpx.Client(timeout=TIMEOUT) as client:
        btc_prices = fetch_yahoo_full_history(client, "BTC-USD")
        eth_prices = fetch_yahoo_full_history(client, "ETH-USD")
        btc_dominance = fetch_btc_dominance_today(client)
        dxy = fetch_yahoo_last_close(client, "DX-Y.NYB")
        us10y = fetch_yahoo_last_close(client, "%5ETNX")  # ^TNX url-encoded

    btc_metrics = compute_coin_cycle_metrics(btc_prices, days_since_halving)
    eth_metrics = compute_coin_cycle_metrics(eth_prices, days_since_halving)

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "current_cycle_halving_date": str(HALVING_DATES["current_cycle"]),
        "days_since_halving": days_since_halving,
        "btc": btc_metrics,
        "eth": eth_metrics,
        "btc_dominance_pct_today": btc_dominance,
        "dxy_level": dxy,
        "us10y_yield_pct": us10y,
        "data_gap": not (btc_metrics["data_ok"] and eth_metrics["data_ok"] and btc_dominance is not None),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nwrote cache to {CACHE_PATH} (data_gap={result['data_gap']})")


if __name__ == "__main__":
    main()
