"""
One-shot verification script for the btc-cycle-metrics idea (2026-10-10).

Run this BEFORE building the real backfill script / endpoint. It just checks
whether the one data source we're unsure about - CoinMetrics' free
"Community" API (no key needed, api.coinmetrics.io/v4) - actually gives us
what the whole idea depends on:
  1. CapMrktCurUSD (market cap) for BTC, reaching back to the 2016/2017 and
     2020/2021 cycles - without this there's no historical dominance to
     compare against.
  2. CapRealUSD (realized cap) for BTC over the same range - if this works
     too, it also unblocks the MVRV Z-Score field from the earlier MVRV/DXY
     round, for free, from the same source.
  3. How many assets the community tier actually returns metrics for - BTC
     dominance = BTC market cap / TOTAL market cap, and "total" needs to be
     a reasonable approximation (summing a few hundred top assets), not
     just BTC+ETH.

This sandbox's egress policy blocks api.coinmetrics.io directly (confirmed
2026-10-10, same block pattern as Hyperliquid/Kalshi/Base RPC/Basescan
earlier in this project) - that's why this hasn't been run yet. Run it by
hand once you're on a machine with normal internet access:

    python3 verify_cycle_data_sources.py

Read the printed verdict at the bottom before doing anything else with the
btc-cycle-metrics idea.
"""

import json
import sys
from datetime import date

import httpx

BASE = "https://api.coinmetrics.io/v4"
TIMEOUT = httpx.Timeout(20.0)

# Date windows from the actual cycles the idea wants to compare against.
WINDOWS = {
    "2017_cycle": ("2017-01-01", "2017-01-10"),
    "2020_cycle": ("2020-05-10", "2020-05-20"),  # around the May 2020 halving
    "current": (str(date.today().replace(day=1)), str(date.today())),
}


def check_asset_metrics(client: httpx.Client, label: str, start: str, end: str) -> dict:
    url = f"{BASE}/timeseries/asset-metrics"
    params = {
        "assets": "btc",
        "metrics": "CapMrktCurUSD,CapRealUSD,PriceUSD",
        "start_time": start,
        "end_time": end,
        "frequency": "1d",
        "page_size": 20,
    }
    try:
        r = client.get(url, params=params)
        status = r.status_code
        body = r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:500]
        return {"label": label, "window": [start, end], "status": status, "body": body}
    except Exception as exc:
        return {"label": label, "window": [start, end], "error": str(exc)}


def check_catalog_asset_count(client: httpx.Client) -> dict:
    """How many assets does community access even expose metrics for?
    If it's a short hardcoded list (BTC, ETH, a few majors), dominance is
    not computable - we'd be dividing by a fake "total".
    """
    url = f"{BASE}/catalog-v2/asset-metrics"
    params = {"metrics": "CapMrktCurUSD"}
    try:
        r = client.get(url, params=params)
        if r.status_code != 200:
            return {"status": r.status_code, "body": r.text[:500]}
        data = r.json()
        items = data.get("data", data) if isinstance(data, dict) else data
        count = len(items) if isinstance(items, list) else "unknown shape"
        return {"status": r.status_code, "asset_count_with_CapMrktCurUSD": count}
    except Exception as exc:
        return {"error": str(exc)}


def main() -> None:
    results = {}
    with httpx.Client(timeout=TIMEOUT) as client:
        for label, (start, end) in WINDOWS.items():
            results[label] = check_asset_metrics(client, label, start, end)
        results["catalog"] = check_catalog_asset_count(client)

    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))

    print("\n--- VERDICT ---")
    ok_2017 = results["2017_cycle"].get("status") == 200 and results["2017_cycle"].get("body")
    ok_2020 = results["2020_cycle"].get("status") == 200 and results["2020_cycle"].get("body")
    cat = results["catalog"]
    asset_count = cat.get("asset_count_with_CapMrktCurUSD")

    if not ok_2017 or not ok_2020:
        print("FAIL: historical CapMrktCurUSD/CapRealUSD not reachable for 2017/2020 windows.")
        print("-> btc-cycle-metrics idea needs a different data source for dominance/MVRV. Stop here.")
    elif isinstance(asset_count, int) and asset_count < 50:
        print(f"PARTIAL: historical data reaches back fine, but only {asset_count} assets have "
              "CapMrktCurUSD - not enough to approximate 'total market cap' for a real dominance "
              "figure. BTC-only metrics (price vs past cycle, CapRealUSD/MVRV) are still usable; "
              "drop the dominance field or find a second source just for total market cap.")
    else:
        print(f"OK: historical data reaches 2017/2020, and {asset_count} assets have CapMrktCurUSD "
              "- good enough to sum into a real total-market-cap denominator. Proceed with the "
              "full backfill script.")

    if sys.stdout.isatty():
        pass  # just for readability when run interactively


if __name__ == "__main__":
    main()
