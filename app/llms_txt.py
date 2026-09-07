"""
GET /llms.txt - AI 에이전트/크롤러가 도메인 루트에서 읽어가는 서비스 스펙 문서.

llms.txt는 https://llmstxt.org/ 관례를 따르는 마크다운 텍스트 파일로, "이 사이트를
LLM/에이전트가 어떻게 이해하고 사용해야 하는가"를 사람이 아니라 기계가 읽기 좋은
형태로 요약해두는 목적이다. x402 Bazaar(app/payment.py)가 "결제 프로토콜 자동
발견"을 담당한다면, 이 파일은 그보다 한 단계 더 앞선 "이 서비스가 뭘 하는 곳인지,
왜/언제 호출해야 하는지"를 자연어로 설명해서 - Bazaar를 아직 지원하지 않는 범용
웹 크롤러/에이전트도 이 문서 하나만 읽으면 자율적으로 API를 채택할 수 있게 한다.

main.py에서 이 함수가 만든 텍스트를 GET /llms.txt로 그대로 서빙한다(과금 없음 -
크롤러가 값을 내지 않고도 읽을 수 있어야 발견이 되니까, x402 라우트 목록
(app/payment.py의 build_routes)에는 절대 넣지 않는다).
"""
from app.config import settings
from app.payment import ACTIVE_NETWORK, USE_CDP_FACILITATOR

