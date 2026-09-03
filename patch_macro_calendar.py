"""
AlphaPipeline 통합 패치 스크립트: GET /v1/calendar/macro-dday 신규 구현
                                + 기존 라우트 설명 문구에 "Paid in USDC on Base." 보강

실행 위치: 프로젝트 루트 (app/ 폴더와 main.py가 있는 곳)에서
    python patch_macro_calendar.py

수정 대상 5개 파일: app/config.py, app/schemas.py, app/logic.py, app/payment.py, main.py
(이번 엔드포인트는 외부 API를 쓰지 않는 정적 데이터라 app/data_sources.py는 건드리지 않음)

패턴: (1) 전부 검증 -> (2) 전부 통과해야만 실제로 씀(부분 적용 없음), (3) 멱등성 가드.
"""
import re
import sys

FILES = {
    "config": "app/config.py",
    "schemas": "app/schemas.py",
    "logic": "app/logic.py",
    "payment": "app/payment.py",
    "main": "main.py",
}


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def must_replace(content: str, old: str, new: str, *, label: str, count: int = 1) -> str:
    found = content.count(old)
    if found != count:
        fail(
            f"{label}: 예상한 앵커 텍스트를 {count}번이 아니라 {found}번 찾았습니다 - "
            "파일이 이전 패치(patch_sablier_goplus.py/patch_funding_rate.py/"
            "patch_dex_slippage.py)와 다른 상태인 것 같습니다. 앵커:\n"
            f"{old!r}"
        )
    return content.replace(old, new, count)


# ---------------------------------------------------------------------------
# 0. 읽기 + 멱등성 가드
# ---------------------------------------------------------------------------
contents = {key: read(path) for key, path in FILES.items()}

if "PRICE_MACRO_DDAY_USDC" in contents["config"]:
    fail("config.py: PRICE_MACRO_DDAY_USDC가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")
if "MacroDdayResponse" in contents["schemas"]:
    fail("schemas.py: MacroDdayResponse가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")
if "get_macro_calendar_dday" in contents["logic"]:
    fail("logic.py: get_macro_calendar_dday가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")
if "macro_dday_option" in contents["payment"]:
    fail("payment.py: macro_dday_option이 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")
if "macro_dday_endpoint" in contents["main"]:
    fail("main.py: macro_dday_endpoint가 이미 존재합니다 - 이미 적용된 패치 아닌지 확인해주세요.")

# ---------------------------------------------------------------------------
# 1. app/config.py: PRICE_MACRO_DDAY_USDC 추가 (기존 PRICE_DEX_SLIPPAGE_USDC 줄을
#    정규식으로 그대로 복사해서 이름/기본값만 바꿔치기 - 기존 문법 스타일을 그대로 따름)
# ---------------------------------------------------------------------------
config_price_line_re = re.compile(
    r'^\s*PRICE_DEX_SLIPPAGE_USDC\s*:\s*float\s*=\s*float\(os\.getenv\("PRICE_DEX_SLIPPAGE_USDC",\s*"0\.02"\)\)\s*$',
    re.MULTILINE,
)
m = config_price_line_re.search(contents["config"])
if not m:
    fail("config.py: PRICE_DEX_SLIPPAGE_USDC 줄을 정규식으로 찾지 못했습니다 (patch_dex_slippage.py가 먼저 적용됐는지 확인해주세요).")
dex_price_line = m.group(0)
macro_price_line = dex_price_line.replace("PRICE_DEX_SLIPPAGE_USDC", "PRICE_MACRO_DDAY_USDC").replace('"0.02"', '"0.01"')
new_config = contents["config"].replace(dex_price_line, dex_price_line + "\n" + macro_price_line, 1)

