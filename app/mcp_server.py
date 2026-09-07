"""
AlphaPipeline 원격 MCP 서버 (Streamable HTTP, stateless) - 기존 7개 x402 유료
REST 엔드포인트를 Cursor/Claude Desktop/커스텀 에이전트가 MCP tools/call로 바로
호출할 수 있게 감싸는 얇은 어댑터.

## 설계 요약 (왜 이렇게 만들었나)
- MCP 표준 자체에는 402에 대응하는 개념이 없다 - 결제는 HTTP 트랜스포트
  레벨에서 처리된다(공식 x402 MCP 통합 가이드들이 공통으로 문서화한 관례:
  docs.x402.org/guides/mcp-server-with-x402, systemprompt.io, eco.com 2026
  가이드 참고). 그래서 `tools/call`이 유료 도구를 가리키면, 이 어댑터는
  **이미 검증된 기존 PaymentMiddlewareASGI 결제 게이트를 그대로 재사용**하기
  위해 같은 프로세스 안에서 해당 REST GET 엔드포인트로 셀프 ASGI 호출을 한다
  (httpx.ASGITransport) - payment.py는 전혀 건드리지 않는다.
- `PaymentMiddlewareASGI`는 URL 경로(`"METHOD /path"`)로만 가격을 구분하므로,
  도구마다 다른 단가를 유지하려면 `/mcp` 한 경로에 전부 때려박을 수 없다.
  대신 이미 가격이 매겨져 있는 기존 GET REST 경로를 그대로 재호출해서, 단가
  기준이 REST/MCP 두 표면 사이에서 절대 어긋나지 않게(single source of
  truth) 만들었다.
- `/mcp` 자체는 결제 라우트 dict(app/payment.py의 build_routes())에 등록하지
  않는다 - `initialize`/`tools/list`는 항상 무료로 열려 있어야 에이전트가
  "뭘 살 수 있는지" 먼저 확인할 수 있다("free discovery, paid execution"
  원칙, 위 가이드들이 공통으로 강조).
- 업스트림(내부 self-call) 응답이 402면 그 상태코드/본문을 그대로 이 요청의
  HTTP 응답으로 릴레이한다 - JSON-RPC 에러 객체로 감싸지 않는다. 이게
  x402-aware 클라이언트(x402-httpx/x402-fetch류 페이먼트 인터셉터를 쓰는
  봇 개발자 코드)가 기대하는 정확한 신호다. 참고: 순정 Claude Desktop/Cursor
  같은 사람 대상 앱은 자체 지갑이 없어서 이 402를 자동으로 결제하지
  못한다 - 이 MCP 서버는 "자체 지갑을 가진 에이전트/봇 프레임워크"를 위한
  것이지, 사람이 쓰는 채팅 UI에서 자동 결제가 되는 게 아니다(2026-09
  리서치로 확인 - Coinbase AgentKit MCP 확장 문서에도 x402 자동결제 언급
  없음, 여러 서드파티 "에이전트 지갑" MCP 서버가 별도로 존재하는 이유).
- MCP 스펙은 2026-07-28 리비전에서 프로토콜 코어 자체가 완전히 stateless로
  바뀌었다(Mcp-Session-Id 제거) - 우리 도구는 원래 세션 상태가 필요 없는
  단순 읽기 전용 데이터 조회라서, 처음부터 세션을 아예 추적하지 않는
  방식으로 만들었다 (모든 버전의 클라이언트와 호환).

## 알아두어야 할 점 (정직하게 밝혀둠)
- `tools/list`에 넣은 `_meta.x402` 필드(가격/네트워크/수신주소)는 MCP
  코어 스펙의 공식 필드가 아니라, `_meta`(스펙이 명시적으로 허용하는 확장
  지점)를 이용한 우리 자체 표기다 - 아직 업계 공통 표준(`x-x402` 등)이
  굳어지지 않아서, 이해 못 하는 클라이언트는 그냥 무시하는 안전한 방식을
  택했다.
- GET(SSE 스트리밍) 요청은 지원하지 않는다 - 도구들이 전부 짧은 동기 조회라
  서버가 먼저 보낼 알림이 없다. 스펙이 명시적으로 허용하는 대로 405를
  반환한다.
- JSON-RPC 배치(여러 요청을 배열로 한 번에)는 지원하지 않는다 - 2025-06-18
  스펙에서 이미 제거되었다.
"""
import json
import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.payment import ACTIVE_NETWORK

