"""
pydantic model_json_schema()가 만든 $defs/$ref 구조가 x402 Bazaar 확장 스키마에
병합되는 과정에서 깨지는 문제(PointerToNowhere: '/$defs/...' does not exist)를
고치는 패치. output_schema를 넘기기 전에 $ref를 전부 실제 값으로 풀어서(inline)
$defs 없는 자기 완결적 스키마로 만든다.

배경: 2026-09-03 밤, 실제 결제 테스트(kimchi-alert)가 Bazaar에서 계속
"invalid discovery configuration"으로 거부되는 진짜 원인을 x402==2.21.0
패키지 소스(_create_query_discovery_extension)를 직접 열어봐서 확인함 -
output.schema를 그대로 output_schema["properties"]["example"]에 병합하는데,
이러면 $defs가 스키마 루트가 아닌 깊은 위치로 들어가버려서 $ref가 깨짐.

실행: 레포 루트에서 `python patch_inline_schema_defs.py`
적용 후 `git diff app/payment.py`로 확인.
"""
import sys
from pathlib import Path

PATH = Path("app/payment.py")

HELPER = '''def _inline_schema_defs(schema: dict) -> dict:
    """pydantic model_json_schema()가 만든 $defs/$ref를 전부 실제 값으로 풀어서
    (inline) $defs 없는 자기 완결적 스키마로 만든다.

    x402==2.21.0의 declare_discovery_extension() 내부(_create_query_discovery_extension)는
    output.schema를 output_schema["properties"]["example"] 위치에 그대로 병합하는데,
    이러면 pydantic이 스키마 최상위에 넣어둔 "$defs"가 더 이상 최상위가 아니게 되어
    "#/$defs/..." 형태의 $ref가 깨진다("PointerToNowhere" 에러). 실제 결제 테스트에서
    Bazaar가 계속 "invalid discovery configuration"으로 거부한 진짜 원인이 이것으로
    확인됐다(2026-09-03 밤). $ref를 미리 다 풀어버리면 이 문제를 근본적으로 피할 수 있다.
    """
    defs = schema.get("$defs", {})
    if not defs:
        return schema

    def _resolve(node, _seen=frozenset()):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                key = ref.split("/")[-1]
                if key in _seen:
                    return {}
                target = defs.get(key, {})
                merged = {k: v for k, v in node.items() if k != "$ref"}
                return _resolve({**target, **merged}, _seen | {key})
            return {k: _resolve(v, _seen) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [_resolve(item, _seen) for item in node]
        return node

    return _resolve(schema)


'''

CALL_SITES = [
    "output_schema=KimchiAlertResponse.model_json_schema(),",
    "output_schema=MarkdownResponse.model_json_schema(),",
    "output_schema=DumpRiskResponse.model_json_schema(),",
]


def main() -> None:
    if not PATH.exists():
        sys.exit(f"{PATH}를 찾을 수 없습니다 - 레포 루트에서 실행해주세요.")

    text = PATH.read_text(encoding="utf-8")

    anchor = "def _bazaar_extension("
    if text.count(anchor) != 1:
        sys.exit(
            f"'{anchor}' 앵커를 정확히 1곳에서 찾지 못했습니다(찾은 곳: {text.count(anchor)}). "
            "수동 확인이 필요합니다 (자동 수정 없이 종료함)."
        )
    text = text.replace(anchor, HELPER + anchor, 1)

    replaced = 0
    for old_call in CALL_SITES:
        count = text.count(old_call)
        if count != 1:
            sys.exit(
                f"'{old_call}' 호출부를 정확히 1곳에서 찾지 못했습니다(찾은 곳: {count}). "
                "수동 확인이 필요합니다 (자동 수정 없이 종료함)."
            )
        model_name = old_call.split("output_schema=")[1].split(".model_json_schema")[0]
        new_call = f"output_schema=_inline_schema_defs({model_name}.model_json_schema()),"
        text = text.replace(old_call, new_call, 1)
        replaced += 1

    PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"완료: _inline_schema_defs() 헬퍼를 추가하고, 호출부 {replaced}곳을 감쌌습니다 (3곳이어야 정상).")
    print("이제 'git diff app/payment.py'로 변경사항을 확인해주세요.")


if __name__ == "__main__":
    main()
