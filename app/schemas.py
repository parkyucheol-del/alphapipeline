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
    "data_source": "geckoterminal",
    "notice": (
        "슬리피지는 GeckoTerminal이 제공하는 풀의 합산 USD 유동성만으로 계산한 근사치입니다 - "
        "이 풀이 표준 constant-product(x*y=k) AMM이고 두 토큰이 50:50 비율로 구성되어 있다고 "
        "가정합니다. Uniswap v3류 집중 유동성 풀이나 스테이블스왑 풀에서는 실제 슬리피지와 "
        "차이가 클 수 있습니다 - 실제 매매 전 온체인 견적(quote)으로 반드시 재확인하세요."
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