logger = logging.getLogger("alphapipeline")

_PROTOCOL_VERSION_FALLBACK = "2025-06-18"

_SERVER_INSTRUCTIONS = (
    "AlphaPipeline은 각 도구를 Base 메인넷 USDC(x402 'exact' 스킴)로 건당 결제해야 "
    "쓸 수 있습니다. tools/list의 각 도구 _meta.x402 필드에 가격/네트워크/수신주소가 "
    "있습니다. 결제 없이 tools/call을 호출하면 이 요청에 대한 HTTP 응답 자체가 402가 "
    "되고, 본문에 x402 accepts 배열(가격/자산/수신주소/논스 등 결제에 필요한 정보)이 "
    "담겨 옵니다 - EIP-3009 transferWithAuthorization 서명을 만들어 X-PAYMENT 헤더에 "
    "실어 같은 요청을 재시도하세요. 사람이 쓰는 순정 Claude Desktop/Cursor 채팅 UI는 "
    "자체 지갑이 없어 이 결제를 자동으로 처리하지 못합니다 - 이 서버는 x402 결제 "
    "인터셉터(x402-httpx, x402-fetch 등)를 갖춘 에이전트/봇 코드를 위한 것입니다."
)

# 각 도구 -> 기존에 이미 결제가 걸려 있는 REST GET 엔드포인트로 매핑한다.
# input_schema는 app/payment.py의 build_routes()에 등록된 Bazaar discovery
# extension의 input_schema와 동일한 내용으로 맞췄다(단가/스펙이 REST와 항상
# 일치하도록 - 이 파일이 별도로 값을 정의하지 않고 그대로 베낀 이유).
_TIMESTAMP_PAIR_SCHEMA = {
    "type": "object",
    "properties": {"utc": {"type": "string"}, "kst": {"type": "string"}},
    "required": ["utc", "kst"],
}

_READ_ONLY_ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False}

KIMCHI_ALERT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated_at": _TIMESTAMP_PAIR_SCHEMA,
        "symbol": {"type": "string"},
        "upbit_price_krw": {"type": "number"},
        "binance_price_usdt": {"type": "number"},
        "usdkrw_rate_estimate": {"type": "number"},
        "kimchi_premium_pct": {"type": "number"},
        "premium_change_1h_pct": {"type": "number"},
        "alerts": {
            "type": "object",
            "properties": {
                "reverse_premium": {"type": "boolean"},
                "premium_surge_1h": {"type": "boolean"},
            },
            "required": ["reverse_premium", "premium_surge_1h"],
        },
        "thresholds": {
            "type": "object",
            "properties": {
                "reverse_premium_pct": {"type": "number"},
                "surge_1h_pct": {"type": "number"},
            },
            "required": ["reverse_premium_pct", "surge_1h_pct"],
        },
    },
    "required": [
        "generated_at", "symbol", "upbit_price_krw", "binance_price_usdt",
        "usdkrw_rate_estimate", "kimchi_premium_pct", "premium_change_1h_pct",
        "alerts", "thresholds",
    ],
}

AI_MARKDOWN_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "title": {"type": "string"},
        "markdown": {"type": "string"},
        "char_count": {"type": "integer"},
    },
    "required": ["url", "title", "markdown", "char_count"],
}

