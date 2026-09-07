"""
FastAPI 응답 스키마 (Pydantic).

이 모델들은 두 가지 역할을 한다.
1. /docs, /openapi.json에 정확한 응답 구조를 노출해서 사람이 보기에도, OpenAPI 스펙을
   그대로 읽는 AI 에이전트 디렉토리(Agentic.Market 등 - 등록 방식이 확정되지 않아
   범용적으로 대비해두는 차원)에도 도움이 되게 한다.
2. x402 Bazaar용 `extensions.bazaar.info.output.example`에 넣는 예시 값과 반드시
   같은 모양을 유지하기 위한 "단일 진실 공급원" 역할 (app/payment.py가 이 예시들을
   그대로 가져다 씀 - BAZAAR_EXAMPLES 참고).

실제 값(get_kimchi_alert, url_to_markdown, get_dump_risk의 반환값)과 필드명이
어긋나지 않도록, 새 필드를 추가/변경하면 여기도 같이 고쳐야 한다.
"""
from typing import Optional
from pydantic import BaseModel, Field


class TimestampPair(BaseModel):
    utc: str
    kst: str


class KimchiAlerts(BaseModel):
    reverse_premium: bool
    premium_surge_1h: bool


class KimchiThresholds(BaseModel):
    reverse_premium_pct: float
    surge_1h_pct: float


class KimchiAlertResponse(BaseModel):
    generated_at: TimestampPair
    symbol: str
    upbit_price_krw: float
    binance_price_usdt: float
    usdkrw_rate_estimate: float
    kimchi_premium_pct: float
    premium_change_1h_pct: float
    alerts: KimchiAlerts
    thresholds: KimchiThresholds


KIMCHI_ALERT_EXAMPLE = {
    "generated_at": {"utc": "2026-09-02T12:00:00Z", "kst": "2026-09-02 21:00:00 KST"},
    "symbol": "BTC",
    "upbit_price_krw": 145000000.0,
    "binance_price_usdt": 108000.5,
    "usdkrw_rate_estimate": 1345.2,
    "kimchi_premium_pct": 0.15,
    "premium_change_1h_pct": 0.42,
    "alerts": {"reverse_premium": False, "premium_surge_1h": False},
    "thresholds": {"reverse_premium_pct": -1.5, "surge_1h_pct": 3.0},
}


class MarkdownResponse(BaseModel):
    url: str
    title: str
    markdown: str
    char_count: int


MARKDOWN_EXAMPLE = {
    "url": "https://example.com",
    "title": "Example Domain",
    "markdown": "# Example Domain\n\nThis domain is for use in illustrative examples in documents.",
    "char_count": 87,
}


class DumpRiskUnlockItem(BaseModel):
    """
    두 데이터 경로(DropsTab / 온체인-Sablier)를 하나의 모델로 표현한다 - 그래서
    DropsTab 전용 필드(unlock_date_utc, is_insider_vc_team 등)와 온체인 전용
    필드(onchain_contract, timing_precision, data_source)가 전부 Optional이다.
    어느 경로든 항상 채워지는 필드는 token/unlock_supply_pct/category/risk_level뿐.
    (app/logic.py의 _process_event / _refresh_unlock_cache_onchain 참고)
    """
    token: str
    onchain_contract: Optional[str] = None
    unlock_date_utc: Optional[str] = None
    days_until_unlock: Optional[float] = None
    timing_precision: Optional[str] = None
    unlock_supply_pct: float
    unlock_amount: Optional[float] = None
    is_insider_vc_team: Optional[bool] = None
    category: str
    risk_level: str
    volume_impact_pct: Optional[float] = None
    data_source: Optional[str] = None


class DumpRiskResponse(BaseModel):
    generated_at: TimestampPair
    window_days: Optional[int] = None
    supply_pct_threshold: float
    protocols_scanned: int
    count: int
    unlocks: list[DumpRiskUnlockItem] = Field(default_factory=list)
    notice: Optional[str] = None
    data_source: Optional[str] = None
    coverage_notice: Optional[str] = None