# ---------------------------------------------------------------------------
# 2. app/schemas.py: TimeRemaining / MacroCalendarEventBrief / MacroDdayResponse
#    / MACRO_DDAY_EXAMPLE 추가 (파일 끝에 안전하게 append)
# ---------------------------------------------------------------------------
SCHEMAS_APPEND = """

class TimeRemaining(BaseModel):
    days: int
    hours: int
    minutes: int


class MacroCalendarEventBrief(BaseModel):
    event_name: str
    event_type: str
    event_datetime: TimestampPair
    impact_level: str
    tags: list[str] = []


class MacroDdayResponse(BaseModel):
    generated_at: TimestampPair
    event_name: str | None = None
    event_type: str | None = None
    event_datetime: TimestampPair | None = None
    d_day: int | None = None
    time_remaining: TimeRemaining | None = None
    impact_level: str | None = None
    tags: list[str] = []
    description: str | None = None
    upcoming_events: list[MacroCalendarEventBrief] = []
    data_source: str
    notice: str | None = None


MACRO_DDAY_EXAMPLE = {
    "generated_at": {"utc": "2026-09-03T12:00:00Z", "kst": "2026-09-03 21:00:00 KST"},
    "event_name": "FOMC 금리 결정 (9월, SEP 포함)",
    "event_type": "FOMC",
    "event_datetime": {"utc": "2026-09-16T18:00:00Z", "kst": "2026-09-17 03:00:00 KST"},
    "d_day": 13,
    "time_remaining": {"days": 13, "hours": 6, "minutes": 0},
    "impact_level": "HIGH",
    "tags": ["rate-decision", "fomc", "interest-rates"],
    "description": "Federal Reserve interest rate decision and policy statement.",
    "upcoming_events": [
        {
            "event_name": "CPI (2026년 9월 기준)",
            "event_type": "CPI",
            "event_datetime": {"utc": "2026-10-14T12:30:00Z", "kst": "2026-10-14 21:30:00 KST"},
            "impact_level": "HIGH",
            "tags": ["inflation", "cpi", "cpi-report"],
        },
        {
            "event_name": "NFP (2026년 9월 기준)",
            "event_type": "NFP",
            "event_datetime": {"utc": "2026-10-02T12:30:00Z", "kst": "2026-10-02 21:30:00 KST"},
            "impact_level": "HIGH",
            "tags": ["employment", "nfp", "jobs-report"],
        },
    ],
    "data_source": "static_2026_macro_calendar",
    "notice": (
        "이 캘린더는 2026년 FOMC 금리 결정, 미국 CPI, 미국 고용지표(NFP) 일정을 공식 "
        "연준(Fed)/BLS 발표 기준으로 정적으로 내장한 것입니다 - 실시간 외부 API를 호출하지 "
        "않습니다. 일정은 연준/BLS가 추후 변경할 수 있고 2027년 일정은 아직 포함되어 있지 "
        "않으니, 중요한 의사결정 전에는 공식 소스(federalreserve.gov, bls.gov)로 재확인하세요."
    ),
}
"""
new_schemas = contents["schemas"].rstrip("\n") + "\n" + SCHEMAS_APPEND.lstrip("\n")

