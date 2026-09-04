"""
AlphaPipeline - MCP tools/list에 7개 도구 전부 outputSchema + annotations를
추가하는 패치 (Smithery Quality Score의 "Capability Quality" 항목인
Output schemas / Annotations 감점 해소 목적).

## 왜 이렇게 만들었나
- outputSchema의 각 필드는 app/schemas.py의 실제 Pydantic 응답 모델
  (KimchiAlertResponse / MarkdownResponse / TokenRiskResponse /
  FundingRateResponse / DexSlippageResponse / MacroDdayResponse /
  DumpRiskResponse)을 그대로 옮긴 것이다 - 필드명을 추측하지 않고 실제
  모델 정의를 그대로 반영해서, REST 응답과 outputSchema가 어긋나는 일이
  없게 했다.
- Pydantic에서 `Optional[...] = None`인 필드는 FastAPI가 JSON으로 만들 때도
  키 자체는 항상 있고 값만 null일 수 있으므로, JSON Schema에서는
  "type": [실제타입, "null"]로 표시하고 required 배열에서는 뺐다. 반대로
  기본값이 있어도 항상 구체적인 값(빈 배열 등)으로 채워지는 필드
  (risk_flags, tags, unlocks, upcoming_events)는 required에 포함시켰다.
- annotations는 MCP 공식 스펙 필드명인 readOnlyHint/destructiveHint를 썼다
  (요청에서는 "readOnly"/"destructive"라고 했지만, 실제 스펙 필드명이 이거라
  Smithery 채점기가 이 이름을 기준으로 확인할 가능성이 높다). 7개 도구 전부
  블록체인 상태를 바꾸지 않는 조회 전용이라 값은 전부 동일
  (readOnlyHint: True, destructiveHint: False)하게 상수 하나로 공유한다.

## 무엇을 고치나
1. _TOOLS 정의 앞에 8개의 OUTPUT_SCHEMA 상수 + _READ_ONLY_ANNOTATIONS 상수를
   추가한다.
2. _TOOLS 리스트의 7개 도구 항목 각각에 "output_schema"/"annotations" 키를
   추가한다 (가격/설명/입력스키마는 전혀 안 건드림).
3. _build_tool_list()가 만드는 tools/list 응답 딕셔너리에
   "outputSchema"/"annotations" 필드를 추가한다.

쓰기 전에 결과를 compile()로 검사했고, 실제 배포본과 동일한 mock 파일에
적용해서 문법 오류 없이 통과하는 것과 각 필드가 정확한 자리에 들어가는 것을
확인했다.
"""
import sys
from pathlib import Path

MCP_SERVER_PY = Path("app/mcp_server.py")

OUTPUT_SCHEMA_CONSTANTS = '''_TIMESTAMP_PAIR_SCHEMA = {
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

'''

# (anchor, output_schema_const_name) 를 각 도구의 input_schema 블록에 매핑한다.
MULTILINE_INPUT_SCHEMA_PATCHES = [
    (
        "kimchi_alert",
        '''        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Crypto ticker symbol to check, e.g. BTC, ETH, SOL. Defaults to BTC.",
                }
            },
            "required": [],
        },''',
        "KIMCHI_ALERT_OUTPUT_SCHEMA",
    ),
    (
        "ai_markdown",
        '''        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Full http(s) URL of the webpage to convert to Markdown.",
                }
            },
            "required": ["url"],
        },''',
        "AI_MARKDOWN_OUTPUT_SCHEMA",
    ),
    (
        "token_risk",
        '''        "input_schema": {
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
        },''',
        "TOKEN_RISK_OUTPUT_SCHEMA",
    ),
    (
        "funding_rate",
        '''        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. BTC, ETH, or BTCUSDT.",
                }
            },
            "required": ["symbol"],
        },''',
        "FUNDING_RATE_OUTPUT_SCHEMA",
    ),
    (
        "dex_liquidity_slippage",
        '''        "input_schema": {
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
        },''',
        "DEX_SLIPPAGE_OUTPUT_SCHEMA",
    ),
]