# DROPSTAB_API_KEY가 없는 게 기본 배포 상태라, 실제로 서빙될 가능성이 더 높은
# 온체인(Sablier) 경로의 모양을 예시로 쓴다 - Bazaar/OpenAPI에 노출되는 예시가
# 실제 응답과 어긋나지 않도록(app/payment.py가 이 값을 그대로 가져다 씀).
DUMP_RISK_EXAMPLE = {
    "generated_at": {"utc": "2026-09-03T12:00:00Z", "kst": "2026-09-03 21:00:00 KST"},
    "window_days": None,
    "supply_pct_threshold": 3.0,
    "protocols_scanned": 87,
    "count": 1,
    "unlocks": [
        {
            "token": "UNI",
            "onchain_contract": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
            "unlock_date_utc": None,
            "days_until_unlock": None,
            "timing_precision": "pending_schema_verification",
            "unlock_supply_pct": 4.8,
            "unlock_amount": 28500000.0,
            "is_insider_vc_team": None,
            "category": "onchain_vesting_stream (unclassified)",
            "risk_level": "MEDIUM",
            "volume_impact_pct": None,
            "data_source": "onchain_sablier",
        }
    ],
    "data_source": "onchain_sablier",
    "coverage_notice": (
        "Scanned via on-chain Sablier vesting streams only. Absence from this list "
        "does not mean a token has no lockup - other vesting mechanisms are not covered."
    ),
}


class ComingSoonResponse(BaseModel):
    status: str
    message: str


class ErrorResponse(BaseModel):
    error: str
    message: str



class TokenRiskResponse(BaseModel):
    generated_at: TimestampPair
    chain_id: int
    contract_address: str
    token_name: str | None = None
    token_symbol: str | None = None
    is_honeypot: bool | None = None
    buy_tax_pct: float | None = None
    sell_tax_pct: float | None = None
    is_mintable: bool | None = None
    is_open_source: bool | None = None
    owner_renounced: bool | None = None
    owner_address: str | None = None
    holder_count: int | None = None
    is_in_dex: bool | None = None
    risk_level: str
    risk_flags: list[str] = []
    data_source: str
    notice: str | None = None


TOKEN_RISK_EXAMPLE = {
    "generated_at": {"utc": "2026-09-04T12:00:00Z", "kst": "2026-09-04 21:00:00 KST"},
    "chain_id": 8453,
    "contract_address": "0x4200000000000000000000000000000000000006",
    "token_name": "Wrapped Ether",
    "token_symbol": "WETH",
    "is_honeypot": False,
    "buy_tax_pct": 0.0,
    "sell_tax_pct": 0.0,
    "is_mintable": False,
    "is_open_source": True,
    "owner_renounced": True,
    "owner_address": None,
    "holder_count": 125000,
    "is_in_dex": True,
    "risk_level": "LOW",
    "risk_flags": [],
    "data_source": "goplus",
    "notice": None,
}


class ContractHealthAuditResponse(BaseModel):
    generated_at: TimestampPair
    chain_id: int
    contract_address: str
    token_name: str | None = None
    token_symbol: str | None = None
    lp_total_supply: float | None = None
    lp_holder_count: int | None = None
    lp_locked_pct: float | None = None
    lp_burned_pct: float | None = None
    top_unlocked_holder_pct: float | None = None
    liquidity_health: str
    risk_flags: list[str] = []
    data_source: str
    notice: str | None = None


CONTRACT_HEALTH_EXAMPLE = {
    "generated_at": {"utc": "2026-09-07T12:00:00Z", "kst": "2026-09-07 21:00:00 KST"},
    "chain_id": 8453,
    "contract_address": "0x532f27101965dd16442e59d40670faf5ebb142e",
    "token_name": "Example Token",
    "token_symbol": "EXTKN",
    "lp_total_supply": 128500.42,
    "lp_holder_count": 3,
    "lp_locked_pct": 0.0,
    "lp_burned_pct": 98.7,
    "top_unlocked_holder_pct": 1.1,
    "liquidity_health": "LOCKED",
    "risk_flags": [],
    "data_source": "goplus",
    "notice": (
        "LP lock/burn detection comes from GoPlus Security's lp_holders field, which "
        "flags an address as is_locked only when GoPlus recognizes it as a known "
        "third-party locker contract (e.g. Unicrypt, Team.Finance) - coverage varies by "
        "chain and is generally weaker outside Ethereum/BSC, so a low lp_locked_pct can "
        "mean 'actually unlocked' or just 'GoPlus doesn't recognize this locker'. Burn "
        "addresses are matched against a small known list and are always counted as "
        "permanently secured. This tool checks LP lock/burn status only - it does not "
        "re-run the honeypot/tax checks from security.token_risk, and it does not "
        "evaluate transaction history for suspicious activity. Always cross-verify on a "
        "block explorer before trusting liquidity as safe."
    ),
}