# ---------------------------------------------------------------------------
# 3. app/logic.py: 정적 2026 매크로 캘린더 + get_macro_calendar_dday() 추가
# ---------------------------------------------------------------------------
LOGIC_APPEND = '''

# 2026년 FOMC(연준 금리 결정) / CPI(미국 소비자물가지수) /
# NFP(미국 고용지표, Employment Situation) 정적 일정표.
# 출처: federalreserve.gov/monetarypolicy/fomccalendars.htm (FOMC),
#       bls.gov/schedule/news_release/cpi.htm (CPI),
#       bls.gov/schedule/news_release/empsit.htm (NFP/Employment Situation).
# 발표 시각: FOMC 성명서는 회의 마지막날 미동부 시간 오후 2시(ET),
# CPI/NFP는 미동부 시간 오전 8시 30분(ET)이며, 아래 utc_iso는 2026년
# 미국 서머타임(DST, 3월 8일~11월 1일) 적용 여부를 이미 반영해 UTC로
# 미리 계산해둔 값이다 (정적 데이터라서 실행 시점에 DST를 다시 계산하지 않음).
_MACRO_EVENTS_2026 = [
    {"event_name": "FOMC 금리 결정 (1월)", "event_type": "FOMC", "utc_iso": "2026-01-28T19:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates"], "description": "Federal Reserve interest rate decision and policy statement."},
    {"event_name": "NFP (2025년 12월 기준)", "event_type": "NFP", "utc_iso": "2026-01-09T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2025년 12월 기준)", "event_type": "CPI", "utc_iso": "2026-01-13T13:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 1월 기준)", "event_type": "NFP", "utc_iso": "2026-02-11T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 1월 기준)", "event_type": "CPI", "utc_iso": "2026-02-13T13:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 2월 기준)", "event_type": "NFP", "utc_iso": "2026-03-06T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 2월 기준)", "event_type": "CPI", "utc_iso": "2026-03-11T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (3월, SEP 포함)", "event_type": "FOMC", "utc_iso": "2026-03-18T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates", "sep"], "description": "Federal Reserve interest rate decision, policy statement, and Summary of Economic Projections (dot plot)."},
    {"event_name": "NFP (2026년 3월 기준)", "event_type": "NFP", "utc_iso": "2026-04-03T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 3월 기준)", "event_type": "CPI", "utc_iso": "2026-04-10T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (4월)", "event_type": "FOMC", "utc_iso": "2026-04-29T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates"], "description": "Federal Reserve interest rate decision and policy statement."},
    {"event_name": "NFP (2026년 4월 기준)", "event_type": "NFP", "utc_iso": "2026-05-08T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 4월 기준)", "event_type": "CPI", "utc_iso": "2026-05-12T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 5월 기준)", "event_type": "NFP", "utc_iso": "2026-06-05T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 5월 기준)", "event_type": "CPI", "utc_iso": "2026-06-10T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (6월, SEP 포함)", "event_type": "FOMC", "utc_iso": "2026-06-17T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates", "sep"], "description": "Federal Reserve interest rate decision, policy statement, and Summary of Economic Projections (dot plot)."},
    {"event_name": "NFP (2026년 6월 기준)", "event_type": "NFP", "utc_iso": "2026-07-02T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 6월 기준)", "event_type": "CPI", "utc_iso": "2026-07-14T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (7월)", "event_type": "FOMC", "utc_iso": "2026-07-29T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates"], "description": "Federal Reserve interest rate decision and policy statement."},
    {"event_name": "NFP (2026년 7월 기준)", "event_type": "NFP", "utc_iso": "2026-08-07T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 7월 기준)", "event_type": "CPI", "utc_iso": "2026-08-12T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 8월 기준)", "event_type": "NFP", "utc_iso": "2026-09-04T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 8월 기준)", "event_type": "CPI", "utc_iso": "2026-09-11T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (9월, SEP 포함)", "event_type": "FOMC", "utc_iso": "2026-09-16T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates", "sep"], "description": "Federal Reserve interest rate decision, policy statement, and Summary of Economic Projections (dot plot)."},
    {"event_name": "NFP (2026년 9월 기준)", "event_type": "NFP", "utc_iso": "2026-10-02T12:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 9월 기준)", "event_type": "CPI", "utc_iso": "2026-10-14T12:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "FOMC 금리 결정 (10월)", "event_type": "FOMC", "utc_iso": "2026-10-28T18:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates"], "description": "Federal Reserve interest rate decision and policy statement."},
    {"event_name": "NFP (2026년 10월 기준)", "event_type": "NFP", "utc_iso": "2026-11-06T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "CPI (2026년 10월 기준)", "event_type": "CPI", "utc_iso": "2026-11-10T13:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
    {"event_name": "NFP (2026년 11월 기준)", "event_type": "NFP", "utc_iso": "2026-12-04T13:30:00Z", "impact_level": "HIGH", "tags": ["employment", "nfp", "jobs-report"], "description": "US Employment Situation report (nonfarm payrolls, unemployment rate)."},
    {"event_name": "FOMC 금리 결정 (12월, SEP 포함)", "event_type": "FOMC", "utc_iso": "2026-12-09T19:00:00Z", "impact_level": "HIGH", "tags": ["rate-decision", "fomc", "interest-rates", "sep"], "description": "Federal Reserve interest rate decision, policy statement, and Summary of Economic Projections (dot plot)."},
    {"event_name": "CPI (2026년 11월 기준)", "event_type": "CPI", "utc_iso": "2026-12-10T13:30:00Z", "impact_level": "HIGH", "tags": ["inflation", "cpi", "cpi-report"], "description": "US Consumer Price Index (CPI) inflation report release."},
]

_MACRO_CALENDAR_NOTICE = (
    "이 캘린더는 2026년 FOMC 금리 결정, 미국 CPI, 미국 고용지표(NFP) 일정을 공식 "
    "연준(Fed)/BLS 발표 기준으로 정적으로 내장한 것입니다 - 실시간 외부 API를 호출하지 "
    "않습니다. 일정은 연준/BLS가 추후 변경할 수 있고 2027년 일정은 아직 포함되어 있지 "
    "않으니, 중요한 의사결정 전에는 공식 소스(federalreserve.gov, bls.gov)로 재확인하세요."
)


def _utc_iso_to_timestamp_pair(utc_iso: str) -> dict:
    '"...Z" 형식의 UTC ISO 문자열을 {utc, kst} 쌍으로 변환한다.'
    from datetime import datetime, timedelta, timezone

    dt_utc = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    kst_tz = timezone(timedelta(hours=9))
    dt_kst = dt_utc.astimezone(kst_tz)
    return {
        "utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kst": dt_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
    }


async def get_macro_calendar_dday() -> dict:
    """
    2026년 FOMC/CPI/NFP 일정을 정적으로 내장해 가장 가까운
    이벤트까지의 D-Day를 계산한다. 외부 API 호출 없음.
    GET /v1/calendar/macro-dday가 사용한다 (main.py 참고).
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    parsed = [
        (datetime.fromisoformat(ev["utc_iso"].replace("Z", "+00:00")), ev)
        for ev in _MACRO_EVENTS_2026
    ]
    upcoming_all = sorted((item for item in parsed if item[0] >= now), key=lambda x: x[0])

    if not upcoming_all:
        return {
            "generated_at": _timestamp_now(),
            "event_name": None,
            "event_type": None,
            "event_datetime": None,
            "d_day": None,
            "time_remaining": None,
            "impact_level": None,
            "tags": [],
            "description": None,
            "upcoming_events": [],
            "data_source": "static_2026_macro_calendar",
            "notice": _MACRO_CALENDAR_NOTICE
            + " 2026년 내장 일정이 모두 지났습니다 - 다음 세션에서 갱신이 필요합니다.",
        }

    nearest_dt, nearest_ev = upcoming_all[0]
    total_seconds = max((nearest_dt - now).total_seconds(), 0)
    days_remaining = int(total_seconds // 86400)
    hours_remaining = int((total_seconds % 86400) // 3600)
    minutes_remaining = int((total_seconds % 3600) // 60)
    d_day = (nearest_dt.date() - now.date()).days

    upcoming_brief = [
        {
            "event_name": ev["event_name"],
            "event_type": ev["event_type"],
            "event_datetime": _utc_iso_to_timestamp_pair(ev["utc_iso"]),
            "impact_level": ev["impact_level"],
            "tags": ev["tags"],
        }
        for _, ev in upcoming_all[1:4]
    ]

    return {
        "generated_at": _timestamp_now(),
        "event_name": nearest_ev["event_name"],
        "event_type": nearest_ev["event_type"],
        "event_datetime": _utc_iso_to_timestamp_pair(nearest_ev["utc_iso"]),
        "d_day": d_day,
        "time_remaining": {
            "days": days_remaining,
            "hours": hours_remaining,
            "minutes": minutes_remaining,
        },
        "impact_level": nearest_ev["impact_level"],
        "tags": nearest_ev["tags"],
        "description": nearest_ev["description"],
        "upcoming_events": upcoming_brief,
        "data_source": "static_2026_macro_calendar",
        "notice": _MACRO_CALENDAR_NOTICE,
    }
'''
new_logic = contents["logic"].rstrip("\n") + "\n" + LOGIC_APPEND.lstrip("\n")