TOKEN_RISK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated_at": _TIMESTAMP_PAIR_SCHEMA,
        "chain_id": {"type": "integer"},
        "contract_address": {"type": "string"},
        "token_name": {"type": ["string", "null"]},
        "token_symbol": {"type": ["string", "null"]},
        "is_honeypot": {"type": ["boolean", "null"]},
        "buy_tax_pct": {"type": ["number", "null"]},
        "sell_tax_pct": {"type": ["number", "null"]},
        "is_mintable": {"type": ["boolean", "null"]},
        "is_open_source": {"type": ["boolean", "null"]},
        "owner_renounced": {"type": ["boolean", "null"]},
        "owner_address": {"type": ["string", "null"]},
        "holder_count": {"type": ["integer", "null"]},
        "is_in_dex": {"type": ["boolean", "null"]},
        "risk_level": {"type": "string"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "data_source": {"type": "string"},
        "notice": {"type": ["string", "null"]},
    },
    "required": [
        "generated_at", "chain_id", "contract_address", "risk_level",
        "risk_flags", "data_source",
    ],
}

FUNDING_RATE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated_at": _TIMESTAMP_PAIR_SCHEMA,
        "symbol": {"type": "string"},
        "funding_rate": {"type": ["number", "null"]},
        "funding_rate_percentage": {"type": ["number", "null"]},
        "predicted_rate": {"type": ["number", "null"]},
        "next_funding_time": {"anyOf": [_TIMESTAMP_PAIR_SCHEMA, {"type": "null"}]},
        "funding_interval_hours": {"type": ["integer", "null"]},
        "data_source": {"type": "string"},
        "notice": {"type": ["string", "null"]},
    },
    "required": ["generated_at", "symbol", "data_source"],
}

FUNDING_APR_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated_at": _TIMESTAMP_PAIR_SCHEMA,
        "symbol": {"type": "string"},
        "funding_rate_percentage": {"type": ["number", "null"]},
        "funding_interval_hours": {"type": ["integer", "null"]},
        "periods_per_year": {"type": ["integer", "null"]},
        "annualized_rate_pct": {"type": ["number", "null"]},
        "funding_collector_side": {"type": ["string", "null"]},
        "daily_funding_income_pct": {"type": ["number", "null"]},
        "assumed_round_trip_cost_pct": {"type": "number"},
        "breakeven_days": {"type": ["number", "null"]},
        "data_source": {"type": "string"},
        "notice": {"type": ["string", "null"]},
    },
    "required": ["generated_at", "symbol", "assumed_round_trip_cost_pct", "data_source"],
}

DEX_SLIPPAGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated_at": _TIMESTAMP_PAIR_SCHEMA,
        "network": {"type": "string"},
        "pool_address": {"type": ["string", "null"]},
        "token_address": {"type": ["string", "null"]},
        "pool_name": {"type": ["string", "null"]},
        "liquidity_usd": {"type": ["number", "null"]},
        "volume_24h_usd": {"type": ["number", "null"]},
        "trade_size_usd": {"type": "number"},
        "estimated_slippage_pct": {"type": ["number", "null"]},
        "price_impact_model": {"type": "string"},
        "data_source": {"type": "string"},
        "notice": {"type": ["string", "null"]},
    },
    "required": [
        "generated_at", "network", "trade_size_usd", "price_impact_model",
        "data_source",
    ],
}

ARB_SPREAD_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated_at": _TIMESTAMP_PAIR_SCHEMA,
        "symbol": {"type": "string"},
        "network": {"type": "string"},
        "pool_address": {"type": ["string", "null"]},
        "status_message": {"type": "string"},
        "is_profitable": {"type": "boolean"},
        "gross_spread_pct": {"type": ["number", "null"]},
        "net_spread_pct": {"type": ["number", "null"]},
        "direction": {"type": ["string", "null"]},
        "cex_price_usd": {"type": ["number", "null"]},
        "dex_price_usd": {"type": ["number", "null"]},
        "trade_size_usd": {"type": "number"},
        "assumed_gas_cost_usd": {"type": "number"},
        "min_spread_threshold_pct": {"type": "number"},
        "data_source": {"type": "string"},
        "notice": {"type": ["string", "null"]},
    },
    "required": [
        "generated_at", "symbol", "network", "status_message", "is_profitable",
        "trade_size_usd", "assumed_gas_cost_usd", "min_spread_threshold_pct",
        "data_source",
    ],
}

