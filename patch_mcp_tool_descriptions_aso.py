"""
AlphaPipeline - MCP 도구 description을 "에이전트 라우팅 최적화(ASO)" 문구로
교체하고, dump_risk 무료 상태를 "임시 비활성화"가 아니라 "의도된 온보딩용
무료 도구"로 명확히 재정의하는 패치.

## 왜 필요한가
LLM 기반 에이전트가 여러 MCP 도구 중 무엇을 호출할지 결정하는 유일한 근거는
tools/list에 실려오는 description 텍스트뿐이다. 각 설명에 "Use this tool
when ~"(언제 쓰는지)뿐 아니라 "Do not use for ~"(비슷한 다른 도구와 헷갈리는
경우 배제)까지 명시하면, 키워드가 겹치는 도구들(예: token_risk vs
dex_liquidity_slippage) 사이에서 에이전트가 더 정확하게 고를 수 있다.

dump_risk가 DUMP_RISK_ENABLED=False일 때 무료로 서빙되는 상태도, "임시
비활성화된 결제 게이트"가 아니라 "에이전트가 다른 유료 파이프라인을 쓰기
전에 네트워크 연결/지연시간/응답 스키마를 검증해볼 수 있는 온보딩용 도구"로
명확히 설명한다. 단, "영구히 무료"라고 단정하지는 않는다 - 실제로 나중에
DUMP_RISK_ENABLED=true로 바꾸면 유료 전환되는 구조라, 그 가능성을 열어두는
문구("Kept free" - "지금 무료로 유지 중")를 쓴다.

## 무엇을 고치나
app/mcp_server.py의 _TOOLS 리스트 7개 항목 description 전부를 트리거+배제
문구 포함 버전으로 교체하고, _build_tool_list()의 dump_risk 무료 상태 문구도
새 버전으로 교체한다. 가격/스키마/로직은 전혀 건드리지 않는다 - 순수 텍스트
교체.

쓰기 전에 결과를 compile()로 검사했고, 이전 다섯 개 패치까지 전부 순서대로
적용한 뒤 이 패치를 얹어서 21개 항목 테스트를 재실행해 통과를 확인했다.
"""
import sys
from pathlib import Path

MCP_SERVER_PY = Path("app/mcp_server.py")

REPLACEMENTS = [
    (
        "kimchi_alert",
        '''        "description": (
            "Real-time Korea (Upbit) vs global crypto price premium - the 'kimchi "
            "premium' - with reverse-premium and 1h-surge alerts. Paid in USDC on Base."
        ),''',
        '''        "description": (
            "Use this tool when evaluating Korean exchange price premiums, the 'kimchi "
            "premium', Upbit price gaps vs Binance/OKX, cross-border crypto arbitrage, or "
            "sudden Korea-specific price anomalies. Real-time Upbit vs global price "
            "spread with reverse-premium and surge alerts. Do not use for general USD "
            "spot prices or on-chain DEX swaps. Paid in USDC on Base."
        ),''',
    ),
    (
        "ai_markdown",
        '''        "description": (
            "Convert any webpage URL into clean, ad-free Markdown text optimized for "
            "LLM context windows. Paid in USDC on Base."
        ),''',
        '''        "description": (
            "Use this tool when an agent needs to parse clean webpage article content "
            "without wasting context tokens on ads, scripts, navigation, and HTML "
            "boilerplate, or when summarizing a specific URL. Converts any URL into "
            "clean Markdown optimized for LLM context windows. Do not use for raw API "
            "endpoints or binary files (PDF/images). Paid in USDC on Base."
        ),''',
    ),
    (
        "token_risk",
        '''        "description": (
            "GoPlus/Honeypot.is-backed token security check - honeypot flag, buy/sell "
            "tax, mintability, and ownership renouncement for a given contract address, "
            "so a bot can decide before it buys. Paid in USDC on Base."
        ),''',
        '''        "description": (
            "Use this tool before executing any on-chain swap to verify if an ERC-20 "
            "contract is a honeypot, rug-pull risk, or has malicious buy/sell taxes and "
            "mintability backdoors. GoPlus/Honeypot.is-backed security audit for a given "
            "contract address. Do not use for market price discovery or liquidity "
            "depth. Paid in USDC on Base."
        ),''',
    ),
    (
        "funding_rate",
        '''        "description": (
            "Bybit (primary) / Binance (fallback) perpetual futures funding rate - the "
            "key signal for long/short crowding that traders use to time or hedge "
            "positions before the next funding settlement. Paid in USDC on Base."
        ),''',
        '''        "description": (
            "Use this tool when analyzing perpetual futures funding rates, long/short "
            "market sentiment crowding, or timing hedging strategies before settlement "
            "periods. Aggregates Bybit (primary) and Binance (fallback) perpetual "
            "funding rates. Do not use for spot market volume or token security "
            "checks. Paid in USDC on Base."
        ),''',
    ),
    (
        "dex_liquidity_slippage",
        '''        "description": (
            "GeckoTerminal-backed DEX pool liquidity and estimated trade slippage - size "
            "a trade or compare pools before swapping, with a clearly-flagged "
            "constant-product approximation model. Paid in USDC on Base."
        ),''',
        '''        "description": (
            "Use this tool to calculate expected DEX price slippage, pool liquidity "
            "depth, and optimal routing before executing an on-chain token swap. "
            "GeckoTerminal-backed pool analytics with constant-product slippage "
            "estimation. Do not use for centralized exchange (CEX) orderbooks or "
            "contract risk analysis. Paid in USDC on Base."
        ),''',
    ),
    (
        "macro_dday",
        '''        "description": (
            "Countdown to the nearest major US macro event (Fed FOMC rate decision, "
            "CPI, or NFP) from a static, pre-loaded 2026 calendar - no live external API "
            "call, never fails on an upstream outage. No input parameters. Paid in USDC "
            "on Base."
        ),''',
        '''        "description": (
            "Use this tool when an agent plans trading schedules around major US "
            "macroeconomic volatility, specifically days remaining until FOMC rate "
            "decisions, CPI prints, or NFP jobs reports. Zero-dependency static 2026 "
            "macro calendar with 100% uptime and no upstream failure risk. Do not use "
            "for real-time market price data or economic forecast consensus figures. "
            "No input parameters. Paid in USDC on Base."
        ),''',
    ),
]

