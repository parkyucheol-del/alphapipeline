"""
AlphaPipeline - README.md를 배지/배너/아이콘/퀵스타트가 포함된 최종본으로
교체하는 스크립트.

콘솔에 긴 here-string을 직접 붙여넣다가 줄이 씹히는 문제가 있어서,
지금까지 썼던 패치 스크립트들과 같은 방식(파이썬 파일 실행)으로 대체함.

- 기존 README.md는 README.md.bak으로 백업 (이미 수동으로 백업했다면 덮어쓰지 않음)
- docs/banner.png, docs/icon.png 는 건드리지 않음 (이미 넣었다고 확인됨)
"""
import sys
from pathlib import Path

README = Path("README.md")
BACKUP = Path("README.md.bak")

NEW_README = '''<p align="center">
  <img src="docs/banner.png" alt="AlphaPipeline banner" width="100%">
</p>

# AlphaPipeline

**Pay-per-call ($0.005–$0.03 USDC) market/on-chain data API for AI agents and trading bots — no signup, no API key, no OAuth.** Authenticate and pay in a single request via the [x402 protocol](https://docs.x402.org) (HTTP 402) on Base, or call it as a remote MCP server from Claude Desktop, Cursor, or any MCP client.

[![Smithery](https://img.shields.io/badge/Smithery-Listed-orange)](https://smithery.ai/servers/parkyucheol/alphapipeline)
[![x402 Bazaar](https://img.shields.io/badge/x402-Bazaar-blue)](https://www.x402bazaar.org/)
[![Glama](https://img.shields.io/badge/Glama-Listed-green)](https://glama.ai/mcp/servers)

---

## Why AlphaPipeline

AlphaPipeline is a **machine-first data API**. Every endpoint is metered per call using x402: a request without a payment header gets back a standard HTTP 402 response describing exactly how to pay (asset, amount, network, recipient). No account creation, no dashboard, no API key issuance — sign, retry the request with the payment attached, and you get the data back in the same request/response cycle.

It is also exposed as a **remote MCP server** (`POST /mcp`) so agent frameworks (Claude Desktop, Cursor, LangChain, CrewAI, and anything else that speaks MCP) can discover and call it with zero custom integration code.

- **Discovery is always free.** `initialize` and `tools/list` never require payment — browse the full tool catalog before you decide to pay.
- **One tool is a permanent free onboarding endpoint** (`dump_risk`, see below) so an agent can verify connectivity, latency, and response schema before it starts paying for the rest.
- **Settlement is Coinbase CDP (Developer Platform) Facilitator** on Base mainnet (`eip155:8453`), asset USDC (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`).

> The endpoints have not been reviewed by Coinbase — please ensure you trust them prior to sending funds.

---

## Quickstart — Remote MCP

Add this to your MCP client's config (Claude Desktop `claude_desktop_config.json`, Cursor `.cursor/mcp.json`, or equivalent):

```json
{
  "mcpServers": {
    "alphapipeline": {
      "url": "https://alphapipeline-eu.onrender.com/mcp"
    }
  }
}
```

`tools/list` works immediately with no payment. `tools/call` on a priced tool requires an x402-aware client that can sign and retry a payment (a wallet-holding bot/agent client — most stock chat UIs cannot pay automatically). See [`examples/`](./examples) for a working Python client using the official `x402` SDK.

### Quickstart — Plain REST / x402

```bash
curl -s https://alphapipeline-eu.onrender.com/v1/market/kimchi-alert?symbol=BTC
# -> HTTP 402, payment terms in the `payment-required` response header (base64 JSON)
# sign a payment, retry with the PAYMENT-SIGNATURE header attached -> 200 + data
```

Full protocol reference: [`/llms.txt`](https://alphapipeline-eu.onrender.com/llms.txt) · [x402 docs](https://docs.x402.org)

---

## Tools / Endpoints

| Tool (MCP) | REST endpoint | Price | What it does |
|---|---|---|---|
| `kimchi_alert` | `GET /v1/market/kimchi-alert` | $0.01 | Real-time Korea (Upbit) vs global crypto price premium — the "kimchi premium" — with reverse-premium and 1h-surge alerts. |
| `ai_markdown` | `GET /v1/tools/ai-markdown` | $0.005 | Converts any webpage URL into clean, ad-free Markdown optimized for LLM context windows. |
| `token_risk` | `GET /v1/security/token-risk` | $0.02 | GoPlus/Honeypot.is-backed contract security check — honeypot flag, buy/sell tax, mintability, ownership renouncement. |
| `funding_rate` | `GET /v1/derivatives/funding-rate` | $0.01 | Bybit (primary) / Binance (fallback) perpetual futures funding rate. |
| `dex_liquidity_slippage` | `GET /v1/dex/liquidity-slippage` | $0.02 | GeckoTerminal-backed DEX pool liquidity and estimated trade slippage. |
| `macro_dday` | `GET /v1/calendar/macro-dday` | $0.01 | Countdown to the nearest major US macro event (FOMC, CPI, NFP) from a static, pre-loaded calendar — no external API call, never fails on an upstream outage. |
| `dump_risk` | `GET /v1/unlocks/dump-risk` | **Free** | On-chain (Sablier) proxy for token unlock/vesting dump risk. Kept free by default as an onboarding tool so agents can verify the service before paying for the rest. |

Every response is timestamped in both UTC and KST, and every priced endpoint's payment prompt reads "Paid in USDC on Base." so a human looking at the 402 screen in a browser isn't left guessing which chain's USDC to send.

---

## Links

- Live service: https://alphapipeline-eu.onrender.com
- Agent-readable spec: https://alphapipeline-eu.onrender.com/llms.txt
- OpenAPI docs: https://alphapipeline-eu.onrender.com/docs
- x402 Bazaar listing: https://www.x402bazaar.org/
- Glama listing: https://glama.ai/mcp/servers
- x402 protocol: https://docs.x402.org

## License

See [LICENSE](./LICENSE).
'''


def main() -> None:
    if README.exists() and not BACKUP.exists():
        BACKUP.write_text(README.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"백업 완료: {BACKUP}")
    elif BACKUP.exists():
        print(f"백업 파일이 이미 있어서 다시 백업하지 않았습니다: {BACKUP}")

    README.write_text(NEW_README, encoding="utf-8")
    print(f"완료: {README} 교체했습니다.")
    print("다음 단계:")
    print('  git add README.md docs\\banner.png docs\\icon.png')
    print('  git commit -m "Add README with badges, banner/icon, and MCP quickstart"')
    print("  git push")


if __name__ == "__main__":
    main()