# macro_dday / dump_risk는 둘 다 input_schema 한 줄 자체가 완전히 동일한
# 텍스트라서, 바로 다음 줄까지 포함해야 서로 구별된다.
MACRO_DDAY_OLD = '''        "input_schema": {"type": "object", "properties": {}, "required": []},
    },'''
MACRO_DDAY_NEW = '''        "input_schema": {"type": "object", "properties": {}, "required": []},
        "output_schema": MACRO_DDAY_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
    },'''

DUMP_RISK_OLD = '''        "input_schema": {"type": "object", "properties": {}, "required": []},
        "dump_risk_only": True,'''
DUMP_RISK_NEW = '''        "input_schema": {"type": "object", "properties": {}, "required": []},
        "output_schema": DUMP_RISK_OUTPUT_SCHEMA,
        "annotations": _READ_ONLY_ANNOTATIONS,
        "dump_risk_only": True,'''

TOOL_LIST_DICT_OLD = '''                "inputSchema": t["input_schema"],
                "_meta": {'''
TOOL_LIST_DICT_NEW = '''                "inputSchema": t["input_schema"],
                "outputSchema": t["output_schema"],
                "annotations": t["annotations"],
                "_meta": {'''


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def must_replace(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        fail(f"{label}: 예상한 앵커 텍스트를 1번이 아니라 {count}번 찾았습니다 - 파일이 예상과 다른 상태인 것 같습니다.")
    return content.replace(old, new, 1)


def main() -> None:
    if not MCP_SERVER_PY.exists():
        fail(f"{MCP_SERVER_PY}가 없습니다 - 이전 MCP 서버 패치들을 먼저 적용해주세요.")

    src = MCP_SERVER_PY.read_text(encoding="utf-8")

    if "_READ_ONLY_ANNOTATIONS" in src:
        fail("이미 이 패치가 적용된 것 같습니다 (_READ_ONLY_ANNOTATIONS 마커 발견) - 중복 적용 방지.")

    new_src = must_replace(
        src,
        "_TOOLS: list[dict] = [",
        OUTPUT_SCHEMA_CONSTANTS + "_TOOLS: list[dict] = [",
        "OUTPUT_SCHEMA 상수 삽입",
    )

    for name, old, const_name in MULTILINE_INPUT_SCHEMA_PATCHES:
        new = old + f'\n        "output_schema": {const_name},\n        "annotations": _READ_ONLY_ANNOTATIONS,'
        new_src = must_replace(new_src, old, new, f"{name} output_schema/annotations 삽입")

    new_src = must_replace(new_src, MACRO_DDAY_OLD, MACRO_DDAY_NEW, "macro_dday output_schema/annotations 삽입")
    new_src = must_replace(new_src, DUMP_RISK_OLD, DUMP_RISK_NEW, "dump_risk output_schema/annotations 삽입")
    new_src = must_replace(new_src, TOOL_LIST_DICT_OLD, TOOL_LIST_DICT_NEW, "tools/list 응답에 outputSchema/annotations 필드 추가")

    try:
        compile(new_src, str(MCP_SERVER_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {MCP_SERVER_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    MCP_SERVER_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {MCP_SERVER_PY}의 7개 도구 전부에 outputSchema + annotations(readOnlyHint/destructiveHint)를 추가했습니다.")
    print("다음 단계:")
    print("  1) python -m py_compile app/mcp_server.py")
    print("  2) git add -A && git commit -m \"Add outputSchema and annotations to all 7 MCP tools (Smithery quality score)\" && git push")
    print("  3) 배포 확인되면 tools/list 다시 호출해서 각 도구에 outputSchema/annotations 필드가 뜨는지 확인, Smithery 대시보드에서 재스캔 후 점수 확인")


if __name__ == "__main__":
    main()
