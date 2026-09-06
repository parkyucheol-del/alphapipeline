"""
AlphaPipeline - main.py 루트("/") 응답의 price_per_call_usdc 가격표에서
dump-risk 가격을 실제 과금 상태와 일치시키는 패치.

## 왜 필요한가
외부 개발자 리뷰에서 지적된 "Bazaar 가격표(0.03)와 실제 무료 서빙이
어긋난다"는 문제의 일부 - Bazaar 카탈로그 자체는 손 댈 수 없지만(과거
결제로 이미 박제된 값이라 코드로 못 고침, 세션에서 이미 확인함), 우리
자신의 루트 엔드포인트가 보여주는 가격표는 우리가 통제할 수 있고, 지금
그것도 실제 상태와 어긋나 있다.

지금 코드는 dump-risk가 무료로 서빙 중이어도(DUMP_RISK_ENABLED=false)
루트 가격표에는 그냥 settings.PRICE_DUMP_RISK_USDC 값(예: 0.03)을
그대로 보여준다 - 실제로 결제 없이 무료로 데이터를 주면서 가격표에는
유료라고 써있는 셈.

## 어떻게 고쳤나
"이 필드는 무조건 0.0"으로 하드코딩하지 않았다 - 그러면 나중에
DUMP_RISK_ENABLED를 다시 true로 켰을 때(계획대로 유료 전환할 미래
시나리오) 이 값이 또 어긋나게 된다. 대신 app/payment.py의
build_routes(dump_risk_enabled=settings.DUMP_RISK_ENABLED)가 실제
과금 여부를 결정할 때 쓰는 것과 정확히 같은 플래그(settings.DUMP_RISK_ENABLED)를
여기서도 그대로 참조해서, 가격표가 항상 실제 과금 상태를 따라가게
만들었다 - "같은 소스오브트루스를 두 곳에서 각자 관리하다 어긋난다"는
이 프로젝트에서 이미 여러 번 겪은 버그 패턴을 근본적으로 막는 방식.

## 검증
- 원본 main.py에서 앵커 블록이 정확히 1번만 있는 것을 확인함.
- 패치 적용 후 python -m py_compile 통과 확인함.
- 로직만 추가됐을 뿐 다른 6개 엔드포인트 가격 표시는 전혀 안 건드림.
"""
import sys
from pathlib import Path

MAIN_PY = Path("main.py")

OLD = '''        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            "/v1/unlocks/dump-risk": settings.PRICE_DUMP_RISK_USDC,
            "/v1/security/token-risk": settings.PRICE_TOKEN_RISK_USDC,
            "/v1/derivatives/funding-rate": settings.PRICE_FUNDING_RATE_USDC,
            "/v1/dex/liquidity-slippage": settings.PRICE_DEX_SLIPPAGE_USDC,
            "/v1/calendar/macro-dday": settings.PRICE_MACRO_DDAY_USDC,
        },'''

NEW = '''        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            # DUMP_RISK_ENABLED가 실제 과금 여부를 결정하는 것과 동일한 플래그를
            # 그대로 참조한다 - PRICE_DUMP_RISK_USDC 값과 무관하게 이 필드가 항상
            # 실제 서빙 상태와 일치하도록 (app/payment.py의 build_routes 참고).
            "/v1/unlocks/dump-risk": (
                settings.PRICE_DUMP_RISK_USDC if settings.DUMP_RISK_ENABLED else 0.0
            ),
            "/v1/security/token-risk": settings.PRICE_TOKEN_RISK_USDC,
            "/v1/derivatives/funding-rate": settings.PRICE_FUNDING_RATE_USDC,
            "/v1/dex/liquidity-slippage": settings.PRICE_DEX_SLIPPAGE_USDC,
            "/v1/calendar/macro-dday": settings.PRICE_MACRO_DDAY_USDC,
        },'''


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def main() -> None:
    if not MAIN_PY.exists():
        fail(f"{MAIN_PY}가 없습니다 - 리포 루트에서 실행해주세요.")

    src = MAIN_PY.read_text(encoding="utf-8")

    if "if settings.DUMP_RISK_ENABLED else 0.0" in src:
        fail("이미 이 패치가 적용된 것 같습니다 - 중복 적용 방지.")

    count = src.count(OLD)
    if count != 1:
        fail(f"예상한 앵커 텍스트를 1번이 아니라 {count}번 찾았습니다 - 파일이 예상과 다른 상태인 것 같습니다.")
    new_src = src.replace(OLD, NEW, 1)

    try:
        compile(new_src, str(MAIN_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {MAIN_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    MAIN_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {MAIN_PY} 루트 응답의 dump-risk 가격이 DUMP_RISK_ENABLED 상태를 따라가도록 고쳤습니다.")
    print("다음 단계:")
    print("  1) python -m py_compile main.py")
    print('  2) git add -A && git commit -m "Sync root price list dump-risk price with DUMP_RISK_ENABLED" && git push')
    print("  3) 배포 확인되면 GET https://alphapipeline-eu.onrender.com/ 호출해서")
    print("     price_per_call_usdc.\"/v1/unlocks/dump-risk\"가 0.0으로 나오는지 확인")


if __name__ == "__main__":
    main()