class WhalePosition(BaseModel):
    coin: str
    side: str = Field(description="LONG or SHORT, derived from the sign of Hyperliquid's szi (signed size).")
    size: float
    entry_price: float
    mark_price: float | None = Field(
        default=None,
        description=(
            "Derived as position_value_usd / size, since clearinghouseState does not "
            "return a live mark price directly. Not a separately-fetched live quote."
        ),
    )
    position_value_usd: float
    leverage: float
    leverage_type: str = Field(description="cross or isolated, as reported by Hyperliquid.")
    unrealized_pnl_usd: float
    liquidation_price: float | None = None
    distance_to_liquidation_pct: float | None = Field(
        default=None,
        description="abs(mark_price - liquidation_price) / mark_price * 100. Null when Hyperliquid reports no liquidation price for this position.",
    )


class WhalePositionAuditResponse(BaseModel):
    generated_at: TimestampPair
    wallet_address: str
    account_value_usd: float | None = None
    total_margin_used_usd: float | None = None
    total_notional_position_usd: float | None = None
    withdrawable_usd: float | None = None
    margin_usage_pct: float | None = None
    open_position_count: int
    positions: list[WhalePosition] = []
    risk_flags: list[str] = []
    data_source: str
    notice: str | None = None


WHALE_AUDIT_EXAMPLE = {
    "generated_at": {"utc": "2026-09-07T12:00:00Z", "kst": "2026-09-07 21:00:00 KST"},
    "wallet_address": "0x31ca8395cf837de08b24da3f660e77761dfb974",
    "account_value_usd": 13104.51,
    "total_margin_used_usd": 4.97,
    "total_notional_position_usd": 100.03,
    "withdrawable_usd": 13099.55,
    "margin_usage_pct": 0.04,
    "open_position_count": 1,
    "positions": [
        {
            "coin": "ETH",
            "side": "LONG",
            "size": 0.0335,
            "entry_price": 2986.3,
            "mark_price": 2986.5,
            "position_value_usd": 100.03,
            "leverage": 20.0,
            "leverage_type": "isolated",
            "unrealized_pnl_usd": -0.0134,
            "liquidation_price": 2866.27,
            "distance_to_liquidation_pct": 4.02,
        }
    ],
    "risk_flags": [],
    "data_source": "Hyperliquid clearinghouseState (official public API)",
    "notice": (
        "This tool audits a wallet address you already know - it does not discover or "
        "rank 'smart money' wallets, because Hyperliquid's public API has no leaderboard "
        "or large-trader disclosure endpoint. mark_price is derived from "
        "position_value_usd / size (Hyperliquid does not return a separate live quote in "
        "this response), so it can lag the true mark price briefly during fast moves. "
        "risk_flags are computed from fixed numeric thresholds only (leverage >= 20x -> "
        "HIGH_LEVERAGE, distance_to_liquidation_pct < 15 -> NEAR_LIQUIDATION), not a "
        "judgment call about the trader."
    ),
}


class FundingRateResponse(BaseModel):
    generated_at: TimestampPair
    symbol: str
    funding_rate: float | None = None
    funding_rate_percentage: float | None = None
    predicted_rate: float | None = None
    next_funding_time: TimestampPair | None = None
    funding_interval_hours: int | None = None
    data_source: str
    notice: str | None = None


FUNDING_RATE_EXAMPLE = {
    "generated_at": {"utc": "2026-09-04T12:00:00Z", "kst": "2026-09-04 21:00:00 KST"},
    "symbol": "BTCUSDT",
    "funding_rate": 0.0001,
    "funding_rate_percentage": 0.01,
    "predicted_rate": 0.0001,
    "next_funding_time": {"utc": "2026-09-04T16:00:00Z", "kst": "2026-09-05 01:00:00 KST"},
    "funding_interval_hours": 8,
    "data_source": "bybit",
    "notice": (
        "펀딩비는 다음 정산 시점(next_funding_time)에 적용될 예정 요율입니다. "
        "Bybit/바이낸스 둘 다 이와 별개의 '예측' 필드를 제공하지 않으므로 "
        "predicted_rate는 funding_rate와 동일한 값입니다."
    ),
}


class FundingAprMatrixResponse(BaseModel):
    generated_at: TimestampPair
    symbol: str
    funding_rate_percentage: float | None = None
    funding_interval_hours: int | None = None
    periods_per_year: int | None = None
    annualized_rate_pct: float | None = None
    funding_collector_side: str | None = None
    daily_funding_income_pct: float | None = None
    assumed_round_trip_cost_pct: float
    breakeven_days: float | None = None
    data_source: str
    notice: str | None = None


