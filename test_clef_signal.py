import asyncio
import sys

sys.path.insert(0, ".")
from app.data_sources import get_clef_flash_signal


async def main():
    signal = await get_clef_flash_signal(
        {
            "market": "BTC above 100k",
            "probability_from": 0.31,
            "probability_to": 0.44,
            "probability_delta": 0.13,
        },
        "Does this probability swing look like a real signal rather than noise?",
    )
    print("signal:", signal)


asyncio.run(main())
