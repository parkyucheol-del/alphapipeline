import os
import json
import time
import requests

CF_ACCOUNT_ID = os.environ["CF_ACCOUNT_ID"]
CF_API_TOKEN = os.environ["CF_API_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

CLEF_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/cloudflare/clef-flash"
HAIKU_URL = "https://api.anthropic.com/v1/messages"

SCENARIOS = [
    {"name": "thin_vol_no_news_small_swing",
     "swing": "28% to 31% (3pp) in the last 15 minutes", "volume": 1200, "depth": 3500, "news": "none detected"},
    {"name": "thin_vol_no_news_large_swing",
     "swing": "31% to 44% (13pp) in the last 15 minutes", "volume": 1200, "depth": 3500, "news": "none detected"},
    {"name": "high_vol_news_large_swing",
     "swing": "31% to 44% (13pp) in the last 15 minutes", "volume": 85000, "depth": 3500,
     "news": "Fed signaled a rate cut is likely at the next meeting"},
    {"name": "high_vol_no_news_large_swing",
     "swing": "31% to 44% (13pp) in the last 15 minutes", "volume": 85000, "depth": 3500, "news": "none detected"},
    {"name": "thin_vol_news_large_swing",
     "swing": "31% to 44% (13pp) in the last 15 minutes", "volume": 1200, "depth": 3500,
     "news": "Fed signaled a rate cut is likely at the next meeting"},
    {"name": "high_vol_news_small_swing",
     "swing": "28% to 31% (3pp) in the last 15 minutes", "volume": 85000, "depth": 3500,
     "news": "Fed signaled a rate cut is likely at the next meeting"},
]

STATE_TEMPLATE = (
    "HIP-4 BTC YES market. Probability swing: {swing}. "
    "15-min trading volume: {volume} USD (synthetic/hypothetical, not real HIP-4 data). "
    "Orderbook depth: {depth} USD (synthetic/hypothetical). News: {news}."
)

QUESTION_SIGNAL = (
    "Does this probability swing represent a real, information-driven market-moving "
    "signal rather than noise or a thin-liquidity fluke?"
)
QUESTION_ACTION = "Given this swing, what should a monitoring bot do?"
ACTION_CRITERIA = {
    "alert": "Notify the user immediately",
    "watch": "Keep monitoring, no notification yet",
    "ignore": "Not significant enough to act on",
}

CLEF_INPUT_PRICE_PER_MTOK = 0.09  # per Daily Alpha briefing; output untracked/free
HAIKU_INPUT_PRICE_PER_MTOK = 0.10
HAIKU_OUTPUT_PRICE_PER_MTOK = 0.50


def call_clef(state_dict):
    body = {
        "model": "clef-flash",
        "state": state_dict,
        "questions": {
            "signal": {"type": "noul", "instructions": QUESTION_SIGNAL},
            "action": {"type": "choice", "instructions": QUESTION_ACTION, "criteria": ACTION_CRITERIA},
        },
    }
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    r = requests.post(CLEF_URL, headers=headers, json=body, timeout=30)
    latency_ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    data = r.json()["result"]
    signal = data["answers"]["signal"]["noul"]
    action = data["answers"]["action"]["choice"]
    in_tok = data["usage"]["input_tokens"]
    out_tok = data["usage"]["output_tokens"]
    cost = in_tok / 1_000_000 * CLEF_INPUT_PRICE_PER_MTOK
    return {"signal": signal, "action": action, "latency_ms": latency_ms, "cost_usd": cost,
            "in_tok": in_tok, "out_tok": out_tok}


def call_haiku(state_text):
    system = (
        "You are a quantitative signal-detection model. Given a market situation, "
        "output ONLY a JSON object with exactly two keys: \"signal_probability\" "
        "(a float from 0 to 1, how likely this is a real information-driven "
        "market-moving signal rather than noise) and \"action\" (one of \"alert\", "
        "\"watch\", \"ignore\"). No other text, no markdown fences."
    )
    body = {
        "model": "claude-haiku-5-5",
        "max_tokens": 300,
        "thinking": {"type": "disabled"},
        "system": system,
        "messages": [{"role": "user", "content": state_text}],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    t0 = time.perf_counter()
    r = requests.post(HAIKU_URL, headers=headers, json=body, timeout=30)
    latency_ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    data = r.json()
    if data.get("type") == "error":
        raise RuntimeError(f"Haiku API error: {json.dumps(data)}")
    text = None
    for block in data.get("content", []):
        if block.get("type") == "text":
            text = block.get("text")
            break
    if text is None:
        raise RuntimeError(f"No text block in Haiku response: {json.dumps(data)}")
    text = text.strip().strip("`").strip()
    if text.startswith("json"):
        text = text[4:].strip()
    parsed = json.loads(text)
    in_tok = data["usage"]["input_tokens"]
    out_tok = data["usage"]["output_tokens"]
    cost = in_tok / 1_000_000 * HAIKU_INPUT_PRICE_PER_MTOK + out_tok / 1_000_000 * HAIKU_OUTPUT_PRICE_PER_MTOK
    return {"signal": parsed["signal_probability"], "action": parsed["action"],
            "latency_ms": latency_ms, "cost_usd": cost, "in_tok": in_tok, "out_tok": out_tok}


def main():
    rows = []
    for sc in SCENARIOS:
        state_dict = {
            "market": "HIP-4 BTC YES market",
            "probability_change": sc["swing"],
            "volume_usd_15m": sc["volume"],
            "orderbook_depth_usd": sc["depth"],
            "news": sc["news"],
        }
        state_text = STATE_TEMPLATE.format(**sc)

        clef = call_clef(state_dict)
        haiku = call_haiku(state_text)

        rows.append({"scenario": sc["name"], "clef": clef, "haiku": haiku})

        print(f"\n=== {sc['name']} ===")
        print(f"  Clef : signal={clef['signal']:.3f}  action={clef['action']:<6}  "
              f"latency={clef['latency_ms']:.0f}ms  cost=${clef['cost_usd']:.6f}")
        print(f"  Haiku: signal={haiku['signal']:.3f}  action={haiku['action']:<6}  "
              f"latency={haiku['latency_ms']:.0f}ms  cost=${haiku['cost_usd']:.6f}")
        print(f"  diff : |delta_signal|={abs(clef['signal'] - haiku['signal']):.3f}  "
              f"action_match={clef['action'] == haiku['action']}")

    total_clef_cost = sum(r["clef"]["cost_usd"] for r in rows)
    total_haiku_cost = sum(r["haiku"]["cost_usd"] for r in rows)
    avg_clef_latency = sum(r["clef"]["latency_ms"] for r in rows) / len(rows)
    avg_haiku_latency = sum(r["haiku"]["latency_ms"] for r in rows) / len(rows)
    agree_count = sum(1 for r in rows if r["clef"]["action"] == r["haiku"]["action"])

    print("\n=== SUMMARY ===")
    print(f"Action agreement: {agree_count}/{len(rows)}")
    print(f"Avg latency  - Clef: {avg_clef_latency:.0f}ms   Haiku: {avg_haiku_latency:.0f}ms")
    print(f"Total cost (6 calls) - Clef: ${total_clef_cost:.6f}   Haiku: ${total_haiku_cost:.6f}")


if __name__ == "__main__":
    main()
