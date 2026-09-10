#!/usr/bin/env node

const ENDPOINT = "https://alphapipeline-eu.onrender.com/v1/prediction/preview-slippage";
const TIMEOUT_MS = 8000; // Render is always-on (Starter plan), no cold-start to buffer for

async function fetchWithTimeout(url, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(url, { signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function main() {
  console.log("Checking live Polymarket exit-liquidity via AlphaPipeline...\n");

  let res;
  try {
    res = await fetchWithTimeout(ENDPOINT, TIMEOUT_MS);
  } catch (err) {
    if (err.name === "AbortError") {
      console.error(`Request timed out after ${TIMEOUT_MS / 1000}s. Try again in a moment.`);
    } else {
      console.error(`Network error: ${err.message}`);
    }
    process.exit(1);
  }

  if (res.status === 429) {
    console.error("Rate limited - too many checks from this network. Try again in a minute.");
    process.exit(1);
  }

  if (!res.ok) {
    console.error(`Preview temporarily unavailable (HTTP ${res.status}). Try again shortly.`);
    process.exit(1);
  }

  const data = await res.json();

  console.log(`Market:            ${data.market_slug ?? "N/A"} (${data.side ?? "?"})`);
  console.log(`Position size:     ${data.position_size_shares ?? "N/A"} shares`);
  console.log(`Executable:        ${data.executable}`);
  if (data.best_quote !== undefined) console.log(`Best quote:        $${Number(data.best_quote).toFixed(4)}`);
  if (data.avg_exit_price !== undefined && data.avg_exit_price !== null) {
    console.log(`Avg exit price:    $${Number(data.avg_exit_price).toFixed(4)}`);
  }
  if (data.price_impact_pct !== undefined && data.price_impact_pct !== null) {
    console.log(`Price impact:      ${data.price_impact_pct}%`);
  }
  console.log(`Data source:       ${data.data_source ?? "N/A"}`);
  console.log(`Latency:           ${data.latency_ms ?? "N/A"}ms`);

  console.log("\nWant this for any market/size, in your own bot?");
  console.log("-> https://github.com/parkyucheol-del/alphapipeline");
}

main();