# ---------------------------------------------------------------------------
# 4. app/payment.py
#    (a) import 블록에 MACRO_DDAY_EXAMPLE / MacroDdayResponse 추가
#    (b) macro_dday_option 변수 추가
#    (c) 기존 6개 라우트 description 끝에 "Paid in USDC on Base." 보강
#    (d) 신규 GET /v1/calendar/macro-dday 라우트 추가
# ---------------------------------------------------------------------------
new_payment = contents["payment"]

new_payment = must_replace(
    new_payment,
    "from app.schemas import (\n"
    "    DEX_SLIPPAGE_EXAMPLE,\n"
    "    DUMP_RISK_EXAMPLE,\n"
    "    FUNDING_RATE_EXAMPLE,\n"
    "    KIMCHI_ALERT_EXAMPLE,\n"
    "    MARKDOWN_EXAMPLE,\n"
    "    TOKEN_RISK_EXAMPLE,\n"
    "    DexSlippageResponse,\n"
    "    DumpRiskResponse,\n"
    "    FundingRateResponse,\n"
    "    KimchiAlertResponse,\n"
    "    MarkdownResponse,\n"
    "    TokenRiskResponse,\n"
    ")",
    "from app.schemas import (\n"
    "    DEX_SLIPPAGE_EXAMPLE,\n"
    "    DUMP_RISK_EXAMPLE,\n"
    "    FUNDING_RATE_EXAMPLE,\n"
    "    KIMCHI_ALERT_EXAMPLE,\n"
    "    MACRO_DDAY_EXAMPLE,\n"
    "    MARKDOWN_EXAMPLE,\n"
    "    TOKEN_RISK_EXAMPLE,\n"
    "    DexSlippageResponse,\n"
    "    DumpRiskResponse,\n"
    "    FundingRateResponse,\n"
    "    KimchiAlertResponse,\n"
    "    MacroDdayResponse,\n"
    "    MarkdownResponse,\n"
    "    TokenRiskResponse,\n"
    ")",
    label="payment.py import 블록",
)

