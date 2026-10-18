"""
market.oracle_gap 로직 단위 테스트 (2026-10-13 추가).

app.data_sources.get_hyperliquid_meta_and_asset_ctxs를 실제 Hyperliquid
metaAndAssetCtxs 응답 형태의 가짜 데이터로 monkey-patch해서, 외부 API 호출 없이
app.logic.get_oracle_gap의 bp 계산/elevated 임계값 로직만 검증한다.

실행: alphapipeline 프로젝트 루트에서
    python test_oracle_gap.py
(requirements.txt가 설치된 가상환경에서 실행할 것 - httpx/pydantic 등 필요)
"""
import asyncio
import sys

import app.data_sources as ds
from app.logic import get_oracle_gap

FAKE_UNIVERSE = [{"name": "BTC"}, {"name": "ETH"}, {"name": "SOL"}]

# 시나리오별 (midPx, markPx, oraclePx) - 실제 Hyperliquid 응답 필드명 그대로.
SCENARIOS = {
    "normal_eth": {"midPx": "2500.0", "markPx": "2500.1", "oraclePx": "2500.0"},
    # Papertrade류: mid가 oracle/mark에서 크게 벌어짐 (기본 임계값 5bp 초과)
    "elevated_eth": {"midPx": "2503.4", "markPx": "2500.1", "oraclePx": "2500.0"},
    # 임계값 바로 아래 (4.9bp) - elevated=False여야 함
    "just_under_threshold": {"midPx": "2501.225", "markPx": "2500.1", "oraclePx": "2500.0"},
}

_passed = 0
_failed = 0


def _check(label: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label} {detail}")


def _patch_ds(eth_ctx: dict):
    """Monkey-patch ds.get_hyperliquid_meta_and_asset_ctxs; returns the restore callback."""
    fake_ctxs = {"BTC": {"midPx": "60000", "markPx": "60000", "oraclePx": "60000"},
                 "ETH": eth_ctx,
                 "SOL": {"midPx": "100", "markPx": "100", "oraclePx": "100"}}

    async def _fake_meta_and_asset_ctxs():
        ordered_ctxs = [fake_ctxs[u["name"]] for u in FAKE_UNIVERSE]
        return {"universe": FAKE_UNIVERSE}, ordered_ctxs

    original = ds.get_hyperliquid_meta_and_asset_ctxs
    ds.get_hyperliquid_meta_and_asset_ctxs = _fake_meta_and_asset_ctxs
    return original


async def _run_scenario(name: str, ctx: dict) -> dict:
    original = _patch_ds(ctx)
    try:
        return await get_oracle_gap("ETH")
    finally:
        ds.get_hyperliquid_meta_and_asset_ctxs = original


async def main() -> None:
    print("1) 정상 케이스 (mid == oracle) -> gap ~0bp, elevated=False")
    r = await _run_scenario("normal_eth", SCENARIOS["normal_eth"])
    _check("mid_oracle_gap_bp ~= 0", abs(r["mid_oracle_gap_bp"]) < 0.01, r)
    _check("elevated == False", r["elevated"] is False, r)

    print("\n2) Papertrade류 케이스 (mid가 oracle 대비 +13.6bp) -> elevated=True")
    r = await _run_scenario("elevated_eth", SCENARIOS["elevated_eth"])
    _check("mid_oracle_gap_bp ~= 13.6", abs(r["mid_oracle_gap_bp"] - 13.6) < 0.1, r)
    _check("elevated == True", r["elevated"] is True, r)

    print("\n3) 임계값(5bp) 바로 아래 (4.9bp) -> elevated=False")
    r = await _run_scenario("just_under_threshold", SCENARIOS["just_under_threshold"])
    _check("mid_oracle_gap_bp ~= 4.9", abs(r["mid_oracle_gap_bp"] - 4.9) < 0.1, r)
    _check("elevated == False", r["elevated"] is False, r)

    print("\n4) 유니버스에 없는 코인 -> ValueError (main.py에서 400으로 변환)")
    original = _patch_ds(SCENARIOS["normal_eth"])
    try:
        try:
            await get_oracle_gap("NOTACOIN")
            _check("raises ValueError for unknown coin", False)
        except ValueError:
            _check("raises ValueError for unknown coin", True)
    finally:
        ds.get_hyperliquid_meta_and_asset_ctxs = original

    print(f"\n{_passed} passed, {_failed} failed")
    if _failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
