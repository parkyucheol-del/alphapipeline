"""
1회성 통합 패치 스크립트: (1) Sablier GraphQL 필드명 버그 수정, (2) 신규
GET /v1/security/token-risk 엔드포인트(GoPlus 1순위 / Honeypot.is 폴백) 추가.

건드리는 파일 6개: app/data_sources.py, app/config.py, app/schemas.py,
app/logic.py, app/payment.py, main.py

동작 방식: 모든 파일에 대해 먼저 "이 패치를 적용할 수 있는가"를 전부 확인하고
(검증 단계), 하나라도 실패하면 아무 파일도 건드리지 않고 종료한다(전부 적용
아니면 전부 취소). 전부 통과해야 실제로 파일을 씁니다.

실행: 레포 루트(app 폴더가 보이는 위치)에서 `python patch_sablier_goplus.py`
적용 후 `git diff`로 6개 파일 변경사항을 꼭 눈으로 확인할 것.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(".")
FILES = {
    "data_sources": ROOT / "app" / "data_sources.py",
    "config": ROOT / "app" / "config.py",
    "schemas": ROOT / "app" / "schemas.py",
    "logic": ROOT / "app" / "logic.py",
    "payment": ROOT / "app" / "payment.py",
    "main": ROOT / "main.py",
}


def fail(msg: str) -> None:
    sys.exit(f"\n중단: {msg}\n(아무 파일도 수정되지 않았습니다.)")


def read(key: str) -> str:
    path = FILES[key]
    if not path.exists():
        fail(f"{path}를 찾을 수 없습니다 - 레포 루트에서 실행해주세요.")
    return path.read_text(encoding="utf-8")


def must_replace(text: str, old: str, new: str, expected: int, where: str) -> str:
    count = text.count(old)
    if count != expected:
        fail(
            f"[{where}] 예상한 패턴을 찾지 못했습니다 (기대 {expected}회, 실제 {count}회).\n"
            f"찾던 텍스트:\n{old}"
        )
    return text.replace(old, new)


# ---------------------------------------------------------------------------
# 1) app/data_sources.py: Sablier lockupStreams -> LockupStream + GoPlus/Honeypot.is 함수 추가
# ---------------------------------------------------------------------------
ds_src = read("data_sources")

ds_src = must_replace(ds_src, "  lockupStreams(\n", "  LockupStream(\n", 2, "data_sources.py: lockupStreams(")
ds_src = must_replace(
    ds_src,
    'return data.get("lockupStreams", []) or []',
    'return data.get("LockupStream", []) or []',
    2,
    "data_sources.py: data.get(lockupStreams)",
)

DS_URL_ANCHOR = 'COINBASE_SPOT_PRICE_URL = "https://api.coinbase.com/v2/prices/{base}-USD/spot"'
DS_NEW_URLS = (
    DS_URL_ANCHOR
    + "\n\n"
    + '# GoPlus Security 토큰 보안/허니팟 체크 (무료, 앱키 불필요 - 커뮤니티 래퍼로 검증됨)\n'
    + 'GOPLUS_TOKEN_SECURITY_URL = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"\n'
    + '# Honeypot.is - GoPlus 실패 시 폴백 (공식 문서: API 키 불필요)\n'
    + 'HONEYPOT_IS_URL = "https://api.honeypot.is/v2/IsHoneypot"'
)
ds_src = must_replace(ds_src, DS_URL_ANCHOR, DS_NEW_URLS, 1, "data_sources.py: URL 상수 삽입 위치")

DS_NEW_FUNCTIONS = '''


async def get_goplus_token_security(chain_id: int, contract_address: str) -> dict:
    """
    GoPlus Security의 token_security 엔드포인트를 호출한다. 앱키 없이 동작한다
    (공식 문서는 Authorization 헤더를 언급하지만, 실사용 커뮤니티 래퍼 기준으로는
    키 없이도 정상 응답한다 - 2026-09 확인).
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            GOPLUS_TOKEN_SECURITY_URL.format(chain_id=chain_id),
            params={"contract_addresses": contract_address.lower()},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 1:
            raise RuntimeError(f"GoPlus 응답 오류: {body.get('message')}")
        result = body.get("result") or {}
        data = result.get(contract_address.lower())
        if not data:
            raise ValueError("GoPlus가 이 컨트랙트에 대한 데이터를 반환하지 않았습니다")
        return data


