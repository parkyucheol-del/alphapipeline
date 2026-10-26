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
      "hl_mark_price_usd": 123.30, "hl_oracle_price_usd": 123.28, "hl_mid_price_usd": 123.31,
      "hl_open_interest": 4821.5, "hl_funding_rate": 0.0001, "hl_dex": "xyz", "hl_ok": true,
      "b20_vs_close_pct": -0.28, "hl_vs_close_pct": -0.12, "b20_vs_hl_pct": -0.16,
      "hl_mark_vs_oracle_bp": 1.6, "hl_mid_vs_oracle_bp": 2.4,
      "data_gap": false
    }

2026-10-18 update (Daily Alpha action card, dex=xyz mark/oracle/mid +
open interest): fetch_hl_mark_and_funding() was extended into
fetch_hl_stock_perp_ctx(), pulling oraclePx/midPx/openInterest off the
same per-dex metaAndAssetCtxs call alongside markPx/funding - same
asset_ctx object Hyperliquid already returns, no extra request. Gap vs.
oracle is reported in basis points (hl_mark_vs_oracle_bp,
hl_mid_vs_oracle_bp), matching the bp convention measure_hl_gap.py
already uses for the BTC/ETH default-dex mid-oracle alert. This collector
was already wired into the hourly
.github/workflows/stock-discrepancy-collect.yml cron (dex="xyz" was
already in SYMBOLS' hl_coin_raw) - this change only adds fields to the
row it was already writing, it does not touch the cron itself.

2026-10-26 update (Daily Alpha build/action 카드, EQX 현물 상장): EQX Labs의
EQ-프리픽스 토큰(EQNVDA 등)이 Hyperliquid HIP-1 현물로 상장하면서, 기존
B20-vs-HL 비교(체인이 다른 두 자산)보다 더 깨끗한 "같은 체인 안 현물 vs 퍼프"
비교가 가능해졌다. collect_eqx_gap_rows()가 이 비교를 담당하고, 결과는 별도
파일 data-public/eqx_gap_log.jsonl에 쌓인다(기존 symbol 스키마와 안 섞음 -
현물/퍼프 페어링 방식이 달라서 한 파일에 넣으면 혼란스러움):
    {
      "eqx_token_name": "EQNVDA", "underlying_symbol": "NVDA",
      "timestamp_utc": "...", "timestamp_kst": "...",
      "eqx_spot_price_usd": 123.40, "eqx_spot_day_volume_usd": 98210.5, "eqx_spot_ok": true,
      "hl_perp_coin_raw": "xyz:NVDA", "hl_perp_mid_price_usd": 123.31,
      "hl_perp_mark_price_usd": 123.30, "hl_perp_oracle_price_usd": 123.28, "hl_perp_ok": true,
      "spot_vs_perp_mid_bp": 7.3,
      "data_gap": false
    }
런칭은 2026-10-26 22:30 KST, 미국 정규장 마감은 그다음 날 05:00 KST라
이미 매시간(:13) 도는 이 크론이 두 시점을 자연히 커버한다 - 다만 1시간 간격이라
브리핑이 제안한 "5분마다"보다 거칠다. 괴리 가설("휴장 중 괴리가 장중보다 큰가")
판정은 최소 1~2주 데이터가 쌓인 뒤 확인할 것 - 자세한 배경은 project 문서
claude/session-log-2026-10-26-eqx-gap-collector-added.md 참고.

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

# --- EQX 현물(Hyperliquid HIP-1) vs xyz 퍼프 괴리 (2026-10-26 데일리 알파
# build/action 카드 대응) ---
#
# 위의 B20-vs-HL 비교는 체인이 다른 두 자산(Base 토큰 vs Hyperliquid 퍼프)을
# 비교해서 브리지/정산 잡음이 섞이는데, EQX의 EQ-프리픽스 토큰은 Hyperliquid
# HIP-1 현물이라 xyz 퍼프와 같은 HyperCore 안에서 바로 비교할 수 있다 - 이게
# 오늘 브리핑이 "더 깨끗한 비교"라고 부른 이유.
#
# EQX 공식 사이트(eqxlabs.io)·문서(docs.eqxlabs.io) 확인 결과(2026-10-26) 11개
# 티커는 EQSPY, EQQQQ, EQAAPL, EQAMZN, EQGOOG, EQMETA, EQMSFT, EQNVDA, EQTSLA,
# EQMU, EQSPCX다. EQGOOG는 기초자산이 GOOGL이지만 토큰 심볼 자체는 GOOG라 xyz
# 퍼프 쪽 코인명(GOOGL)과 다를 수 있어 아래 매칭에서 둘 다 시도한다.
#
# 주의 - 아직 미확인: EQX는 이 글 작성 시점(런칭 당일)에 토큰 ID/주소를
# 공개 문서에 올리지 않았다("공식 토큰 ID를 공개할 계획"이라고만 되어 있음).
# 그래서 지금은 spotMetaAndAssetCtxs에서 받은 토큰 이름이 "EQ" + (아래 11개
# 티커 중 하나)와 정확히 일치하는지로만 걸러낸다 - 이름이 같은 모방 토큰까지는
# 못 거른다. EQX가 토큰 ID를 공개하면(docs.eqxlabs.io 쪽 "markets and token
# IDs" 페이지) 여기 주석에 적어두고 ID 비교로 바꿀 것.
EQX_TICKERS = {
    "EQSPY": "SPY",
    "EQQQQ": "QQQ",
    "EQAAPL": "AAPL",
    "EQAMZN": "AMZN",
    "EQGOOG": "GOOGL",  # xyz 퍼프 코인명은 GOOGL 쪽일 가능성이 높아 기본값으로 둠 - 매칭 시 GOOG도 같이 시도
    "EQMETA": "META",
    "EQMSFT": "MSFT",
    "EQNVDA": "NVDA",
    "EQTSLA": "TSLA",
    "EQMU": "MU",
    "EQSPCX": "SPCX",
}
EQX_PERP_DEX = "xyz"

DATA_DIR = Path(__file__).resolve().parent.parent / "data-public"
STOCK_LOG_PATH = DATA_DIR / "stock_discrepancy_log.jsonl"
KALSHI_LOG_PATH = DATA_DIR / "kalshi_btc15m_log.jsonl"
EQX_GAP_LOG_PATH = DATA_DIR / "eqx_gap_log.jsonl"

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


async def fetch_hl_stock_perp_ctx(
    client: httpx.AsyncClient, coin_raw: str
) -> dict[str, float | None]:
    """mark/oracle/mid price + funding + open interest for one HIP-3 stock
    perp, e.g. coin_raw="xyz:NVDA".

    metaAndAssetCtxs returns [{"universe": [...]}, [asset_ctx, ...]] where
    asset_ctx[i] lines up positionally with universe[i] - this mirrors the
    allMids/outcomeMeta pairing pattern already used for prediction.hip4_snapshot
    in app/data_sources.py, just for a perp dex instead of the default one.
    oraclePx/midPx/openInterest are already present on the same asset_ctx
    object as markPx/funding (same fields measure_hl_gap.py reads off the
    default dex for ETH/BTC) - no extra HTTP call needed to get them.
    """
    empty = {"mark": None, "oracle": None, "mid": None, "funding": None, "open_interest": None}
    dex_name, _, coin = coin_raw.partition(":")
    data = await fetch_hl_perp_dex_universe(client, dex_name)
    if not data:
        return empty
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

                def f(key: str) -> float | None:
                    val = ctx.get(key)
                    return float(val) if val is not None else None

                return {
                    "mark": f("markPx"),
                    "oracle": f("oraclePx"),
                    "mid": f("midPx"),
                    "funding": f("funding"),
                    "open_interest": f("openInterest"),
                }
        print(f"[warn] {coin_raw} not found in dex {dex_name} universe", file=sys.stderr)
        return empty
    except Exception as exc:
        print(f"[warn] parsing metaAndAssetCtxs failed for {coin_raw}: {exc}", file=sys.stderr)
        return empty


def pct_diff(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100, 4)


def bp_diff(a: float | None, b: float | None) -> float | None:
    """Same as pct_diff but in basis points, matching measure_hl_gap.py's
    mid-oracle alert convention (gap = (mid - oracle) / oracle * 1e4)."""
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 1e4, 2)