def _insert_before_routes_dict(content: str) -> str:
    """
    "routes: dict[str, RouteConfig] = {" 줄 하나만 정규식으로 찾아서(주변 들여쓰기가
    스페이스든 탭이든, 앞뒤 공백이 정확히 몇 칸이든 상관없이) 그 줄의 들여쓰기를 그대로
    재사용해 macro_dday_option 선언 + 신규 라우트 엔트리(딕셔너리의 첫 번째 항목으로)를
    끼워 넣는다. 기존 라우트(kimchi/ai-markdown/.../dex-slippage) 블록이 정확히 어떻게
    끝나는지에 의존하지 않아서, 그쪽 텍스트가 조금이라도 다르게 편집되어 있어도(예: 수동
    GitHub 웹 편집기 수정 이력 등) 이 앵커는 영향을 받지 않는다.
    """
    pattern = re.compile(r'([ \t]*)routes:\s*dict\[str,\s*RouteConfig\]\s*=\s*\{\n')
    matches = list(pattern.finditer(content))
    if len(matches) != 1:
        fail(
            f"payment.py routes 딕셔너리 선언: 예상한 패턴을 1번이 아니라 {len(matches)}번 "
            "찾았습니다 - 파일이 이전 패치와 다른 상태인 것 같습니다."
        )
    m = matches[0]
    indent = m.group(1)
    inner_indent = indent + "    "
    new_route_entry = (
        f'{inner_indent}"GET /v1/calendar/macro-dday": _make_route_config(\n'
        f'{inner_indent}    accepts=[macro_dday_option],\n'
        f'{inner_indent}    mime_type="application/json",\n'
        f'{inner_indent}    description=(\n'
        f'{inner_indent}        "Static 2026 macro calendar - countdown to the nearest FOMC rate "\n'
        f'{inner_indent}        "decision, US CPI, or US Employment Situation (NFP) release, with "\n'
        f'{inner_indent}        "impact tags and the next few upcoming events. No live external API "\n'
        f'{inner_indent}        "call is made; dates are pre-loaded from official Fed/BLS schedules. "\n'
        f'{inner_indent}        "Paid in USDC on Base."\n'
        f'{inner_indent}    ),\n'
        f'{inner_indent}    resource=_resource_url("/v1/calendar/macro-dday"),\n'
        f'{inner_indent}    extensions=_bazaar_extension(\n'
        f'{inner_indent}        input_example={{}},\n'
        f'{inner_indent}        input_schema={{"type": "object", "properties": {{}}, "required": []}},\n'
        f'{inner_indent}        output_example=MACRO_DDAY_EXAMPLE,\n'
        f'{inner_indent}        output_schema=_inline_schema_defs(MacroDdayResponse.model_json_schema()),\n'
        f'{inner_indent}    ),\n'
        f'{inner_indent}    service_name="AlphaPipeline Macro Calendar",\n'
        f'{inner_indent}    tags=["macro", "calendar", "fomc", "cpi", "nfp"],\n'
        f'{inner_indent}),\n'
    )
    replacement = (
        f"{indent}macro_dday_option = _payment_option(settings.PRICE_MACRO_DDAY_USDC)\n"
        f"{indent}routes: dict[str, RouteConfig] = {{\n"
        f"{new_route_entry}"
    )
    return content[: m.start()] + replacement + content[m.end() :]


new_payment = _insert_before_routes_dict(new_payment)

