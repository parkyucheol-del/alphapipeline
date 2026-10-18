"""
데일리 알파 2026-10-13 액션카드: Hyperliquid mid/mark/oracle 괴리 10분 측정.
5초 간격 x 120회 = 10분. 결과는 화면 출력 + gap_log.txt로 저장.
읽기 전용, 키/지갑 불필요.
"""
import json
import time
import urllib.request

URL = "https://api.hyperliquid.xyz/info"


def ctx():
    req = urllib.request.Request(
        URL,
        data=b'{"type":"metaAndAssetCtxs"}',
        headers={"Content-Type": "application/json"},
    )
    meta, ctxs = json.load(urllib.request.urlopen(req))
    return {u["name"]: c for u, c in zip(meta["universe"], ctxs)}


def main():
    with open("gap_log.txt", "a", encoding="utf-8") as f:
        for _ in range(120):
            d = ctx()
            for s in ("ETH", "BTC"):
                c = d[s]
                mid, mark, orc = float(c["midPx"]), float(c["markPx"]), float(c["oraclePx"])
                gap = (mid - orc) / orc * 1e4
                flag = "  <-- ALERT" if abs(gap) > 5 else ""
                line = f"{time.strftime('%H:%M:%S')} {s} mid={mid} mark={mark} oracle={orc} mid-oracle={gap:+.1f}bp{flag}"
                print(line)
                f.write(line + "\n")
                f.flush()
            time.sleep(5)
    print("\n10분 측정 완료 - gap_log.txt 저장됨")


if __name__ == "__main__":
    main()