FUNDING_APR_EXAMPLE = {
    "generated_at": {"utc": "2026-09-07T12:00:00Z", "kst": "2026-09-07 21:00:00 KST"},
    "symbol": "BTCUSDT",
    "funding_rate_percentage": 0.01,
    "funding_interval_hours": 8,
    "periods_per_year": 1095,
    "annualized_rate_pct": 10.95,
    "funding_collector_side": "SHORT",
    "daily_funding_income_pct": 0.03,
    "assumed_round_trip_cost_pct": 0.2,
    "breakeven_days": 6.67,
    "data_source": "bybit",
    "notice": (
        "annualized_rate_pct is a flat extrapolation of the CURRENT funding rate "
        "(periods_per_year x current rate) - it assumes the rate stays constant, which "
        "funding rates rarely do over a full year. breakeven_days assumes a constant "
        "funding_collector_side position and a flat assumed_round_trip_cost_pct covering "
        "entry+exit trading fees on both the spot and perpetual legs - it excludes margin "
        "borrow cost, spot-perp basis risk, and perp liquidation risk. Re-verify with your "
        "actual exchange fee tier before sizing a real carry trade."
    ),
}



class SlippageTier(BaseModel):
    trade_size_usd: float = Field(
        description="Fixed hypothetical trade size in USD for this tier ($1,000 / $5,000 / $10,000)."
    )
    estimated_price_impact_pct: float | None = Field(
        default=None,
        description=(
            "Estimated price impact percentage at this trade size, using the same "
            "constant-product (x*y=k) 50:50 approximation as estimated_slippage_pct."
        ),
    )
    warning_level: str | None = Field(
        default=None,
        description=(
            "Heuristic risk label for this tier: LOW (<1% impact), MEDIUM (1-3%), or "
            "HIGH (>3%). This is AlphaPipeline's own threshold, not an industry standard."
        ),
    )


class DexSlippageResponse(BaseModel):
    generated_at: TimestampPair
    network: str
    pool_address: str | None = None
    token_address: str | None = None
    pool_name: str | None = None
    liquidity_usd: float | None = None
    volume_24h_usd: float | None = None
    trade_size_usd: float
    estimated_slippage_pct: float | None = None
    price_impact_model: str
    slippage_tiers: list[SlippageTier] | None = Field(
        default=None,
        description=(
            "Fixed $1,000/$5,000/$10,000 price-impact tiers computed from the same pool "
            "liquidity data, independent of the trade_size_usd query parameter - lets an "
            "agent gauge depth at a glance without multiple calls."
        ),
    )
    data_source: str
    notice: str | None = None


DEX_SLIPPAGE_EXAMPLE = {
    "generated_at": {"utc": "2026-09-04T12:00:00Z", "kst": "2026-09-04 21:00:00 KST"},
    "network": "base",
    "pool_address": "0xd0b53d9277642d899df5c87a3966a349a798f224",
    "token_address": None,
    "pool_name": "WETH / USDC 0.05%",
    "liquidity_usd": 25000000.0,
    "volume_24h_usd": 8500000.0,
    "trade_size_usd": 10000.0,
    "estimated_slippage_pct": 0.08,
    "price_impact_model": "constant_product_50_50_approximation",
    "slippage_tiers": [
        {"trade_size_usd": 1000.0, "estimated_price_impact_pct": 0.008, "warning_level": "LOW"},
        {"trade_size_usd": 5000.0, "estimated_price_impact_pct": 0.04, "warning_level": "LOW"},
        {"trade_size_usd": 10000.0, "estimated_price_impact_pct": 0.08, "warning_level": "LOW"},
    ],
    "data_source": "geckoterminal",
    "notice": (
        "Slippage is an approximation computed only from the pool's aggregate USD "
        "liquidity as reported by GeckoTerminal - it assumes the pool is a standard "
        "constant-product (x*y=k) AMM with the two tokens in a 50:50 ratio. Actual "
        "slippage can differ significantly for Uniswap v3-style concentrated-liquidity "
        "pools or stableswap pools - always re-verify with an on-chain quote before "
        "trading. slippage_tiers uses the same approximation at three fixed sizes "
        "regardless of the trade_size_usd you passed in; warning_level thresholds (LOW "
        "<1%, MEDIUM 1-3%, HIGH >3%) are AlphaPipeline's own heuristic, not an industry "
        "standard."
    ),
}