MACRO_DDAY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated_at": _TIMESTAMP_PAIR_SCHEMA,
        "event_name": {"type": ["string", "null"]},
        "event_type": {"type": ["string", "null"]},
        "event_datetime": {"anyOf": [_TIMESTAMP_PAIR_SCHEMA, {"type": "null"}]},
        "d_day": {"type": ["integer", "null"]},
        "time_remaining": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "days": {"type": "integer"},
                        "hours": {"type": "integer"},
                        "minutes": {"type": "integer"},
                    },
                    "required": ["days", "hours", "minutes"],
                },
                {"type": "null"},
            ],
        },
        "impact_level": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "description": {"type": ["string", "null"]},
        "upcoming_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_name": {"type": "string"},
                    "event_type": {"type": "string"},
                    "event_datetime": _TIMESTAMP_PAIR_SCHEMA,
                    "impact_level": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["event_name", "event_type", "event_datetime", "impact_level"],
            },
        },
        "data_source": {"type": "string"},
        "notice": {"type": ["string", "null"]},
    },
    "required": ["generated_at", "tags", "upcoming_events", "data_source"],
}

DUMP_RISK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "generated_at": _TIMESTAMP_PAIR_SCHEMA,
        "window_days": {"type": ["integer", "null"]},
        "supply_pct_threshold": {"type": "number"},
        "protocols_scanned": {"type": "integer"},
        "count": {"type": "integer"},
        "unlocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "token": {"type": "string"},
                    "onchain_contract": {"type": ["string", "null"]},
                    "unlock_date_utc": {"type": ["string", "null"]},
                    "days_until_unlock": {"type": ["number", "null"]},
                    "timing_precision": {"type": ["string", "null"]},
                    "unlock_supply_pct": {"type": "number"},
                    "unlock_amount": {"type": ["number", "null"]},
                    "is_insider_vc_team": {"type": ["boolean", "null"]},
                    "category": {"type": "string"},
                    "risk_level": {"type": "string"},
                    "volume_impact_pct": {"type": ["number", "null"]},
                    "data_source": {"type": ["string", "null"]},
                },
                "required": ["token", "unlock_supply_pct", "category", "risk_level"],
            },
        },
        "notice": {"type": ["string", "null"]},
        "data_source": {"type": ["string", "null"]},
        "coverage_notice": {"type": ["string", "null"]},
    },
    "required": [
        "generated_at", "supply_pct_threshold", "protocols_scanned", "count",
        "unlocks",
    ],
}

