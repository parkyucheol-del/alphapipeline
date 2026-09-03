"""
app/payment.py의 _bazaar_extension()을 실제 설치된 x402==2.21.0 패키지의
공식 declare_discovery_extension() 헬퍼를 올바르게 쓰도록 재작성하는 1회성 패치.

배경: 2026-09-03 밤 실배포 후 실제 결제 테스트에서 Bazaar가 여전히
"invalid discovery configuration"으로 거부하는 것을 확인. 사용자 PC에 실제로
설치된 x402 패키지 소스를 inspect.getsource()로 직접 열어본 결과, 공식
declare_discovery_extension()의 진짜 시그니처/반환 구조가 이전에 참고한 문서
사이트(docs.x402.org) 설명과 달랐다:
  - method 파라미터가 없음 (라우트 키에서 자동 추론됨, bazaar_resource_server_extension이 처리)
  - 반환값은 {"bazaar": {"info": {...}}} 한 겹이 아니라
    {"bazaar": {"info": {...}, "schema": {...}}} 두 겹 구조
기존 수동 fallback 코드는 이 구조를 몰라서 계속 잘못된 모양을 보내고 있었다.
이 스크립트는 그 fallback을 걷어내고 공식 헬퍼를 그대로 쓰도록 고친다.

실행: 레포 루트(app 폴더가 보이는 위치)에서 `python patch_payment.py`
적용 후 `git diff app/payment.py`로 변경사항을 꼭 눈으로 확인할 것.
"""
from __future__ import annotations

import sys
from pathlib import Path

PATH = Path("app/payment.py")

NEW_FUNCTION = '''def _bazaar_extension(
    *,
    input_example: dict,
    input_schema: dict,
    output_example=None,
    output_schema=None,
) -> dict:
    """x402 Bazaar 인덱서가 읽는 discovery extension을 만든다.

    2026-09-03 밤, 실제 설치된 x402==2.21.0 패키지 소스를 inspect.getsource()로
    직접 열어서 확인한 결과, 공식 헬퍼 declare_discovery_extension()의 진짜
    시그니처는 이전에 참고했던 문서 사이트(docs.x402.org) 설명과 달랐다:
      - method 파라미터 자체가 없다 (호출부의 라우트 키("GET /v1/...")에서
        bazaar_resource_server_extension이 런타임에 자동으로 채워 넣는다).
      - 반환값도 {"bazaar": {"info": {...}}} 한 겹이 아니라
        {"bazaar": {"info": {...}, "schema": {...}}} 두 겹 구조다.
    이전의 수동 fallback({"info": {"input": {"type": "http", "method": ...,
    "queryParams": ...}, "output": {...}}})은 이 실제 구조와 맞지 않아서 매
    결제마다 "invalid discovery configuration"으로 계속 거부되고 있었다.
    이제 공식 헬퍼를 있는 그대로 호출해서 이 문제를 해결한다 - 손으로 다시
    만들지 말 것, 위 버그가 재발한다.
    """
    if not _HAS_DISCOVERY_HELPER:
        logger.warning(
            "x402.extensions.bazaar.declare_discovery_extension을 쓸 수 없어 "
            "이 라우트는 Bazaar 디스커버리 메타데이터 없이 서빙됩니다. "
            "'pip install -U x402'로 SDK를 업그레이드하면 자동으로 복구됩니다."
        )
        return {}

    output = None
    if output_example is not None or output_schema is not None:
        output = OutputConfig(example=output_example, schema=output_schema)

    return declare_discovery_extension(
        input=input_example,
        input_schema=input_schema,
        output=output,
    )


'''


def main() -> None:
    if not PATH.exists():
        sys.exit(f"{PATH}를 찾을 수 없습니다 - 레포 루트에서 실행해주세요.")

    lines = PATH.read_text(encoding="utf-8").splitlines(keepends=True)

    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if line.startswith("def _bazaar_extension("):
            start_idx = i
        elif start_idx is not None and end_idx is None and i > start_idx and line.startswith("def "):
            end_idx = i
            break

    if start_idx is None or end_idx is None:
        sys.exit(
            "_bazaar_extension() 함수 경계를 찾지 못했습니다 - 파일이 예상과 달라진 것 같습니다. "
            "수동으로 확인이 필요합니다 (자동 수정 없이 종료함)."
        )

    removed = end_idx - start_idx
    new_lines = lines[:start_idx] + [NEW_FUNCTION] + lines[end_idx:]

    # 호출부의 method="GET", 인자는 이제 declare_discovery_extension이 안 받으므로 제거.
    before_count = sum(1 for ln in new_lines if ln.strip() == 'method="GET",')
    new_lines = [ln for ln in new_lines if ln.strip() != 'method="GET",']

    PATH.write_text("".join(new_lines), encoding="utf-8", newline="\n")

    print(f"완료: _bazaar_extension() 함수 {removed}줄을 새 구현으로 교체했습니다.")
    print(f"완료: 호출부의 method=\"GET\", 인자 {before_count}곳을 제거했습니다 (3곳이어야 정상).")
    print("이제 'git diff app/payment.py'로 변경사항을 확인해주세요.")


if __name__ == "__main__":
    main()
