"""One-off diagnostic: list Hyperliquid HIP-3 perp dexes and their coin
universes, so we can find the right dex name + exact coin naming for
NVDA/AAPL/META/GOOGL stock perps. Not part of the hourly collector - run
once, paste the output back, then delete this file.
"""
import asyncio
import json

import httpx

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"


async def main():
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(HYPERLIQUID_INFO_URL, json={"type": "perpDexs"})
        r.raise_for_status()
        dexs = r.json()
        print("=== perpDexs ===")
        print(json.dumps(dexs, indent=2))

        # dexs[0] is the default dex (name ""); skip it, we only want the
        # isolated HIP-3 dexes that might carry stock perps.
        for d in dexs:
            if not d:
                continue
            name = d.get("name")
            if not name:
                continue
            r2 = await client.post(
                HYPERLIQUID_INFO_URL, json={"type": "meta", "dex": name}
            )
            r2.raise_for_status()
            universe = [a.get("name") for a in r2.json().get("universe", [])]
            print(f"\n=== dex '{name}' universe ({len(universe)} coins) ===")
            print(universe)


if __name__ == "__main__":
    asyncio.run(main())
