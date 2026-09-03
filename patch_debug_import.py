"""
왜 Render에서만 x402.extensions.bazaar 임포트가 실패하는지(로컬에선 똑같은
x402==2.21.0인데 성공) 실제 예외 메시지를 로그에 찍어서 확인하기 위한
1회성 진단 패치.

실행: 레포 루트에서 `python patch_debug_import.py`
적용 후 `git diff app/payment.py`로 확인.
"""
import sys
from pathlib import Path

PATH = Path("app/payment.py")

OLD = (
    'except ImportError:\n'
    '    OutputConfig = None\n'
    '    declare_discovery_extension = None\n'
    '    _HAS_DISCOVERY_HELPER = False\n'
    '    logger.warning(\n'
)
NEW = (
    'except ImportError as e:\n'
    '    OutputConfig = None\n'
    '    declare_discovery_extension = None\n'
    '    _HAS_DISCOVERY_HELPER = False\n'
    '    logger.warning("bazaar 헬퍼 임포트 실패 - 실제 원인: %r", e)\n'
    '    logger.warning(\n'
)


def main() -> None:
    if not PATH.exists():
        sys.exit(f"{PATH}를 찾을 수 없습니다 - 레포 루트에서 실행해주세요.")

    text = PATH.read_text(encoding="utf-8")
    count = text.count(OLD)

    if count == 0:
        sys.exit(
            "예상한 except ImportError 블록을 찾지 못했습니다 - 파일이 예상과 달라진 것 같습니다. "
            "수동 확인이 필요합니다 (자동 수정 없이 종료함)."
        )
    if count > 1:
        sys.exit(
            f"예상한 패턴이 {count}곳에서 발견되어 어디를 고쳐야 할지 애매합니다. "
            "수동 확인이 필요합니다 (자동 수정 없이 종료함)."
        )

    new_text = text.replace(OLD, NEW, 1)
    PATH.write_text(new_text, encoding="utf-8", newline="\n")
    print("완료: except ImportError 블록에 실제 예외 메시지 로깅을 추가했습니다.")
    print("이제 'git diff app/payment.py'로 변경사항을 확인해주세요.")


if __name__ == "__main__":
    main()