class ArbSpreadResponse(BaseModel):
    generated_at: TimestampPair
    symbol: str
    network: str
    pool_address: str | None = None
    status_message: str
    is_profitable: bool
    gross_spread_pct: float | None = None
    net_spread_pct: float | None = None
    direction: str | None = None
    cex_price_usd: float | None = None
    dex_price_usd: float | None = None
    trade_size_usd: float
    assumed_gas_cost_usd: float
    min_spread_threshold_pct: float
    data_source: str
    notice: str | None = None


ARB_SPREAD_EXAMPLE = {
    "generated_at": {"utc": "2026-09-07T12:00:00Z", "kst": "2026-09-07 21:00:00 KST"},
    "symbol": "SUI",
    "network": "base",
    "pool_address": "0x4a3636608d7bc5776cb19eb72caa36ebb9bd9e5b",
    "status_message": "Spread is 0.45%, below 0.8% threshold (Unprofitable).",
    "is_profitable": False,
    "gross_spread_pct": 0.52,
    "net_spread_pct": 0.45,
    "direction": "DEX_TO_CEX",
    "cex_price_usd": 3.521,
    "dex_price_usd": 3.503,
    "trade_size_usd": 1000.0,
    "assumed_gas_cost_usd": 0.05,
    "min_spread_threshold_pct": 0.8,
    "data_source": "coinbase+geckoterminal",
    "notice": (
        "CEX-side price is Coinbase spot (CoinGecko fallback), not a specific exchange "
        "orderbook - it does not reflect actual tradable depth on any single exchange. "
        "DEX-side price is read from GeckoTerminal's pool price fields. net_spread_pct "
        "only subtracts an assumed flat gas cost - it excludes CEX deposit/withdrawal "
        "availability, trading fees, and slippage beyond trade_size_usd. Re-verify with "
        "live quotes before executing a real trade."
    ),
}


class TimeRemaining(BaseModel):
    days: int
    hours: int
    minutes: int


class MacroCalendarEventBrief(BaseModel):
    event_name: str
    event_type: str
    event_datetime: TimestampPair
    impact_level: str
    tags: list[str] = []


class MacroDdayResponse(BaseModel):
    generated_at: TimestampPair
    event_name: str | None = None
    event_type: str | None = None
    event_datetime: TimestampPair | None = None
    d_day: int | None = None
    time_remaining: TimeRemaining | None = None
    impact_level: str | None = None
    tags: list[str] = []
    description: str | None = None
    upcoming_events: list[MacroCalendarEventBrief] = []
    data_source: str
    notice: str | None = None


MACRO_DDAY_EXAMPLE = {
    "generated_at": {"utc": "2026-09-03T12:00:00Z", "kst": "2026-09-03 21:00:00 KST"},
    "event_name": "FOMC 금리 결정 (9월, SEP 포함)",
    "event_type": "FOMC",
    "event_datetime": {"utc": "2026-09-16T18:00:00Z", "kst": "2026-09-17 03:00:00 KST"},
    "d_day": 13,
    "time_remaining": {"days": 13, "hours": 6, "minutes": 0},
    "impact_level": "HIGH",
    "tags": ["rate-decision", "fomc", "interest-rates"],
    "description": "Federal Reserve interest rate decision and policy statement.",
    "upcoming_events": [
        {
            "event_name": "CPI (2026년 9월 기준)",
            "event_type": "CPI",
            "event_datetime": {"utc": "2026-10-14T12:30:00Z", "kst": "2026-10-14 21:30:00 KST"},
            "impact_level": "HIGH",
            "tags": ["inflation", "cpi", "cpi-report"],
        },
        {
            "event_name": "NFP (2026년 9월 기준)",
            "event_type": "NFP",
            "event_datetime": {"utc": "2026-10-02T12:30:00Z", "kst": "2026-10-02 21:30:00 KST"},
            "impact_level": "HIGH",
            "tags": ["employment", "nfp", "jobs-report"],
        },
    ],
    "data_source": "static_2026_macro_calendar",
    "notice": (
        "이 캘린더는 2026년 FOMC 금리 결정, 미국 CPI, 미국 고용지표(NFP) 일정을 공식 "
        "연준(Fed)/BLS 발표 기준으로 정적으로 내장한 것입니다 - 실시간 외부 API를 호출하지 "
        "않습니다. 일정은 연준/BLS가 추후 변경할 수 있고 2027년 일정은 아직 포함되어 있지 "
        "않으니, 중요한 의사결정 전에는 공식 소스(federalreserve.gov, bls.gov)로 재확인하세요."
    ),
}