_TOOLS: list[dict] = [
    {
        "name": "market.kimchi_alert",
        "path": "/v1/market/kimchi-alert",
        "price_attr": "PRICE_KIMCHI_ALERT_USDC",
        "description": (
            "Use this tool when evaluating Korean exchange price premiums, the 'kimchi "
            "premium', Upbit price gaps vs Binance/OKX, cross-border crypto arbitrage, or "
            "sudden Korea-specific price anomalies. Real-time Upbit vs global price "
            "spread with reverse-premium and surge alerts. Do not use for general USD "
            "spot prices or on-chain DEX swaps. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Crypto ticker symbol to check, e.g. BTC, ETH, SOL. Defaults to BTC.",
                }
            },
            "required": [],
        },
        "output_schema": KIMCHI_ALERT_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "tools.ai_markdown",
        "path": "/v1/tools/ai-markdown",
        "price_attr": "PRICE_AI_MARKDOWN_USDC",
        "description": (
            "Use this tool when an agent needs to parse clean webpage article content "
            "without wasting context tokens on ads, scripts, navigation, and HTML "
            "boilerplate, or when summarizing a specific URL. Converts any URL into "
            "clean Markdown optimized for LLM context windows. Do not use for raw API "
            "endpoints or binary files (PDF/images). Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Full http(s) URL of the webpage to convert to Markdown.",
                }
            },
            "required": ["url"],
        },
        "output_schema": AI_MARKDOWN_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "security.token_risk",
        "path": "/v1/security/token-risk",
        "price_attr": "PRICE_TOKEN_RISK_USDC",
        "description": (
            "Use this tool before executing any on-chain swap to verify if an ERC-20 "
            "contract is a honeypot, rug-pull risk, or has malicious buy/sell taxes and "
            "mintability backdoors. GoPlus/Honeypot.is-backed security audit for a given "
            "contract address. Do not use for market price discovery or liquidity "
            "depth. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chain_id": {
                    "type": "integer",
                    "description": "EVM chain id, e.g. 8453 for Base.",
                },
                "contract_address": {
                    "type": "string",
                    "description": "Token contract address (0x...).",
                },
            },
            "required": ["chain_id", "contract_address"],
        },
        "output_schema": TOKEN_RISK_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "derivatives.funding_rate",
        "path": "/v1/derivatives/funding-rate",
        "price_attr": "PRICE_FUNDING_RATE_USDC",
        "description": (
            "Use this tool when analyzing perpetual futures funding rates, long/short "
            "market sentiment crowding, or timing hedging strategies before settlement "
            "periods. Aggregates Bybit (primary) and Binance (fallback) perpetual "
            "funding rates. Do not use for spot market volume or token security "
            "checks. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. BTC, ETH, or BTCUSDT.",
                }
            },
            "required": ["symbol"],
        },
        "output_schema": FUNDING_RATE_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "derivatives.funding_apr_matrix",
        "path": "/v1/derivatives/funding-apr-matrix",
        "price_attr": "PRICE_FUNDING_APR_USDC",
        "description": (
            "Use this tool to evaluate a spot+perpetual carry trade: annualizes the "
            "current perpetual funding rate into an APR, flags which side (SHORT or "
            "LONG perp) currently collects funding, and computes how many days of that "
            "funding income it takes to recoup an assumed round-trip trading cost. "
            "Pure calculation on top of funding_rate data - no extra upstream call. "
            "Do not use for the raw current funding rate alone (use "
            "derivatives.funding_rate) or for spot price data. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. BTC, ETH, or BTCUSDT.",
                },
                "assumed_round_trip_cost_pct": {
                    "type": "number",
                    "description": (
                        "Combined entry+exit trading fee percentage across both the "
                        "spot and perpetual legs. Defaults to 0.2."
                    ),
                },
            },
            "required": ["symbol"],
        },
        "output_schema": FUNDING_APR_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "dex.liquidity_slippage",
        "path": "/v1/dex/liquidity-slippage",
        "price_attr": "PRICE_DEX_SLIPPAGE_USDC",
        "description": (
            "Use this tool to calculate expected DEX price slippage, pool liquidity "
            "depth, and optimal routing before executing an on-chain token swap. "
            "GeckoTerminal-backed pool analytics with constant-product slippage "
            "estimation. Do not use for centralized exchange (CEX) orderbooks or "
            "contract risk analysis. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "network": {
                    "type": "string",
                    "description": "GeckoTerminal network id, e.g. base, eth. Defaults to base.",
                },
                "pool_address": {
                    "type": "string",
                    "description": "Specific DEX pool contract address (optional if token_address is given).",
                },
                "token_address": {
                    "type": "string",
                    "description": "Token contract address - the most liquid pool is auto-selected (optional if pool_address is given).",
                },
                "trade_size_usd": {
                    "type": "number",
                    "description": "Hypothetical trade size in USD to estimate slippage for.",
                },
            },
            "required": ["trade_size_usd"],
        },
        "output_schema": DEX_SLIPPAGE_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "arb.spread_matrix",
        "path": "/v1/arb/spread-matrix",
        "price_attr": "PRICE_ARB_SPREAD_USDC",
        "description": (
            "Use this tool before executing a cross-venue arbitrage trade to check "
            "whether a global reference price (Coinbase spot, CoinGecko fallback - not a "
            "specific exchange orderbook) and a DEX pool price diverge enough to be "
            "worth trading after an assumed flat gas cost. Returns gross/net spread "
            "percentages and an is_profitable boolean. Do not use for DEX-only liquidity "
            "depth checks or contract security. Paid in USDC on Base."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. SUI, BTC, ETH.",
                },
                "network": {
                    "type": "string",
                    "description": "GeckoTerminal network id, e.g. base, eth. Defaults to base.",
                },
                "trade_size_usd": {
                    "type": "number",
                    "description": "Hypothetical trade size in USD. Defaults to 1000.",
                },
                "min_spread_threshold_pct": {
                    "type": "number",
                    "description": "Net spread threshold (%) above which is_profitable is true. Defaults to 0.8.",
                },
                "pool_address": {
                    "type": "string",
                    "description": "Specific DEX pool contract address (optional if token_address is given).",
                },
                "token_address": {
                    "type": "string",
                    "description": "Token contract address - the most liquid pool is auto-selected (optional if pool_address is given).",
                },
            },
            "required": ["symbol"],
        },
        "output_schema": ARB_SPREAD_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "calendar.macro_dday",
        "path": "/v1/calendar/macro-dday",
        "price_attr": "PRICE_MACRO_DDAY_USDC",
        "description": (
            "Use this tool when an agent plans trading schedules around major US "
            "macroeconomic volatility, specifically days remaining until FOMC rate "
            "decisions, CPI prints, or NFP jobs reports. Zero-dependency static 2026 "
            "macro calendar with 100% uptime and no upstream failure risk. Do not use "
            "for real-time market price data or economic forecast consensus figures. "
            "No input parameters. Paid in USDC on Base."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "output_schema": MACRO_DDAY_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },
    {
        "name": "unlocks.dump_risk",
        "path": "/v1/unlocks/dump-risk",
        "price_attr": "PRICE_DUMP_RISK_USDC",
        "description": (
            "Use this tool to evaluate token unlock schedules, vesting cliffs, and "
            "upcoming VC/team dump pressure relative to circulating supply. Analyzes "
            "supply overhang risk before taking mid-to-long term positions. Do not use "
            "for intra-day slippage or real-time transaction simulation. Paid in USDC "
            "on Base."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "output_schema": DUMP_RISK_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
        "dump_risk_only": True,
    },
]
_TOOL_BY_NAME = {t["name"]: t for t in _TOOLS}