DESCRIPTION_PATCHES = [
    (
        "kimchi-alert description",
        '            description=(\n'
        '                "Real-time Korea (Upbit) vs global crypto price premium - the "\n'
        '                "\'kimchi premium\' - with reverse-premium and 1h-surge alerts."\n'
        '            ),',
        '            description=(\n'
        '                "Real-time Korea (Upbit) vs global crypto price premium - the "\n'
        '                "\'kimchi premium\' - with reverse-premium and 1h-surge alerts. "\n'
        '                "Paid in USDC on Base."\n'
        '            ),',
    ),
    (
        "ai-markdown description",
        '            description=(\n'
        '                "Convert any webpage URL into clean, ad-free Markdown text "\n'
        '                "optimized for LLM context windows."\n'
        '            ),',
        '            description=(\n'
        '                "Convert any webpage URL into clean, ad-free Markdown text "\n'
        '                "optimized for LLM context windows. Paid in USDC on Base."\n'
        '            ),',
    ),
    (
        "token-risk description",
        '            description=(\n'
        '                "GoPlus/Honeypot.is-backed token security check - honeypot flag, "\n'
        '                "buy/sell tax, mintability, and ownership renouncement for a given "\n'
        '                "contract address, so a bot can decide before it buys."\n'
        '            ),',
        '            description=(\n'
        '                "GoPlus/Honeypot.is-backed token security check - honeypot flag, "\n'
        '                "buy/sell tax, mintability, and ownership renouncement for a given "\n'
        '                "contract address, so a bot can decide before it buys. Paid in "\n'
        '                "USDC on Base."\n'
        '            ),',
    ),
    (
        "funding-rate description",
        '            description=(\n'
        '                "Bybit (primary) / Binance (fallback) perpetual futures funding rate - "\n'
        '                "the key signal for long/short crowding that traders use to time or hedge "\n'
        '                "positions before the next funding settlement."\n'
        '            ),',
        '            description=(\n'
        '                "Bybit (primary) / Binance (fallback) perpetual futures funding rate - "\n'
        '                "the key signal for long/short crowding that traders use to time or hedge "\n'
        '                "positions before the next funding settlement. Paid in USDC on Base."\n'
        '            ),',
    ),
    (
        "dex-liquidity-slippage description",
        '            description=(\n'
        '                "GeckoTerminal-backed DEX pool liquidity and estimated trade slippage - "\n'
        '                "size a trade or compare pools before swapping, with a clearly-flagged "\n'
        '                "constant-product approximation model."\n'
        '            ),',
        '            description=(\n'
        '                "GeckoTerminal-backed DEX pool liquidity and estimated trade slippage - "\n'
        '                "size a trade or compare pools before swapping, with a clearly-flagged "\n'
        '                "constant-product approximation model. Paid in USDC on Base."\n'
        '            ),',
    ),
    (
        "dump-risk description",
        '            description=(\n'
        '                "Tokens with large amounts of currently-locked or vesting supply "\n'
        '                "relative to circulating supply - a proxy for future sell/dump pressure."\n'
        '            ),',
        '            description=(\n'
        '                "Tokens with large amounts of currently-locked or vesting supply "\n'
        '                "relative to circulating supply - a proxy for future sell/dump pressure. "\n'
        '                "Paid in USDC on Base."\n'
        '            ),',
    ),
]

for label, old, new in DESCRIPTION_PATCHES:
    new_payment = must_replace(new_payment, old, new, label=f"payment.py {label}")

# ---------------------------------------------------------------------------
# 5. main.py
# ---------------------------------------------------------------------------
new_main = contents["main"]

new_main = must_replace(
    new_main,
    "from app.logic import (\n"
    "    get_dex_liquidity_slippage,\n"
    "    get_dump_risk,\n"
    "    get_funding_rate,\n"
    "    get_kimchi_alert,\n"
    "    get_token_risk,\n"
    "    refresh_unlock_cache,\n"
    ")",
    "from app.logic import (\n"
    "    get_dex_liquidity_slippage,\n"
    "    get_dump_risk,\n"
    "    get_funding_rate,\n"
    "    get_kimchi_alert,\n"
    "    get_macro_calendar_dday,\n"
    "    get_token_risk,\n"
    "    refresh_unlock_cache,\n"
    ")",
    label="main.py app.logic import",
)