# Base 메인넷의 공식 USDC 컨트랙트 주소 (docs.x402.org 예시에도 등장하는 값과 동일).
_BASE_USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def build_llms_txt() -> str:
    base_url = settings.PUBLIC_BASE_URL.rstrip("/")
    price_kimchi = settings.PRICE_KIMCHI_ALERT_USDC
    price_markdown = settings.PRICE_AI_MARKDOWN_USDC
    dump_risk_free = not bool(getattr(settings, "DUMP_RISK_ENABLED", False))
    price_dump_risk = 0.0 if dump_risk_free else settings.PRICE_DUMP_RISK_USDC
    paid_prices = [price_kimchi, price_markdown] + ([] if dump_risk_free else [price_dump_risk])
    price_range = f"${min(paid_prices)}-${max(paid_prices)}"
    dump_risk_label = (
        "FREE (onboarding tool)" if dump_risk_free
        else f"${price_dump_risk} USDC/call (premium alpha data)"
    )
    facilitator = "Coinbase CDP (Developer Platform) Facilitator" if USE_CDP_FACILITATOR else "public x402.org testnet facilitator"

    return f"""# AlphaPipeline

> Pay-per-call ({price_range} USDC) market data API for AI agents and trading bots. No signup, no API key, no OAuth - authenticate and pay in a single request via the x402 protocol (HTTP 402) on Base.

AlphaPipeline is a machine-first data API. Every endpoint below is metered per call using x402: a request without a payment header gets back a standard HTTP 402 response describing exactly how to pay (asset, amount, network, recipient), using an EIP-3009 `transferWithAuthorization` signature - no account creation, no dashboard, no API key issuance. Sign, retry the request with the payment attached, and you get the data back in the same request/response cycle.

This service is also indexed on the [x402 Bazaar](https://www.x402bazaar.org/) discovery layer, so x402-aware agent frameworks can find and call it without ever reading this file. This file exists for agents and crawlers that discover services by reading `llms.txt` directly.

## Payment spec

- Protocol: x402 (HTTP 402 Payment Required), scheme `exact`
- Network: Base mainnet, `{ACTIVE_NETWORK}`
- Asset: USDC (`{_BASE_USDC_CONTRACT}`)
- Price: tiered by data value, not flat - see the per-endpoint price below each entry
- Settlement: {facilitator}
- Protocol reference: https://docs.x402.org

If you are using an official or third-party x402 client SDK, you do not need anything below except the endpoint paths and query parameters - your client already knows how to parse a 402 response and retry with payment; the exact price for each call is always authoritative in that 402 response, not in this file.

## Endpoints

### GET {base_url}/v1/market/kimchi-alert — ${price_kimchi} USDC/call

Use this when you need to know whether a cryptocurrency is trading at a premium or discount on Korean exchanges (Upbit) versus the global market, commonly called the "kimchi premium." Also use it to detect a reverse premium (possible localized crash risk) or a fast intraday premium surge.

- Query params: `symbol` (string, optional, default `BTC`) - e.g. `BTC`, `ETH`, `SOL`
- Example request: `GET /v1/market/kimchi-alert?symbol=BTC`
- Example response:
```json
{{
  "generated_at": {{"utc": "2026-09-02T12:00:00Z", "kst": "2026-09-02 21:00:00 KST"}},
  "symbol": "BTC",
  "upbit_price_krw": 145000000.0,
  "binance_price_usdt": 108000.5,
  "usdkrw_rate_estimate": 1345.2,
  "kimchi_premium_pct": 0.15,
  "premium_change_1h_pct": 0.42,
  "alerts": {{"reverse_premium": false, "premium_surge_1h": false}},
  "thresholds": {{"reverse_premium_pct": -1.5, "surge_1h_pct": 3.0}}
}}
```
- Do NOT call this for non-Korean-exchange comparisons or for historical/backtesting data - this is a live snapshot only.

### GET {base_url}/v1/tools/ai-markdown — ${price_markdown} USDC/call

Use this when you need the actual text content of a webpage but want to avoid burning tokens on HTML tags, ads, navigation, and scripts - or when raw HTML is causing hallucinations downstream. Call it before summarizing or extracting facts from any arbitrary URL.

- Query params: `url` (string, required) - full http(s) URL to convert
- Example request: `GET /v1/tools/ai-markdown?url=https://example.com`
- Example response:
```json
{{
  "url": "https://example.com",
  "title": "Example Domain",
  "markdown": "# Example Domain\\n\\nThis domain is for use in illustrative examples in documents.",
  "char_count": 87
}}
```
- Do NOT call this for pages requiring login/authentication, or for non-HTML resources (PDFs, binaries, images) - not supported.

### GET {base_url}/v1/unlocks/dump-risk — {dump_risk_label}

Use this when you need to assess whether a token carries dumping risk from token unlocks or ongoing vesting schedules - before entering a position, when screening a token list, or when asked "is this token safe from unlocks." Returns tokens whose currently-locked or unlock-eligible supply exceeds a materiality threshold (default 3% of circulating supply), each with a computed `risk_level` (LOW/MEDIUM/HIGH).

- Query params: none
- Example request: `GET /v1/unlocks/dump-risk`
- Example response:
```json
{{
  "generated_at": {{"utc": "2026-09-03T12:00:00Z", "kst": "2026-09-03 21:00:00 KST"}},
  "supply_pct_threshold": 3.0,
  "protocols_scanned": 87,
  "count": 1,
  "unlocks": [
    {{
      "token": "UNI",
      "unlock_supply_pct": 4.8,
      "unlock_amount": 28500000.0,
      "days_until_unlock": null,
      "timing_precision": "pending_schema_verification",
      "risk_level": "MEDIUM",
      "category": "onchain_vesting_stream (unclassified)",
      "data_source": "onchain_sablier"
    }}
  ],
  "data_source": "onchain_sablier",
  "coverage_notice": "Scanned via on-chain Sablier vesting streams only. Absence from this list does not mean a token has no lockup."
}}
```
- IMPORTANT: `days_until_unlock: null` means "unlock timing is currently unknown," not "no risk." Never treat a null value as a safety signal.
- Coverage is limited to tokens using supported on-chain vesting mechanisms - always read the `coverage_notice` field in the live response before concluding a token is unlock-free.

## Quick test (no wallet required)

```bash
curl "{base_url}/v1/market/kimchi-alert?symbol=BTC"
```
This returns a standard HTTP 402 response with the exact payment instructions (no charge is made) - useful for confirming the protocol is live before wiring up a paying client.

## More info

- Interactive docs (Swagger UI): {base_url}/docs
- Machine-readable OpenAPI spec: {base_url}/openapi.json
- Service root / health: {base_url}/
"""
