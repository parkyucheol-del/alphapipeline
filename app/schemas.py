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
    token: str
    unlock_date_utc: str
    days_until_unlock: float
    unlock_supply_pct: float
    unlock_amount: Optional[float] = None
    is_insider_vc_team: bool
    category: str
    risk_level: str
    volume_impact_pct: Optional[float] = None


class DumpRiskResponse(BaseModel):
    generated_at: TimestampPair
    window_days: int
    supply_pct_threshold: float
    protocols_scanned: int
    count: int
    unlocks: list[DumpRiskUnlockItem] = Field(default_factory=list)
    notice: Optional[str] = None


DUMP_RISK_EXAMPLE = {
    "generated_at": {"utc": "2026-09-02T12:00:00Z", "kst": "2026-09-02 21:00:00 KST"},
    "window_days": 7,
    "supply_pct_threshold": 3.0,
    "protocols_scanned": 120,
    "count": 1,
    "unlocks": [
        {
            "token": "ATH",
            "unlock_date_utc": "2026-09-06T00:00:00Z",
            "days_until_unlock": 4.0,
            "unlock_supply_pct": 5.2,
            "unlock_amount": 12000000,
            "is_insider_vc_team": True,
            "category": "team",
            "risk_level": "HIGH",
            "volume_impact_pct": 62.3,
        }
    ],
}


class ComingSoonResponse(BaseModel):
    status: str
    message: str


class ErrorResponse(BaseModel):
    error: str
    message: str