def _dump_risk_enabled() -> bool:
    # 실제 config.py에 이미 존재하는 플래그(REST build_routes()가 이미 씀) -
    # 혹시 몰라 getattr로 방어적으로 읽는다.
    return bool(getattr(settings, "DUMP_RISK_ENABLED", True))


def _build_tool_list() -> list[dict]:
    # 2026-09 수정: dump_risk_enabled=False는 "기능 꺼짐"이 아니라 app/payment.py
    # build_routes()의 실제 의미대로 "결제 게이트 없이 무료로 서빙 중"이다(REST와
    # 동일). 그래서 도구를 목록에서 숨기거나 tools/call을 거부하지 않고, 항상
    # 노출하되 가격만 0으로 정확히 표시한다 - REST가 무료로 응답하는데 MCP가
    # "이 도구는 없다"고 하면 표면 간에 사실이 어긋난다.
    tools = []
    for t in _TOOLS:
        is_free_now = bool(t.get("dump_risk_only")) and not _dump_risk_enabled()
        if is_free_now:
            price = 0.0
            description = t["description"].replace(
                "Paid in USDC on Base.",
                "FREE ONBOARDING TOOL - Zero payment required by default. Kept free so "
                "autonomous agents can verify network connectivity, latency, and output "
                "schema validity before initiating x402 paid pipelines.",
            )
        else:
            price = getattr(settings, t["price_attr"])
            description = t["description"]
        tools.append(
            {
                "name": t["name"],
                "description": description,
                "inputSchema": t["input_schema"],
                "outputSchema": t["output_schema"],
                "annotations": t["annotations"],
                "_meta": {
                    "x402": {
                        "price_usdc": price,
                        "network": ACTIVE_NETWORK,
                        "asset": "USDC",
                        "pay_to": settings.RECEIVER_WALLET_ADDRESS,
                        "rest_equivalent": f"GET {t['path']}",
                        "currently_free": is_free_now,
                    }
                },
            }
        )
    return tools