new_main = must_replace(
    new_main,
    "from app.schemas import (\n"
    "    DexSlippageResponse,\n"
    "    DumpRiskResponse,\n"
    "    ErrorResponse,\n"
    "    FundingRateResponse,\n"
    "    KimchiAlertResponse,\n"
    "    MarkdownResponse,\n"
    "    TokenRiskResponse,\n"
    ")",
    "from app.schemas import (\n"
    "    DexSlippageResponse,\n"
    "    DumpRiskResponse,\n"
    "    ErrorResponse,\n"
    "    FundingRateResponse,\n"
    "    KimchiAlertResponse,\n"
    "    MacroDdayResponse,\n"
    "    MarkdownResponse,\n"
    "    TokenRiskResponse,\n"
    ")",
    label="main.py app.schemas import",
)

new_main = must_replace(
    new_main,
    '            "/v1/dex/liquidity-slippage": settings.PRICE_DEX_SLIPPAGE_USDC,\n'
    "        },",
    '            "/v1/dex/liquidity-slippage": settings.PRICE_DEX_SLIPPAGE_USDC,\n'
    '            "/v1/calendar/macro-dday": settings.PRICE_MACRO_DDAY_USDC,\n'
    "        },",
    label="main.py price_per_call_usdc 딕셔너리",
)

new_main = must_replace(
    new_main,
    '            "/v1/dex/liquidity-slippage",\n'
    "        ],",
    '            "/v1/dex/liquidity-slippage",\n'
    '            "/v1/calendar/macro-dday",\n'
    "        ],",
    label="main.py endpoints 리스트",
)

MACRO_ENDPOINT_FUNC = '''

@app.get(
    "/v1/calendar/macro-dday",
    tags=["market"],
    summary="Countdown to the nearest major US macro event (FOMC/CPI/NFP)",
    description=(
        "Use this endpoint when you need to know how much time is left before the next "
        "market-moving US macro release - a Fed interest rate decision (FOMC), CPI inflation "
        "report, or nonfarm payrolls (NFP) release - to plan position sizing or avoid holding "
        "risk into a high-impact print. Returns the nearest event's name, exact date/time (UTC "
        "and KST), a D-Day countdown, exact time remaining (days/hours/minutes), an impact "
        "level, and the next few upcoming events for context. Data is a static, pre-loaded "
        "2026 calendar sourced from official Federal Reserve and BLS release schedules - no "
        "live external API call is made, so this endpoint is fast and never fails on an "
        "upstream outage. No input parameters required."
    ),
    responses={
        200: {"model": MacroDdayResponse, "description": "매크로 이벤트 D-Day 캘린더 데이터"},
        402: {"description": "x402 결제 필요"},
        500: {"model": ErrorResponse, "description": "내부 처리 오류"},
    },
)
async def macro_dday_endpoint():
    try:
        data = await get_macro_calendar_dday()
        return JSONResponse(content=data)
    except Exception as e:
        logger.exception("macro-dday 처리 실패")
        return JSONResponse(status_code=500, content={"error": "internal_error", "message": str(e)})

'''

new_main = must_replace(
    new_main,
    'if __name__ == "__main__":',
    MACRO_ENDPOINT_FUNC.lstrip("\n") + '\nif __name__ == "__main__":',
    label="main.py macro_dday_endpoint 삽입 위치",
)

# ---------------------------------------------------------------------------
# 6. 전부 검증 통과 - 이제 실제로 씀
# ---------------------------------------------------------------------------
write(FILES["config"], new_config)
write(FILES["schemas"], new_schemas)
write(FILES["logic"], new_logic)
write(FILES["payment"], new_payment)
write(FILES["main"], new_main)

print("완료: 5개 파일 모두 패치했습니다.")
print("  - app/config.py : PRICE_MACRO_DDAY_USDC 추가")
print("  - app/schemas.py: MacroDdayResponse / MACRO_DDAY_EXAMPLE 추가")
print("  - app/logic.py  : get_macro_calendar_dday (2026 FOMC/CPI/NFP 정적 캘린더) 추가")
print("  - app/payment.py: GET /v1/calendar/macro-dday 라우트 등록 + 기존 6개 라우트 설명에 'Paid in USDC on Base.' 보강")
print("  - main.py       : GET /v1/calendar/macro-dday 엔드포인트 추가")
print("이제 'git diff'로 5개 파일 변경사항을 확인해주세요.")