async def collect_stock_rows(client: httpx.AsyncClient) -> list[dict]:
    ts_utc, ts_kst = now_stamps()
    rows = []
    for symbol, cfg in SYMBOLS.items():
        close = await fetch_last_regular_close(client, symbol)
        b20_price = await fetch_b20_price(client, cfg["base_token_address"])
        hl_ctx = await fetch_hl_stock_perp_ctx(client, cfg["hl_coin_raw"])
        hl_mark = hl_ctx["mark"]
        hl_oracle = hl_ctx["oracle"]
        hl_mid = hl_ctx["mid"]
        hl_funding = hl_ctx["funding"]
        hl_open_interest = hl_ctx["open_interest"]

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
                "hl_oracle_price_usd": hl_oracle,
                "hl_mid_price_usd": hl_mid,
                "hl_open_interest": hl_open_interest,
                "hl_funding_rate": hl_funding,
                "hl_dex": cfg["hl_coin_raw"].split(":")[0],
                "hl_ok": hl_ok,
                "b20_vs_close_pct": pct_diff(b20_price, close),
                "hl_vs_close_pct": pct_diff(hl_mark, close),
                "b20_vs_hl_pct": pct_diff(b20_price, hl_mark),
                # mark/mid vs oracle, in bp - same convention as
                # measure_hl_gap.py's BTC/ETH default-dex alert, now applied
                # to the xyz stock perps this collector already tracks.
                "hl_mark_vs_oracle_bp": bp_diff(hl_mark, hl_oracle),
                "hl_mid_vs_oracle_bp": bp_diff(hl_mid, hl_oracle),
                # data_gap feeds the launch-criteria "<=10% missing data" check directly.
                "data_gap": not (b20_ok and hl_ok and close is not None),
            }
        )
    return rows