def _jsonrpc_result(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# 2026-09-03 실제 배포본에 curl로 직접 확인한 결과: 이 세션이 쓰는 x402 SDK
# 버전은 402 응답의 실제 결제 조건(accepts 배열 등)을 본문이 아니라
# "payment-required" 응답 헤더에 base64(JSON)로 싣는다 - 본문은 그냥 "{}"다.
# 그래서 본문만 릴레이하면 클라이언트가 결제 조건을 전혀 못 받는다 - 아래
# hop-by-hop/길이 관련 헤더만 빼고 나머지는 전부 그대로 릴레이해서
# payment-required가 반드시 같이 넘어가게 한다(방향은 바뀔 수 있는 SDK
# 세부사항이라 특정 헤더 이름 하나만 하드코딩하지 않았다).
_HOP_BY_HOP_RESPONSE_HEADERS = {
    "connection", "keep-alive", "transfer-encoding", "upgrade",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "content-length", "content-encoding", "date", "server",
}


def _relay_headers(upstream: httpx.Response) -> dict[str, str]:
    """내부 self-call 응답 헤더 중 hop-by-hop/길이 관련만 빼고 그대로 릴레이한다.

    402(결제 조건)와 200(결제 정산 영수증) 양쪽 다 이걸 쓴다 - 두 경우 모두
    실제 정보가 본문이 아니라 헤더에 실려 오기 때문이다.
    """
    return {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP_RESPONSE_HEADERS}


async def _call_tool(body: dict, request: Request, client: httpx.AsyncClient) -> Response:
    req_id = body.get("id")
    params = body.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}

    tool = _TOOL_BY_NAME.get(name)
    if tool is None:
        return JSONResponse(content=_jsonrpc_error(req_id, -32602, f"알 수 없는 tool 이름: {name}"))

    query = {k: v for k, v in arguments.items() if v is not None}
    forward_headers = {}
    # x402 v2(우리 서버가 실제로 쓰는 버전)는 재시도 결제 헤더 이름이
    # "PAYMENT-SIGNATURE"다 - "X-PAYMENT"는 구버전(v1) 전용이다(설치된
    # x402==2.21.0의 x402/http/x402_http_client_base.py에서 직접 확인).
    # 어느 쪽으로 오든 그대로 실어 보내도록 둘 다 확인한다.
    payment_signature = request.headers.get("payment-signature")
    if payment_signature:
        forward_headers["PAYMENT-SIGNATURE"] = payment_signature
    legacy_x_payment = request.headers.get("x-payment")
    if legacy_x_payment:
        forward_headers["X-PAYMENT"] = legacy_x_payment

    try:
        upstream = await client.get(tool["path"], params=query, headers=forward_headers)
    except Exception as e:
        logger.exception("MCP tools/call 내부 self-call 실패: %s", name)
        return JSONResponse(content=_jsonrpc_error(req_id, -32000, f"내부 호출 실패: {e}"))

    if upstream.status_code == 402:
        # x402 결제 필요 - 실제 결제 조건은 본문이 아니라 payment-required
        # 헤더에 실려 있다(위 _HOP_BY_HOP_RESPONSE_HEADERS 주석 참고) - 본문과
        # 헤더를 함께 그대로 릴레이한다. MCP에는 402에 대응하는 JSON-RPC
        # 에러 코드가 없으므로, 이 요청 자체의 HTTP 응답을 그대로 402로
        # 릴레이한다(모듈 docstring 참고).
        return Response(content=upstream.content, status_code=402, headers=_relay_headers(upstream))
    if upstream.status_code >= 400:
        return JSONResponse(
            content=_jsonrpc_error(
                req_id, -32000, f"업스트림 오류 ({upstream.status_code}): {upstream.text[:500]}"
            )
        )

    data = upstream.json()
    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}], "isError": False}
    # 결제 성공 시에도 정산 영수증(payment-response류 헤더 - 402 때와 마찬가지로
    # 정확한 이름을 하드코딩하지 않았다)이 실려 있으므로, 클라이언트가
    # get_payment_settle_response()로 읽을 수 있도록 그대로 같이 릴레이한다.
    return JSONResponse(content=_jsonrpc_result(req_id, result), headers=_relay_headers(upstream))


