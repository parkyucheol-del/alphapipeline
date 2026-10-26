"""
AlphaPipeline의 모든 REST 엔드포인트를 한 번씩 순서대로 호출해보는 스모크 테스트
스크립트 (x402 공식 SDK 사용, examples/client_example.py와 동일한 결제 방식).

목적: 엔드포인트를 새로 추가/수정한 뒤 "다 한 번씩 불러서 살아있는지" 빠르게
확인하는 용도. 각 호출은 실제 x402 결제가 나가는 라이브 호출이므로(총
합산 약 $0.31~0.36 수준(2026-10-18 저가 티어 3개+hip4-mispricing+order-split-guide+Phase 3 3종+
polymarket-fee-estimate($0.01)+polymarket-mispricing($0.03) 추가로 소폭 증가) -
무료 엔드포인트 3개 제외하고 나머지 단가 합),
실행 전에 그 점을 인지할 것.

사용법:
  pip install "x402[https]" eth_account httpx   (이미 설치돼 있으면 생략)
  EVM_PRIVATE_KEY 또는 EVM_MNEMONIC 환경변수 설정 (.env에 이미 있으면 자동 로드 안 됨 -
    별도 쉘 환경변수로 export/set 하거나, 이 리포에 python-dotenv가 깔려있다면
    아래 load_dotenv() 호출이 .env를 읽음)
  python examples/call_all_endpoints.py

옵션:
  --include-hip4-subscribe  hip4-alerts/subscribe(웹훅 구독)까지 포함해서 호출.
    기본적으로는 제외함 - HIP4_EVENTS_ENABLED=false가 기본값이라 보통 503이
    뜨고 끝나지만, 혹시 나중에 켜진 상태에서 이 스크립트를 돌리면 더미
    webhook_url로 실제 구독이 하나 생성되는 부작용이 있어 기본 제외로 둠.
  --base-url URL  기본값은 ALPHAPIPELINE_API_BASE 환경변수 또는 라이브 서버.

종료 코드 (2026-10-08 추가, GitHub Actions 월간 자동 실행용): KNOWN_EXPECTED_FAILURES에
없는 엔드포인트가 하나라도 실패하면 exit code 1을 반환한다. 이전까지는 이 스크립트가
무엇이 나오든 항상 0으로 끝나서, 결제 자체가 전부 막히는 등 실제로 문제가 생겨도
GitHub Actions에서는 "성공"으로만 보였다 - 사람이 매번 로그를 직접 열어봐야만 알 수
있던 걸, 완전 무인 모드로 돌리려면 실패를 실제로 실패로 신호해야 해서 추가함.
(2026-10-09 수정) EVM_PRIVATE_KEY/EVM_MNEMONIC이 없는 payment-free 모드에서는 유료
엔드포인트의 402 응답을 실패로 치지 않는다 - 결제가 안 나갈 걸 알고 돌리는 모드라서
402가 오히려 "정상"이다. monthly-smoke-test.yml처럼 지갑 시크릿 없이 매달 돌아가는
무인 실행이 이 구분 없이는 매번 "실패"로 뜨는 버그가 있었음 (_print_summary 참고).

hip4-alerts/unsubscribe(POST)는 유효한 subscription_id가 없으면 의미가 없어서
이 스크립트에서 아예 제외함 - 구독 생성/해제 흐름을 테스트하고 싶으면
examples/subscribe_hip4_alerts.py를 따로 쓸 것.

주의: neg-risk-arbitrage/exit-capacity-audit에 쓰는 event_slug/market_slug
기본값은 과거 세션에서 확인했던 실제 라이브 이벤트 슬러그다. 폴리마켓 이벤트는
만료/교체되므로, 이 스크립트를 한참 뒤에 돌렸는데 이 두 엔드포인트만 404/빈
응답이 나오면 슬러그가 오래돼서 그런 것일 가능성이 높다 - 아래
DEFAULT_PARAMS에서 event_slug/market_slug만 최신 슬러그로 바꿔주면 된다.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from eth_account import Account

from x402 import AbortResult, PaymentCreationContext, x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

MNEMONIC_HD_PATH = "m/44'/60'/0'/0/0"

# Base 메인넷(chain 8453) WETH - 보안/슬리피지 엔드포인트 테스트용으로 이미
# 도그푸딩 봇에서 반복 검증된 주소 (실제 유동성 있는 실존 토큰).
_WETH_BASE = "0x4200000000000000000000000000000000000006"
_WETH_USDC_POOL_BASE = "0xd0b53d9277642d899df5c87a3966a349a798f224"
# AlphaPipeline 결제 수신 지갑 - whale-position-audit 테스트용 임의 주소로 재사용
# (실제 Hyperliquid 포지션은 없을 가능성이 높지만, 유효한 주소 형식으로 라우트
# 자체가 정상 응답하는지는 확인 가능).
_SAMPLE_EVM_ADDRESS = "0x2bcfA4F7E1fA5E96934846A7dEa623398Ed7f752"

# 2026-10-25: 자가 결제 크론에 "기대 가격" 가드 추가 (데일리 알파 2026-10-25-6 대응,
# x402 SDK 지출한도 결함 - V12 보고, Issue #3866 - 후속조치). 어제(10/24) 추가한
# max_amount_per_payment=$0.05 상한은 "한 번에 얼마까지"만 막고, "이 엔드포인트가
# 평소보다 훨씬 비싸졌는지"는 못 걸러낸다 (예: 평소 $0.01짜리를 $0.04로 올려 불러도
# $0.05 상한 밑이라 그냥 통과함). 엔드포인트별 가격표를 코드에 하드코딩하는 대신,
# 과거에 실제로 지불했던 "최저 관측가"를 로컬 파일에 기록해두고, 그보다
# PRICE_JUMP_RATIO배 이상 + 절대로도 유의미하게 비싸게 부르면 결제 전에 막는다.
# 하드코딩 표는 정상적인 가격 변경 때마다 수동으로 맞춰줘야 해서 금방 낡는데
# (2026-10-25-2 빌드카드에서 서버 쪽 /pricing.json에도 지적된 것과 같은 문제),
# 이 방식은 정상 가격 변경에는 자동으로 적응하면서 "갑자기 수십~수백 배" 같은
# 비정상 과금만 잡아낸다. (V12 분석: 10/12~10/23 베이스 체인 x402 결제 중 25건이
# 정상가의 100배 이상 + $1 이상 과금됨, 사례 중 하나는 $0.01짜리가 $588로 청구됨.)
PRICE_BASELINE_FILE = Path(__file__).parent / ".price_baseline.json"
PRICE_JUMP_RATIO = 5.0  # 과거 최저 관측가의 5배 넘게 부르면 의심
PRICE_JUMP_MIN_ABS_USDC = 0.02  # 절대 차이가 $0.02 미만이면 반올림 차이 정도로 보고 무시


def _load_price_baseline() -> dict[str, int]:
    try:
        return json.loads(PRICE_BASELINE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_price_baseline(baseline: dict[str, int]) -> None:
    try:
        PRICE_BASELINE_FILE.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    except OSError as exc:
        print(f"[가격 가드] 베이스라인 저장 실패(무시하고 진행): {exc}")


def make_expected_price_guard():
    """결제 직전에 '이 엔드포인트가 과거 관측가 대비 비정상적으로 비싸졌는지'를
    검사하는 on_before_payment_creation 훅을 만든다. 통과시킨 가격은 즉시
    베이스라인에 반영해서(최저가만 저장) 다음 호출/실행에도 쓴다.

    훅 컨텍스트(PaymentCreationContext)에는 호출 URL이 없어서, 네트워크/자산/
    수취 지갑 조합을 엔드포인트 식별자 대신 쓴다 - AlphaPipeline은 엔드포인트마다
    이 조합이 거의 고정이라 실질적으로 충분하다.
    """
    baseline = _load_price_baseline()

    def hook(context: "PaymentCreationContext"):
        req = context.selected_requirements
        key = f"{req.network}:{req.asset}:{req.pay_to}".lower()
        try:
            amount = int(req.get_amount())
        except (TypeError, ValueError, AttributeError):
            return None  # amount를 못 읽으면 이 가드는 건너뛴다 (max_amount_per_payment가 여전히 상한 역할)

        prior = baseline.get(key)
        if prior is not None and prior > 0:
            jump_abs_usdc = (amount - prior) / 1_000_000  # USDC는 6 decimals
            if amount > prior * PRICE_JUMP_RATIO and jump_abs_usdc > PRICE_JUMP_MIN_ABS_USDC:
                return AbortResult(
                    reason="unexpected_price_jump",
                    message=(
                        f"{key} 가격이 과거 최저 관측가 {prior}(atomic) 대비 "
                        f"{amount}(atomic)로 {amount / prior:.1f}배 뛰었습니다 - "
                        "결제를 막습니다. 서버 설정 오류이거나 공격일 수 있습니다."
                    ),
                )
            if amount < prior:
                baseline[key] = amount
                _save_price_baseline(baseline)
        else:
            baseline[key] = amount
            _save_price_baseline(baseline)
        return None

    return hook


@dataclass
class EndpointCall:
    name: str
    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    note: str = ""


ENDPOINTS: list[EndpointCall] = [
    # --- 무료/온보딩 엔드포인트 ---
    EndpointCall("unlocks.dump_risk", "GET", "/v1/unlocks/dump-risk"),
    EndpointCall("market.kimchi_alert", "GET", "/v1/market/kimchi-alert", {"symbol": "BTC"}),
    EndpointCall("calendar.macro_dday", "GET", "/v1/calendar/macro-dday"),
    EndpointCall("prediction.preview_slippage", "GET", "/v1/prediction/preview-slippage"),
    EndpointCall(
        "prediction.hip4_alerts_event_types", "GET", "/v1/prediction/hip4-alerts/event-types"
    ),
    # --- 보안 계열 ($0.02~$0.03) ---
    EndpointCall(
        "security.token_risk",
        "GET",
        "/v1/security/token-risk",
        {"chain_id": 8453, "contract_address": _WETH_BASE},
    ),
    EndpointCall(
        "security.contract_health_audit",
        "GET",
        "/v1/security/contract-health-audit",
        {"chain_id": 8453, "contract_address": _WETH_BASE},
    ),
    EndpointCall(
        "security.token_diagnostic",
        "GET",
        "/v1/security/token-diagnostic",
        {"chain_id": 8453, "contract_address": _WETH_BASE},
    ),
    # --- DEX/차익거래 ---
    EndpointCall(
        "dex.liquidity_slippage",
        "GET",
        "/v1/dex/liquidity-slippage",
        {
            "trade_size_usd": 100,
            "network": "base",
            "pool_address": _WETH_USDC_POOL_BASE,
            "token_address": _WETH_BASE,
        },
    ),
    EndpointCall(
        "arb.spread_matrix",
        "GET",
        "/v1/arb/spread-matrix",
        {
            "symbol": "BTC",
            "network": "base",
            "trade_size_usd": 1000,
            "pool_address": _WETH_USDC_POOL_BASE,
            "token_address": _WETH_BASE,
        },
    ),
    # --- 파생상품 ---
    EndpointCall(
        "derivatives.whale_position_audit",
        "GET",
        "/v1/derivatives/whale-position-audit",
        {"address": _SAMPLE_EVM_ADDRESS},
        note="실제 Hyperliquid 포지션이 없는 주소라 빈 결과가 정상일 수 있음",
    ),
    EndpointCall(
        "derivatives.funding_rate", "GET", "/v1/derivatives/funding-rate", {"symbol": "BTC"}
    ),
    EndpointCall(
        "derivatives.funding_rate_value",
        "GET",
        "/v1/derivatives/funding-rate-value",
        {"symbol": "BTC"},
    ),
    EndpointCall(
        "derivatives.funding_apr_matrix",
        "GET",
        "/v1/derivatives/funding-apr-matrix",
        {"symbol": "BTC"},
    ),
    EndpointCall(
        "market.oracle_gap", "GET", "/v1/market/oracle-gap", {"coin": "ETH"}
    ),
    EndpointCall(
        "market.oracle_gap_bp", "GET", "/v1/market/oracle-gap-bp", {"coin": "ETH"}
    ),
    # --- 예측시장 (폴리마켓) - event_slug/market_slug는 만료될 수 있음, 상단 docstring 참고 ---
    EndpointCall(
        "prediction.neg_risk_arbitrage",
        "GET",
        "/v1/prediction/neg-risk-arbitrage",
        {"event_slug": "how-many-fed-rate-cuts-in-2026"},
        note="event_slug가 오래됐으면 404/빈 응답 가능 - 최신 슬러그로 교체 필요",
    ),
    EndpointCall(
        "prediction.exit_capacity_audit",
        "GET",
        "/v1/prediction/exit-capacity-audit",
        {
            "position_size_shares": 100,
            "market_slug": "how-many-fed-rate-cuts-in-2026",
            "outcome": "yes",
            "side": "sell",
        },
        note="market_slug가 오래됐으면 404/빈 응답 가능 - 최신 슬러그로 교체 필요",
    ),
    EndpointCall(
        "prediction.order_split_guide",
        "GET",
        "/v1/prediction/order-split-guide",
        {
            "position_size_shares": 500,
            "market_slug": "how-many-fed-rate-cuts-in-2026",
            "outcome": "yes",
            "side": "sell",
            "max_price_impact_pct_per_slice": 1.0,
        },
        note="market_slug가 오래됐으면 404/빈 응답 가능 - 최신 슬러그로 교체 필요 (exit_capacity_audit와 동일 슬러그 재사용)",
    ),
    EndpointCall(
        "prediction.bid_ask_spread_pct",
        "GET",
        "/v1/prediction/bid-ask-spread-pct",
        {"market_slug": "how-many-fed-rate-cuts-in-2026", "outcome": "yes"},
        note="market_slug가 오래됐으면 404/빈 응답 가능 - 최신 슬러그로 교체 필요",
    ),
    EndpointCall(
        "prediction.orderbook_imbalance_ratio",
        "GET",
        "/v1/prediction/orderbook-imbalance-ratio",
        {"market_slug": "how-many-fed-rate-cuts-in-2026", "outcome": "yes", "depth_band_pct": 5.0},
        note="market_slug가 오래됐으면 404/빈 응답 가능 - 최신 슬러그로 교체 필요",
    ),
    EndpointCall(
        "prediction.liquidity_depth_after_haircut",
        "GET",
        "/v1/prediction/liquidity-depth-after-haircut",
        {
            "market_slug": "how-many-fed-rate-cuts-in-2026",
            "outcome": "yes",
            "side": "sell",
            "haircut_pct": 10.0,
            "max_price_impact_pct": 5.0,
        },
        note="market_slug가 오래됐으면 404/빈 응답 가능 - 최신 슬러그로 교체 필요 (exit_capacity_audit와 동일 슬러그 재사용)",
    ),
    EndpointCall(
        "prediction.polymarket_fee_estimate",
        "GET",
        "/v1/prediction/polymarket-fee-estimate",
        {
            "shares": 100,
            "market_slug": "how-many-fed-rate-cuts-in-2026",
            "outcome": "yes",
            "side": "sell",
        },
        note="market_slug가 오래됐으면 404/빈 응답 가능 - 최신 슬러그로 교체 필요 (exit_capacity_audit와 동일 슬러그 재사용)",
    ),
    EndpointCall(
        "prediction.polymarket_mispricing",
        "GET",
        "/v1/prediction/polymarket-mispricing",
        {
            "strikes": [100000, 110000],
            "market_slugs": [
                "how-many-fed-rate-cuts-in-2026",
                "how-many-fed-rate-cuts-in-2026",
            ],
        },
        note=(
            "스모크 테스트용 - 같은 슬러그를 두 rung에 재사용해 '동일 정산조건'을 "
            "흉내만 낸 것으로 실제 차익거래 신호로 해석하면 안 됨. market_slug가 "
            "오래됐으면 404/빈 응답 가능 - 최신 슬러그로 교체 필요."
        ),
    ),
    # --- 예측시장 (Hyperliquid HIP-4) ---
    EndpointCall(
        "prediction.hip4_snapshot", "GET", "/v1/prediction/hip4-snapshot", {"limit": 20}
    ),
    EndpointCall(
        "prediction.hip4_price_ladder",
        "GET",
        "/v1/prediction/hip4-price-ladder",
        {"underlying": "BTC"},
    ),
    EndpointCall(
        "prediction.hip4_price",
        "GET",
        "/v1/prediction/hip4-price",
        {"underlying": "BTC", "strike": 150000},
    ),
    EndpointCall(
        "prediction.hip4_mispricing",
        "GET",
        "/v1/prediction/hip4-mispricing",
        {"underlying": "BTC"},
    ),
    # --- 도구 ---
    EndpointCall(
        "tools.ai_markdown", "GET", "/v1/tools/ai-markdown", {"url": "https://example.com"}
    ),
]

# 매번 재발이 예상되는, 서버 버그가 아닌 기대 실패 케이스만 여기 올린다. 새로운 실패가
# 여기 없는 이름으로 뜨면 그게 진짜 조사해야 할 회귀(regression)다.
# - prediction.exit_capacity_audit: 테스트 스크립트의 event_slug/market_slug 기본값이
#   같은 문자열을 재사용해서 생기는 레벨 불일치 - 서버 버그 아님
#   (claude/session-log-2026-10-01-call-all-endpoints-smoke-test.md 참고).
# - prediction.order_split_guide, prediction.bid_ask_spread_pct,
#   prediction.orderbook_imbalance_ratio, prediction.liquidity_depth_after_haircut,
#   prediction.polymarket_fee_estimate, prediction.polymarket_mispricing:
#   exit_capacity_audit와 동일한 market_slug(실제로는 event 레벨 slug)를
#   재사용하므로 같은 이유로 실패 가능 - 서버 버그 아님.
KNOWN_EXPECTED_FAILURES: set[str] = {
    "prediction.exit_capacity_audit",
    "prediction.order_split_guide",
    "prediction.bid_ask_spread_pct",
    "prediction.orderbook_imbalance_ratio",
    "prediction.liquidity_depth_after_haircut",
    "prediction.polymarket_fee_estimate",
    "prediction.polymarket_mispricing",
}

HIP4_SUBSCRIBE_ENDPOINT = EndpointCall(
    "prediction.hip4_alerts_subscribe",
    "GET",
    "/v1/prediction/hip4-alerts/subscribe",
    {
        "webhook_url": "https://example.com/alphapipeline-smoke-test-webhook",
        "secret": "smoke-test-placeholder-secret",
        "underlying": "BTC",
    },
    note="HIP4_EVENTS_ENABLED=false가 기본값이면 503이 정상 - --include-hip4-subscribe로만 호출됨",
)


def _load_account() -> Account | None:
    """EVM_PRIVATE_KEY 또는 EVM_MNEMONIC 중 있는 걸로 계정을 만든다. 둘 다 없으면 None."""
    private_key = os.getenv("EVM_PRIVATE_KEY")
    if private_key:
        return Account.from_key(private_key)

    mnemonic = os.getenv("EVM_MNEMONIC")
    if mnemonic:
        normalized_mnemonic = mnemonic.strip().lower()
        Account.enable_unaudited_hdwallet_features()
        return Account.from_mnemonic(normalized_mnemonic, account_path=MNEMONIC_HD_PATH)

    return None


def _build_url(base_url: str, call: EndpointCall) -> str:
    url = f"{base_url}{call.path}"
    if call.params:
        # doseq=True: list 값(예: prediction.polymarket_mispricing의 strikes/
        # market_slugs)을 반복 쿼리 파라미터(?strikes=1&strikes=2)로 풀어준다 -
        # 기본값(False)이면 리스트를 str()해서 한 덩어리로 인코딩해버려 FastAPI의
        # list[float]/list[str] Query 파싱이 깨진다(2026-10-18).
        url = f"{url}?{urlencode(call.params, doseq=True)}"
    return url


async def _call_one(http, call: EndpointCall) -> dict[str, Any]:
    url_or_path = call.path + (f"?{urlencode(call.params, doseq=True)}" if call.params else "")
    started = time.monotonic()
    try:
        if call.method == "GET":
            response = await http.get(url_or_path)
        else:
            response = await http.post(url_or_path, json=call.json_body)
        await response.aread()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "name": call.name,
            "status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "body_preview": response.text[:200],
            "note": call.note,
            "error": None,
        }
    except Exception as exc:  # 한 엔드포인트 실패가 전체 루프를 막으면 안 됨
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "name": call.name,
            "status": None,
            "elapsed_ms": elapsed_ms,
            "body_preview": "",
            "note": call.note,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def run(base_url: str, include_hip4_subscribe: bool) -> list[dict[str, Any]]:
    account = _load_account()
    endpoints = list(ENDPOINTS)
    if include_hip4_subscribe:
        endpoints.append(HIP4_SUBSCRIBE_ENDPOINT)
    else:
        print(
            "(참고: hip4-alerts/subscribe는 기본 제외됨 - "
            "포함하려면 --include-hip4-subscribe)\n"
        )

    results: list[dict[str, Any]] = []

    if account is None:
        print(
            "EVM_PRIVATE_KEY 또는 EVM_MNEMONIC이 설정되지 않았습니다. "
            "결제 없이 각 엔드포인트의 402 응답만 확인합니다."
        )
        import httpx

        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as http:
            for call in endpoints:
                result = await _call_one(http, call)
                results.append(result)
                _print_one(result)
    else:
        print(f"사용 지갑 주소: {account.address}\n")
        client = x402Client()
        # 2026-10-24: x402 SDK 지출한도 결함(V12 보고, Issue #3866) 대응 - 패키지
        # 기본값에 기대지 않고 결제당 상한을 코드에 명시. AlphaPipeline 엔드포인트
        # 중 가장 비싼 게 $0.03이라 $0.05면 정상 호출은 전부 통과하고 비정상
        # 과금만 막는다.
        client.set_spend_controls({"max_amount_per_payment": "$0.05"})
        # 2026-10-25: 위 상한과 별개로, 엔드포인트별 "기대 가격"(과거 최저 관측가)
        # 대비 비정상적인 가격 상승을 막는 훅. 자세한 설명은 파일 상단
        # make_expected_price_guard() 주석 참고.
        client.on_before_payment_creation(make_expected_price_guard())
        register_exact_evm_client(client, EthAccountSigner(account))
        http_client = x402HTTPClient(client)

        async with x402HttpxClient(client, base_url=base_url, timeout=30.0) as http:
            for call in endpoints:
                result = await _call_one(http, call)
                results.append(result)
                _print_one(result)

    return _print_summary(results, payment_free=(account is None))


def _print_one(result: dict[str, Any]) -> None:
    status = result["status"] if result["status"] is not None else "ERROR"
    print(f"[{status}] {result['name']} ({result['elapsed_ms']}ms)")
    if result["error"]:
        print(f"    예외: {result['error']}")
    elif result["body_preview"]:
        print(f"    응답: {result['body_preview']}")
    if result["note"]:
        print(f"    참고: {result['note']}")
    print()


def _print_summary(results: list[dict[str, Any]], payment_free: bool) -> list[dict[str, Any]]:
    print("=" * 60)
    print("요약")
    print("=" * 60)

    def _is_ok(r: dict[str, Any]) -> bool:
        if r["status"] is None:
            return False
        if 200 <= r["status"] < 300:
            return True
        # payment_free 모드(지갑 키 없음)에서는 유료 엔드포인트가 402를 돌려주는
        # 것 자체가 "정상 동작 확인"이다 - 결제가 안 나갈 걸 알고 돌리는 모드라서,
        # 402는 실패가 아니라 엔드포인트/가격 응답이 살아있다는 신호다. 2026-10-09:
        # 이 구분이 없어서 지갑 키 없는 월간 스모크 테스트(monthly-smoke-test.yml)가
        # 매번 "예상 못한 실패 13건"으로 exit 1 나는 버그가 있었다 - 실제로는
        # 아무것도 안 깨진 정상 상태였다.
        if payment_free and r["status"] == 402:
            return True
        return False

    ok = [r for r in results if _is_ok(r)]
    failed = [r for r in results if r not in ok]
    print(f"성공: {len(ok)}/{len(results)}")
    if failed:
        print("실패/주의 필요:")
        for r in failed:
            status = r["status"] if r["status"] is not None else "예외"
            expected = " (기대된 실패 - KNOWN_EXPECTED_FAILURES)" if r["name"] in KNOWN_EXPECTED_FAILURES else ""
            print(f"  - {r['name']}: {status}{expected}")

    unexpected = [r for r in failed if r["name"] not in KNOWN_EXPECTED_FAILURES]
    if unexpected:
        print(f"\n⚠️  예상 못한 실패 {len(unexpected)}건 - exit code 1로 종료합니다.")
    return unexpected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("ALPHAPIPELINE_API_BASE", "https://alphapipeline-eu.onrender.com"),
    )
    parser.add_argument(
        "--include-hip4-subscribe",
        action="store_true",
        help="hip4-alerts/subscribe도 포함해서 호출 (기본 제외, 위 docstring 참고)",
    )
    args = parser.parse_args()
    unexpected_failures = asyncio.run(run(args.base_url, args.include_hip4_subscribe))
    if unexpected_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