async def fetch_eqx_spot_universe(client: httpx.AsyncClient) -> dict[str, dict] | None:
    """spotMetaAndAssetCtxs 전체를 한 번 호출해서, 이름이 EQX_TICKERS에 있는
    토큰만 뽑아 {token_name: {"price": ..., "day_volume_usd": ...}}로 돌려준다.

    Hyperliquid 응답 구조는 [meta, asset_ctxs] 튜플이고, meta["tokens"]는
    {"name", "index", ...} 토큰 정의 목록, meta["universe"]는 거래쌍(마켓)
    목록으로 각 항목의 "tokens"가 [baseTokenIndex, quoteTokenIndex]다.
    asset_ctxs[i]가 universe[i]와 위치로 대응된다 - fetch_hl_stock_perp_ctx()가
    쓰는 metaAndAssetCtxs와 같은 페어링 패턴.
    """
    try:
        r = await client.post(
            HYPERLIQUID_INFO_URL,
            json={"type": "spotMetaAndAssetCtxs"},
        )
        r.raise_for_status()
        meta, asset_ctxs = r.json()
        tokens_by_index = {t["index"]: t for t in meta["tokens"]}
        result: dict[str, dict] = {}
        for market, ctx in zip(meta["universe"], asset_ctxs):
            base_token = tokens_by_index.get(market["tokens"][0])
            if not base_token:
                continue
            name = base_token.get("name", "")
            if name not in EQX_TICKERS:
                continue
            price = ctx.get("midPx") or ctx.get("markPx")
            result[name] = {
                "price": float(price) if price is not None else None,
                "day_volume_usd": (
                    float(ctx["dayNtlVlm"]) if ctx.get("dayNtlVlm") is not None else None
                ),
            }
        return result
    except Exception as exc:
        print(f"[warn] spotMetaAndAssetCtxs failed: {exc}", file=sys.stderr)
        return None


async def collect_eqx_gap_rows(client: httpx.AsyncClient) -> list[dict]:
    ts_utc, ts_kst = now_stamps()
    spot = await fetch_eqx_spot_universe(client)
    rows = []
    for eqx_name, underlying in EQX_TICKERS.items():
        spot_entry = (spot or {}).get(eqx_name)
        spot_price = spot_entry["price"] if spot_entry else None
        spot_vol = spot_entry["day_volume_usd"] if spot_entry else None

        # GOOG/GOOGL처럼 현물 토큰 심볼과 퍼프 코인명이 다를 수 있어 둘 다 시도.
        perp_candidates = [underlying]
        if underlying == "GOOGL":
            perp_candidates.append("GOOG")
        elif underlying == "GOOG":
            perp_candidates.append("GOOGL")

        hl_mid = hl_mark = hl_oracle = None
        matched_coin_raw = None
        for candidate in perp_candidates:
            ctx = await fetch_hl_stock_perp_ctx(client, f"{EQX_PERP_DEX}:{candidate}")
            if ctx["mid"] is not None or ctx["mark"] is not None:
                hl_mid, hl_mark, hl_oracle = ctx["mid"], ctx["mark"], ctx["oracle"]
                matched_coin_raw = f"{EQX_PERP_DEX}:{candidate}"
                break

        perp_ref = hl_mid or hl_mark
        spot_ok = spot_price is not None
        perp_ok = perp_ref is not None
        rows.append(
            {
                "eqx_token_name": eqx_name,
                "underlying_symbol": underlying,
                "timestamp_utc": ts_utc,
                "timestamp_kst": ts_kst,
                "eqx_spot_price_usd": spot_price,
                "eqx_spot_day_volume_usd": spot_vol,
                "eqx_spot_ok": spot_ok,
                "hl_perp_coin_raw": matched_coin_raw,
                "hl_perp_mid_price_usd": hl_mid,
                "hl_perp_mark_price_usd": hl_mark,
                "hl_perp_oracle_price_usd": hl_oracle,
                "hl_perp_ok": perp_ok,
                # 같은 HyperCore 안의 현물 vs 퍼프라 체인/브리지 잡음 없이 순수한
                # 괴리만 남는다 - bp_diff() 재사용(측정 단위만 bp로 통일).
                "spot_vs_perp_mid_bp": bp_diff(spot_price, perp_ref),
                "data_gap": not (spot_ok and perp_ok),
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
        eqx_rows = await collect_eqx_gap_rows(client)

    append_jsonl(STOCK_LOG_PATH, stock_rows)
    append_jsonl(KALSHI_LOG_PATH, [kalshi_row])
    append_jsonl(EQX_GAP_LOG_PATH, eqx_rows)

    gaps = sum(1 for r in stock_rows if r["data_gap"])
    print(f"wrote {len(stock_rows)} stock rows ({gaps} with data_gap=true) to {STOCK_LOG_PATH}")
    print(f"wrote 1 kalshi row (ok={kalshi_row['ok']}) to {KALSHI_LOG_PATH}")
    eqx_gaps = sum(1 for r in eqx_rows if r["data_gap"])
    print(
        f"wrote {len(eqx_rows)} EQX gap rows ({eqx_gaps} with data_gap=true) to {EQX_GAP_LOG_PATH}"
    )


if __name__ == "__main__":
    asyncio.run(main())