async def get_honeypot_is_check(chain_id: int, contract_address: str) -> dict:
    """Honeypot.is 폴백 조회. API 키가 필요 없다 (공식 문서 명시)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            HONEYPOT_IS_URL,
            params={"address": contract_address, "chainID": chain_id},
        )
        r.raise_for_status()
        return r.json()
'''
ds_src = ds_src.rstrip("\n") + "\n" + DS_NEW_FUNCTIONS.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 2) app/config.py: PRICE_TOKEN_RISK_USDC 추가 (기존 PRICE_DUMP_RISK_USDC 줄 스타일을 그대로 복제)
# ---------------------------------------------------------------------------
cfg_src = read("config")

cfg_matches = re.findall(r"^[ \t]*.*PRICE_DUMP_RISK_USDC.*$", cfg_src, re.MULTILINE)
if len(cfg_matches) != 1:
    fail(
        f"config.py: PRICE_DUMP_RISK_USDC 줄을 정확히 1개 찾아야 하는데 {len(cfg_matches)}개 찾았습니다."
    )
cfg_old_line = cfg_matches[0]
if "0.03" not in cfg_old_line:
    fail(f"config.py: PRICE_DUMP_RISK_USDC 줄에서 기본값 0.03을 찾지 못했습니다:\n{cfg_old_line}")
cfg_new_line = cfg_old_line.replace("PRICE_DUMP_RISK_USDC", "PRICE_TOKEN_RISK_USDC").replace("0.03", "0.02")
cfg_insert_at = cfg_src.index(cfg_old_line) + len(cfg_old_line)
cfg_src = cfg_src[:cfg_insert_at] + "\n" + cfg_new_line + cfg_src[cfg_insert_at:]


# ---------------------------------------------------------------------------
# 3) app/schemas.py: TokenRiskResponse + TOKEN_RISK_EXAMPLE 추가 (파일 맨 끝에 append)
# ---------------------------------------------------------------------------
schemas_src = read("schemas")

if "class TokenRiskResponse" in schemas_src:
    fail("schemas.py: TokenRiskResponse가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

SCHEMAS_NEW = '''


class TokenRiskResponse(BaseModel):
    generated_at: TimestampPair
    chain_id: int
    contract_address: str
    token_name: str | None = None
    token_symbol: str | None = None
    is_honeypot: bool | None = None
    buy_tax_pct: float | None = None
    sell_tax_pct: float | None = None
    is_mintable: bool | None = None
    is_open_source: bool | None = None
    owner_renounced: bool | None = None
    owner_address: str | None = None
    holder_count: int | None = None
    is_in_dex: bool | None = None
    risk_level: str
    risk_flags: list[str] = []
    data_source: str
    notice: str | None = None


TOKEN_RISK_EXAMPLE = {
    "generated_at": {"utc": "2026-09-04T12:00:00Z", "kst": "2026-09-04 21:00:00 KST"},
    "chain_id": 8453,
    "contract_address": "0x4200000000000000000000000000000000000006",
    "token_name": "Wrapped Ether",
    "token_symbol": "WETH",
    "is_honeypot": False,
    "buy_tax_pct": 0.0,
    "sell_tax_pct": 0.0,
    "is_mintable": False,
    "is_open_source": True,
    "owner_renounced": True,
    "owner_address": None,
    "holder_count": 125000,
    "is_in_dex": True,
    "risk_level": "LOW",
    "risk_flags": [],
    "data_source": "goplus",
    "notice": None,
}
'''
schemas_src = schemas_src.rstrip("\n") + "\n" + SCHEMAS_NEW.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 4) app/logic.py: _bool_or_none + get_token_risk 추가 (파일 맨 끝에 append)
# ---------------------------------------------------------------------------
logic_src = read("logic")

if "def get_token_risk" in logic_src:
    fail("logic.py: get_token_risk가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

LOGIC_NEW = '''


def _bool_or_none(v) -> bool | None:
    """GoPlus는 불리언을 문자열 "1"/"0"으로 주는 경우가 많아 통일해서 변환한다."""
    if v is None:
        return None
    return str(v) == "1" or v is True


async def get_token_risk(chain_id: int, contract_address: str) -> dict:
    """
    GoPlus Security를 1순위로, 실패 시 Honeypot.is로 폴백해서 토큰 보안/허니팟
    위험도를 조회한다. GET /v1/security/token-risk가 사용한다 (main.py 참고).
    """
    contract_address = contract_address.lower()
    owner_address = None
    data_source = "goplus"
    notice = None

    try:
        gp = await ds.get_goplus_token_security(chain_id, contract_address)
        is_honeypot = _bool_or_none(gp.get("is_honeypot"))
        buy_tax = float(gp.get("buy_tax") or 0) * 100
        sell_tax = float(gp.get("sell_tax") or 0) * 100
        is_mintable = _bool_or_none(gp.get("is_mintable"))
        is_open_source = _bool_or_none(gp.get("is_open_source"))
        owner_address = gp.get("owner_address") or None
        owner_renounced = (
            owner_address in ("", "0x0000000000000000000000000000000000000000", None)
            if owner_address is not None
            else None
        )
        holder_count = (
            int(gp["holder_count"]) if gp.get("holder_count") not in (None, "") else None
        )
        is_in_dex = _bool_or_none(gp.get("is_in_dex"))
        token_name = gp.get("token_name") or None
        token_symbol = gp.get("token_symbol") or None
    except Exception as e:
        logger.warning("GoPlus 조회 실패, Honeypot.is로 폴백합니다: %s", e)
        try:
            hp = await ds.get_honeypot_is_check(chain_id, contract_address)
        except Exception as e2:
            return {
                "generated_at": _timestamp_now(),
                "chain_id": chain_id,
                "contract_address": contract_address,
                "risk_level": "UNKNOWN",
                "risk_flags": ["data_unavailable"],
                "data_source": "none",
                "notice": f"GoPlus/Honeypot.is 둘 다 조회 실패: {e2}",
            }
        honeypot_result = hp.get("honeypotResult") or {}
        simulation = hp.get("simulationResult") or {}
        contract_code = hp.get("contractCode") or {}
        token_info = hp.get("token") or {}
        is_honeypot = honeypot_result.get("isHoneypot")
        buy_tax = simulation.get("buyTax")
        sell_tax = simulation.get("sellTax")
        is_mintable = None
        is_open_source = contract_code.get("openSource")
        owner_renounced = None
        holder_count = token_info.get("totalHolders")
        is_in_dex = None
        token_name = token_info.get("name")
        token_symbol = token_info.get("symbol")
        data_source = "honeypot_is"
        notice = "GoPlus 조회 실패로 Honeypot.is 폴백 데이터를 사용했습니다 (필드 커버리지가 더 좁습니다)."

    flags = []
    if is_honeypot:
        flags.append("honeypot")
    if is_mintable:
        flags.append("mintable")
    if is_open_source is False:
        flags.append("closed_source")
    if owner_renounced is False:
        flags.append("owner_not_renounced")
    if buy_tax and buy_tax >= 10:
        flags.append("high_buy_tax")
    if sell_tax and sell_tax >= 10:
        flags.append("high_sell_tax")

    if is_honeypot or (sell_tax and sell_tax >= 50):
        risk_level = "HIGH"
    elif flags:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {
        "generated_at": _timestamp_now(),
        "chain_id": chain_id,
        "contract_address": contract_address,
        "token_name": token_name,
        "token_symbol": token_symbol,
        "is_honeypot": is_honeypot,
        "buy_tax_pct": round(buy_tax, 2) if buy_tax is not None else None,
        "sell_tax_pct": round(sell_tax, 2) if sell_tax is not None else None,
        "is_mintable": is_mintable,
        "is_open_source": is_open_source,
        "owner_renounced": owner_renounced,
        "owner_address": owner_address if data_source == "goplus" else None,
        "holder_count": holder_count,
        "is_in_dex": is_in_dex,
        "risk_level": risk_level,
        "risk_flags": flags,
        "data_source": data_source,
        "notice": notice,
    }
'''
logic_src = logic_src.rstrip("\n") + "\n" + LOGIC_NEW.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# 5) app/payment.py: import 확장 + token_risk_option + 신규 라우트
# ---------------------------------------------------------------------------
pay_src = read("payment")

PAY_OLD_IMPORT = """from app.schemas import (
    DUMP_RISK_EXAMPLE,
    KIMCHI_ALERT_EXAMPLE,
    MARKDOWN_EXAMPLE,
    DumpRiskResponse,
    KimchiAlertResponse,
    MarkdownResponse,
)"""
PAY_NEW_IMPORT = """from app.schemas import (
    DUMP_RISK_EXAMPLE,
    KIMCHI_ALERT_EXAMPLE,
    MARKDOWN_EXAMPLE,
    TOKEN_RISK_EXAMPLE,
    DumpRiskResponse,
    KimchiAlertResponse,
    MarkdownResponse,
    TokenRiskResponse,
)"""
pay_src = must_replace(pay_src, PAY_OLD_IMPORT, PAY_NEW_IMPORT, 1, "payment.py: app.schemas import 블록")

PAY_OLD_OPTION = "    dump_risk_option = _payment_option(settings.PRICE_DUMP_RISK_USDC)"
PAY_NEW_OPTION = PAY_OLD_OPTION + "\n    token_risk_option = _payment_option(settings.PRICE_TOKEN_RISK_USDC)"
pay_src = must_replace(pay_src, PAY_OLD_OPTION, PAY_NEW_OPTION, 1, "payment.py: dump_risk_option 줄")

PAY_OLD_ROUTES_TAIL = '''            service_name="AlphaPipeline AI Markdown",
            tags=["ai-tools", "web-scraping", "markdown", "llm"],
        ),
    }'''
PAY_NEW_ROUTES_TAIL = '''            service_name="AlphaPipeline AI Markdown",
            tags=["ai-tools", "web-scraping", "markdown", "llm"],
        ),
        "GET /v1/security/token-risk": _make_route_config(
            accepts=[token_risk_option],
            mime_type="application/json",
            description=(
                "GoPlus/Honeypot.is-backed token security check - honeypot flag, "
                "buy/sell tax, mintability, and ownership renouncement for a given "
                "contract address, so a bot can decide before it buys."
            ),
            resource=_resource_url("/v1/security/token-risk"),
            extensions=_bazaar_extension(
                input_example={
                    "chain_id": 8453,
                    "contract_address": "0x4200000000000000000000000000000000000006",
                },
                input_schema={
                    "type": "object",
                    "properties": {
                        "chain_id": {
                            "type": "integer",
                            "description": "EVM chain id, e.g. 8453 for Base.",
                        },
                        "contract_address": {
                            "type": "string",
                            "description": "Token contract address (0x...).",
                        },
                    },
                    "required": ["chain_id", "contract_address"],
                },
                output_example=TOKEN_RISK_EXAMPLE,
                output_schema=_inline_schema_defs(TokenRiskResponse.model_json_schema()),
            ),
            service_name="AlphaPipeline Token Risk Scanner",
            tags=["crypto", "security", "honeypot", "token-risk"],
        ),
    }'''
pay_src = must_replace(pay_src, PAY_OLD_ROUTES_TAIL, PAY_NEW_ROUTES_TAIL, 1, "payment.py: routes 딕셔너리 끝부분")


# ---------------------------------------------------------------------------
# 6) main.py: import 확장 + 루트 메타데이터 확장 + 신규 엔드포인트
# ---------------------------------------------------------------------------
main_src = read("main")

MAIN_OLD_LOGIC_IMPORT = "from app.logic import get_dump_risk, get_kimchi_alert, refresh_unlock_cache"
MAIN_NEW_LOGIC_IMPORT = "from app.logic import get_dump_risk, get_kimchi_alert, get_token_risk, refresh_unlock_cache"
main_src = must_replace(main_src, MAIN_OLD_LOGIC_IMPORT, MAIN_NEW_LOGIC_IMPORT, 1, "main.py: app.logic import 줄")

MAIN_OLD_SCHEMAS_IMPORT = (
    "from app.schemas import DumpRiskResponse, ErrorResponse, KimchiAlertResponse, MarkdownResponse"
)
MAIN_NEW_SCHEMAS_IMPORT = (
    "from app.schemas import DumpRiskResponse, ErrorResponse, KimchiAlertResponse, MarkdownResponse, "
    "TokenRiskResponse"
)
main_src = must_replace(main_src, MAIN_OLD_SCHEMAS_IMPORT, MAIN_NEW_SCHEMAS_IMPORT, 1, "main.py: app.schemas import 줄")

MAIN_OLD_PRICE_DICT = '''        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            "/v1/unlocks/dump-risk": settings.PRICE_DUMP_RISK_USDC,
        },'''
MAIN_NEW_PRICE_DICT = '''        "price_per_call_usdc": {
            "/v1/market/kimchi-alert": settings.PRICE_KIMCHI_ALERT_USDC,
            "/v1/tools/ai-markdown": settings.PRICE_AI_MARKDOWN_USDC,
            "/v1/unlocks/dump-risk": settings.PRICE_DUMP_RISK_USDC,
            "/v1/security/token-risk": settings.PRICE_TOKEN_RISK_USDC,
        },'''
main_src = must_replace(main_src, MAIN_OLD_PRICE_DICT, MAIN_NEW_PRICE_DICT, 1, "main.py: price_per_call_usdc 딕셔너리")

MAIN_OLD_ENDPOINTS_LIST = '''        "endpoints": [
            "/v1/unlocks/dump-risk",
            "/v1/market/kimchi-alert",
            "/v1/tools/ai-markdown",
        ],'''
MAIN_NEW_ENDPOINTS_LIST = '''        "endpoints": [
            "/v1/unlocks/dump-risk",
            "/v1/market/kimchi-alert",
            "/v1/tools/ai-markdown",
            "/v1/security/token-risk",
        ],'''
main_src = must_replace(main_src, MAIN_OLD_ENDPOINTS_LIST, MAIN_NEW_ENDPOINTS_LIST, 1, "main.py: endpoints 리스트")

MAIN_OLD_MAIN_GUARD = '''if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)'''
MAIN_NEW_ENDPOINT_PLUS_GUARD = '''@app.get(
    "/v1/security/token-risk",
    tags=["security"],
    summary="Check a token contract for honeypot/scam risk before buying",
    description=(
        "Use this endpoint when you need to know whether a token contract is safe to buy - "
        "call it right before entering a position on an unfamiliar or newly-listed token. "
        "Returns is_honeypot, buy/sell tax percentages, mintability, open-source status, "
        "ownership renouncement, holder count, and a summarized risk_level "
        "(LOW/MEDIUM/HIGH/UNKNOWN) with risk_flags. Data source is GoPlus Security "
        "(primary), falling back to Honeypot.is if GoPlus is unavailable - check the "
        "data_source and notice fields to see which was used. Input: required "
        "`chain_id` (EVM chain id, e.g. 8453 for Base) and `contract_address` query "
        "parameters. Do not treat a null field as 'safe' - it means that field could not "
        "be determined; check risk_level and risk_flags instead."
    ),
    responses={
        200: {"model": TokenRiskResponse, "description": "토큰 보안/허니팟 위험도 데이터"},
        402: {"description": "x402 결제 필요"},
        502: {"model": ErrorResponse, "description": "업스트림(GoPlus/Honeypot.is) 오류"},
    },
)
async def token_risk_endpoint(
    chain_id: int = Query(..., description="EVM chain id, e.g. 8453 for Base"),
    contract_address: str = Query(..., description="Token contract address (0x...)"),
):
    try:
        data = await get_token_risk(chain_id, contract_address)
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("token-risk 처리 실패")
        return JSONResponse(status_code=502, content={"error": "upstream_error", "message": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.PORT, reload=True)'''
main_src = must_replace(main_src, MAIN_OLD_MAIN_GUARD, MAIN_NEW_ENDPOINT_PLUS_GUARD, 1, "main.py: __main__ 가드(신규 엔드포인트 삽입 위치)")


# ---------------------------------------------------------------------------
# 전부 검증 통과 - 이제 실제로 파일에 쓴다.
# ---------------------------------------------------------------------------
FILES["data_sources"].write_text(ds_src, encoding="utf-8", newline="\n")
FILES["config"].write_text(cfg_src, encoding="utf-8", newline="\n")
FILES["schemas"].write_text(schemas_src, encoding="utf-8", newline="\n")
FILES["logic"].write_text(logic_src, encoding="utf-8", newline="\n")
FILES["payment"].write_text(pay_src, encoding="utf-8", newline="\n")
FILES["main"].write_text(main_src, encoding="utf-8", newline="\n")

print("완료: 6개 파일 모두 패치했습니다.")
print("  - app/data_sources.py : Sablier lockupStreams -> LockupStream 수정, GoPlus/Honeypot.is 함수 추가")
print("  - app/config.py       : PRICE_TOKEN_RISK_USDC 추가")
print("  - app/schemas.py      : TokenRiskResponse / TOKEN_RISK_EXAMPLE 추가")
print("  - app/logic.py        : get_token_risk / _bool_or_none 추가")
print("  - app/payment.py      : GET /v1/security/token-risk 라우트 등록")
print("  - main.py             : GET /v1/security/token-risk 엔드포인트 추가")
print("")
print("이제 'git diff'로 6개 파일 변경사항을 확인해주세요.")