async def _dispatch(body, request: Request, client: httpx.AsyncClient) -> Response:
    if isinstance(body, list):
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_error(
                None, -32600, "JSON-RPC 배치 요청은 지원하지 않습니다 (MCP 2025-06-18+에서 제거됨) - 요청을 하나씩 보내세요."
            ),
        )
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "method" not in body:
        return JSONResponse(
            status_code=400,
            content=_jsonrpc_error(body.get("id") if isinstance(body, dict) else None, -32600, "Invalid Request"),
        )

    method = body["method"]
    req_id = body.get("id")
    is_notification = "id" not in body

    if method == "initialize":
        client_params = body.get("params") or {}
        protocol_version = client_params.get("protocolVersion") or _PROTOCOL_VERSION_FALLBACK
        result = {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "alphapipeline-mcp", "version": "1.0.0"},
            "instructions": _SERVER_INSTRUCTIONS,
        }
        return JSONResponse(content=_jsonrpc_result(req_id, result))

    if is_notification or method == "notifications/initialized":
        # JSON-RPC 알림(id 없음)에는 응답 본문을 보내지 않는다.
        return Response(status_code=202)

    if method == "ping":
        return JSONResponse(content=_jsonrpc_result(req_id, {}))

    if method == "tools/list":
        return JSONResponse(content=_jsonrpc_result(req_id, {"tools": _build_tool_list()}))

    if method == "tools/call":
        return await _call_tool(body, request, client)

    return JSONResponse(content=_jsonrpc_error(req_id, -32601, f"Method not found: {method}"))


def register_mcp_routes(app: FastAPI) -> None:
    """main.py에서 앱 생성 직후 한 번만 호출한다.

    /mcp 는 의도적으로 app/payment.py의 결제 라우트 dict에 등록하지 않는다 -
    initialize/tools/list는 항상 무료로 열려 있어야 하고, tools/call의 실제
    결제 게이팅은 내부 self-call이 이미 결제가 걸려 있는 기존 REST 경로를
    타면서 자동으로 적용된다 (모듈 docstring 참고).
    """
    internal_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp-internal")

    @app.post("/mcp", include_in_schema=False)
    async def mcp_endpoint(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content=_jsonrpc_error(None, -32700, "Parse error"))
        return await _dispatch(body, request, internal_client)

    @app.get("/mcp", include_in_schema=False)
    async def mcp_streaming_not_supported():
        return JSONResponse(
            status_code=405,
            content={
                "error": "method_not_allowed",
                "message": (
                    "This MCP server is stateless Streamable HTTP - POST only. There is no "
                    "SSE streaming (GET) because the server never sends unsolicited "
                    "notifications. / 이 MCP 서버는 상태 비저장(stateless) Streamable HTTP - "
                    "POST만 지원합니다. 서버가 먼저 보내는 알림이 없어 SSE 스트리밍(GET)은 "
                    "제공하지 않습니다."
                ),
            },
        )