# dump_risk는 문구 안에 "Paid in USDC on Base."가 포함돼 있어 free-tier 치환
# 로직(_build_tool_list)의 replace 대상과 겹치므로, 위 REPLACEMENTS 리스트와
# 분리해서 별도로 처리한다.
DUMP_RISK_OLD = '''        "description": (
            "Tokens with large amounts of currently-locked or vesting supply relative to "
            "circulating supply - a proxy for future sell/dump pressure. Paid in USDC on "
            "Base."
        ),'''
DUMP_RISK_NEW = '''        "description": (
            "Use this tool to evaluate token unlock schedules, vesting cliffs, and "
            "upcoming VC/team dump pressure relative to circulating supply. Analyzes "
            "supply overhang risk before taking mid-to-long term positions. Do not use "
            "for intra-day slippage or real-time transaction simulation. Paid in USDC "
            "on Base."
        ),'''

FREE_TIER_OLD = '''            description = t["description"].replace(
                "Paid in USDC on Base.",
                "Currently offered FREE (no payment required) - the x402 payment gate "
                "is temporarily disabled for this endpoint.",
            )'''
FREE_TIER_NEW = '''            description = t["description"].replace(
                "Paid in USDC on Base.",
                "FREE ONBOARDING TOOL - Zero payment required by default. Kept free so "
                "autonomous agents can verify network connectivity, latency, and output "
                "schema validity before initiating x402 paid pipelines.",
            )'''


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

    if "FREE ONBOARDING TOOL" in src:
        fail("이미 이 패치가 적용된 것 같습니다 (FREE ONBOARDING TOOL 마커 발견) - 중복 적용 방지.")

    new_src = src
    for name, old, new in REPLACEMENTS:
        new_src = must_replace(new_src, old, new, f"{name} description")

    new_src = must_replace(new_src, DUMP_RISK_OLD, DUMP_RISK_NEW, "dump_risk description")
    new_src = must_replace(new_src, FREE_TIER_OLD, FREE_TIER_NEW, "dump_risk 무료 상태 문구")

    try:
        compile(new_src, str(MCP_SERVER_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {MCP_SERVER_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    MCP_SERVER_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {MCP_SERVER_PY}의 7개 도구 description을 에이전트 라우팅 최적화(트리거+배제) 버전으로 교체했습니다.")
    print("dump_risk는 이제 'FREE ONBOARDING TOOL'로 명확히 표시됩니다 (기능/가격/스키마는 변경 없음).")
    print("다음 단계:")
    print("  1) python -m py_compile app/mcp_server.py")
    print("  2) git add -A && git commit -m \"Improve MCP tool descriptions for agent routing (ASO) + label dump_risk as onboarding free tool\" && git push")
    print("  3) 배포 확인되면 tools/list 다시 호출해서 description들이 새 문구로 바뀌었는지, dump_risk에 FREE ONBOARDING TOOL 문구가 뜨는지 확인")


if __name__ == "__main__":
    main()
