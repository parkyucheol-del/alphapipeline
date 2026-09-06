"""
AlphaPipeline - app/logic.py 안의 notice/message 필드 19곳을 전부 영어로
교체하는 패치.

## 왜 필요한가
외부 개발자 리뷰: dump-risk의 coverage_notice가 한국어라 영어 기반 AI
에이전트가 못 읽는다는 지적. 실제 app/logic.py를 열어보니 같은 문제가
coverage_notice 하나가 아니라 7개 엔드포인트 전반의 notice/message 필드
19곳에 똑같이 있었음 - 이번에 한 번에 전부 영문화한다.

## 번역 방식
완전 영문(영한 병기 아님) - 이 필드들은 사람이 브라우저로 보는 게 아니라
LLM 에이전트가 응답 JSON을 파싱해서 조건 분기하는 용도라서, 한국어를
같이 넣으면 토큰만 낭비되고 파싱 리스크만 생긴다는 판단(GET /mcp의 405
응답과는 다른 카테고리 - 그건 사람이 브라우저로 찔러볼 수도 있어서 병기함).

## 스키마 영향 없음
전부 "문자열 내용"만 바꾸는 거고 필드 타입(string)이나 구조는 전혀 안
건드림 - Smithery outputSchema/annotations 인증(98점)에 영향 없음.

## 검증
- 19개 앵커 전부 원본 app/logic.py에서 정확히 1번씩만 매치하는 것을
  사전에 mock 파일로 확인함.
- 패치 적용 후 python -m py_compile 통과 확인함.
- 남은 한국어 notice/message 라인이 없는지 재검색해서 빠짐없이 다
  치환됐는지 확인함.
- 이 스크립트를 두 번 실행하면 이미 적용됐다고 보고 중단함(중복 적용 방지).
"""
import sys
from pathlib import Path

LOGIC_PY = Path("app/logic.py")

# (설명, OLD, NEW) - 순서대로 하나씩 적용
PATCHES = [
    (
        "DropsTab 키 없음 notice (dump-risk)",
        '''            "notice": (
                "DROPSTAB_API_KEY가 설정되지 않아 락업 데이터를 가져오지 않았습니다. "
                "README의 '락업 데이터 소스 변경 이력'을 참고해 DropsTab API 키를 발급받아 .env에 넣어주세요."
            ),''',
        '''            "notice": (
                "DROPSTAB_API_KEY is not set, so unlock data was not fetched. "
                "See the README's 'Unlock Data Source Change History' section to get a "
                "DropsTab API key and add it to .env."
            ),''',
    ),
    (
        "coverage_notice (dump-risk, 온체인 경로)",
        '''        "coverage_notice": (
            "이 데이터는 Sablier 프로토콜로 온체인 베스팅되는 물량만 스캔한 결과입니다. "
            "다른 방식(커스텀 컨트랙트, 거래소 자체 락업 등)의 락업은 포함되지 않으므로, "
            "이 목록에 없다고 해서 해당 토큰에 락업이 없다는 뜻은 아닙니다. "
            "또한 정확한 해제 시점(D-day)은 아직 제공하지 않으며, '현재 잠겨있는 물량 "
            "비율'만 계산합니다 (timing_precision=pending_schema_verification)."
        ),''',
        '''        "coverage_notice": (
            "This data only covers supply vested on-chain via the Sablier protocol. "
            "Lockups through other mechanisms (custom vesting contracts, exchange-side "
            "lockups, etc.) are not covered, so a token's absence from this list does "
            "not mean it has no lockup. Exact unlock timing (D-day) is not yet "
            "provided; this only computes the 'currently locked' supply ratio "
            "(timing_precision=pending_schema_verification)."
        ),''',
    ),
    (
        "symbol_not_mapped message (dump-risk 심볼별)",
        '''            "message": (
                f"{symbol}에 대한 CoinGecko 매핑이 없어 컨트랙트 주소/유통량을 조회할 수 없습니다. "
                "app/data_sources.py의 _COINGECKO_IDS에 추가하면 지원됩니다."
            ),
        }''',
        '''            "message": (
                f"No CoinGecko mapping exists for {symbol}, so its contract address/"
                "circulating supply could not be looked up. Add it to _COINGECKO_IDS "
                "in app/data_sources.py to support it."
            ),
        }''',
    ),
    (
        "CoinGecko 조회 실패 (dump-risk 심볼별, upstream_error)",
        '            "message": f"CoinGecko 조회 실패: {e}",',
        '            "message": f"CoinGecko lookup failed: {e}",',
    ),
    (
        "insufficient_token_metadata message (dump-risk 심볼별)",
        '            "message": f"{symbol}의 유통량 또는 컨트랙트 주소 정보를 CoinGecko에서 얻지 못했습니다.",',
        '            "message": f"Could not obtain circulating supply or contract address info for {symbol} from CoinGecko.",',
    ),
    (
        "no_onchain_vesting_found message (dump-risk 심볼별)",
        '''            "message": (
                f"{symbol}에 대해 Sablier 프로토콜로 베스팅되는 활성 스트림을 찾지 못했습니다. "
                "이는 '락업이 없다'는 뜻이 아니라 '이 방법으로는 못 찾았다'는 뜻입니다 - "
                "다른 방식(커스텀 컨트랙트, 거래소 자체 락업)의 베스팅은 이 조회로 잡히지 않습니다."
            ),
            "generated_at": _timestamp_now(),''',
        '''            "message": (
                f"No active Sablier vesting streams were found for {symbol}. "
                "This does not mean there is no lockup - it means this method did not "
                "find one. Vesting via other mechanisms (custom vesting contracts, "
                "exchange-side lockups) is not detected by this lookup."
            ),
            "generated_at": _timestamp_now(),''',
    ),
    (
        "invalid_symbol message (dump-risk 심볼별)",
        '''            "message": "symbol 파라미터가 비어 있습니다. 예: 'ATH', 'AO', 'CPOOL'",''',
        '''            "message": "The symbol parameter is empty. Example: 'ATH', 'AO', 'CPOOL'",''',
    ),
    (
        "DropsTab 조회 실패 (dump-risk 심볼별, upstream_error)",
        '''            "message": f"DropsTab 조회 실패: {e}",
        }''',
        '''            "message": f"DropsTab lookup failed: {e}",
        }''',
    ),
    (
        "no_upcoming_unlock message (dump-risk 심볼별)",
        '            "message": f"{symbol}에 대한 예정된 락업 해제 이벤트를 찾지 못했습니다.",',
        '            "message": f"No upcoming unlock events were found for {symbol}.",',
    ),
    (
        "GoPlus/Honeypot.is 둘 다 실패 notice (token-risk)",
        '                "notice": f"GoPlus/Honeypot.is 둘 다 조회 실패: {e2}",',
        '                "notice": f"Both GoPlus and Honeypot.is lookups failed: {e2}",',
    ),
    (
        "Honeypot.is 폴백 notice (token-risk)",
        '        notice = "GoPlus 조회 실패로 Honeypot.is 폴백 데이터를 사용했습니다 (필드 커버리지가 더 좁습니다)."',
        '        notice = "GoPlus lookup failed; using Honeypot.is fallback data (narrower field coverage)."',
    ),
    (
        "base_notice (funding-rate)",
        '''    base_notice = (
        "펀딩비는 다음 정산 시점(next_funding_time)에 적용될 예정 요율입니다. "
        "Bybit/바이낸스 둘 다 이와 별개의 '예측' 필드를 제공하지 않으므로 "
        "predicted_rate는 funding_rate와 동일한 값입니다."
    )''',
        '''    base_notice = (
        "The funding rate is the rate that will apply at the next settlement "
        "(next_funding_time). Neither Bybit nor Binance provides a separate "
        "'predicted' field, so predicted_rate is always the same value as funding_rate."
    )''',
    ),
    (
        "바이낸스 폴백 추가 notice (funding-rate)",
        '''            notice = (
                base_notice
                + " (Bybit 조회 실패로 바이낸스 폴백 데이터를 사용했습니다 - 이 서버의 IP 대역에서 "
                "바이낸스가 451로 차단될 수 있어 이 값도 항상 성공하지는 않습니다.)"
            )''',
        '''            notice = (
                base_notice
                + " (Bybit lookup failed; using Binance fallback data - Binance may "
                "return a 451 block on this server's IP range, so this value does "
                "not always succeed either.)"
            )''',
    ),
    (
        "Bybit/바이낸스 둘 다 실패 notice (funding-rate)",
        '                "notice": f"Bybit/바이낸스 둘 다 조회 실패: {e2}",',
        '                "notice": f"Both Bybit and Binance lookups failed: {e2}",',
    ),
    (
        "_DEX_SLIPPAGE_NOTICE 상수 (dex-liquidity-slippage)",
        '''_DEX_SLIPPAGE_NOTICE = (
    "슬리피지는 GeckoTerminal이 제공하는 풀의 합산 USD 유동성만으로 계산한 근사치입니다 - "
    "이 풀이 표준 constant-product(x*y=k) AMM이고 두 토큰이 50:50 비율로 구성되어 있다고 "
    "가정합니다. Uniswap v3류 집중 유동성 풀이나 스테이블스왑 풀에서는 실제 슬리피지와 "
    "차이가 클 수 있습니다 - 실제 매매 전 온체인 견적(quote)으로 반드시 재확인하세요."
)''',
        '''_DEX_SLIPPAGE_NOTICE = (
    "Slippage is an approximation computed only from the pool's aggregate USD "
    "liquidity as reported by GeckoTerminal - it assumes the pool is a standard "
    "constant-product (x*y=k) AMM with the two tokens in a 50:50 ratio. Actual "
    "slippage can differ significantly for Uniswap v3-style concentrated-liquidity "
    "pools or stableswap pools - always re-verify with an on-chain quote before "
    "trading."
)''',
    ),
    (
        "풀 못찾음 notice (dex-liquidity-slippage)",
        '''                "notice": f"GeckoTerminal에서 {network}의 {token_address} 토큰에 연결된 풀을 찾지 못했습니다.",''',
        '''                "notice": f"No pool linked to token {token_address} on {network} was found on GeckoTerminal.",''',
    ),
    (
        "유동성 데이터 없음 notice (dex-liquidity-slippage)",
        '''            "notice": _DEX_SLIPPAGE_NOTICE + " (이 풀의 유동성 데이터를 확인할 수 없어 슬리피지를 계산하지 못했습니다.)",''',
        '''            "notice": _DEX_SLIPPAGE_NOTICE + " (Could not compute slippage because this pool's liquidity data is unavailable.)",''',
    ),
    (
        "_MACRO_CALENDAR_NOTICE 상수 (macro-dday)",
        '''_MACRO_CALENDAR_NOTICE = (
    "이 캘린더는 2026년 FOMC 금리 결정, 미국 CPI, 미국 고용지표(NFP) 일정을 공식 "
    "연준(Fed)/BLS 발표 기준으로 정적으로 내장한 것입니다 - 실시간 외부 API를 호출하지 "
    "않습니다. 일정은 연준/BLS가 추후 변경할 수 있고 2027년 일정은 아직 포함되어 있지 "
    "않으니, 중요한 의사결정 전에는 공식 소스(federalreserve.gov, bls.gov)로 재확인하세요."
)''',
        '''_MACRO_CALENDAR_NOTICE = (
    "This calendar statically embeds the 2026 FOMC rate-decision, US CPI, and US "
    "employment (NFP) schedule based on official Fed/BLS releases - it makes no "
    "live external API calls. The Fed/BLS may still change these dates, and 2027 "
    "dates are not yet included, so re-verify with the official sources "
    "(federalreserve.gov, bls.gov) before any important decision."
)''',
    ),
    (
        "2026 일정 소진 추가 notice (macro-dday)",
        '''            + " 2026년 내장 일정이 모두 지났습니다 - 다음 세션에서 갱신이 필요합니다.",
        }''',
        '''            + " All embedded 2026 events have passed - this calendar needs to be "
            "updated with next year's schedule.",
        }''',
    ),
]


def fail(msg: str) -> None:
    print(f"중단: {msg}")
    print("(아무 파일도 수정되지 않았습니다.)")
    sys.exit(1)


def main() -> None:
    if not LOGIC_PY.exists():
        fail(f"{LOGIC_PY}가 없습니다 - 리포 루트에서 실행해주세요 (app/logic.py가 있어야 함).")

    src = LOGIC_PY.read_text(encoding="utf-8")

    if "DROPSTAB_API_KEY is not set" in src:
        fail("이미 이 패치가 적용된 것 같습니다 - 중복 적용 방지.")

    new_src = src
    for i, (desc, old, new) in enumerate(PATCHES, start=1):
        count = new_src.count(old)
        if count != 1:
            fail(
                f"[{i}/{len(PATCHES)}] '{desc}' 앵커를 1번이 아니라 {count}번 찾았습니다 - "
                "파일이 예상과 다른 상태인 것 같습니다."
            )
        new_src = new_src.replace(old, new, 1)

    try:
        compile(new_src, str(LOGIC_PY), "exec")
    except SyntaxError as e:
        fail(f"생성될 {LOGIC_PY} 내용에 문법 오류가 있습니다 (아무것도 쓰지 않았습니다): {e}")

    LOGIC_PY.write_text(new_src, encoding="utf-8")
    print(f"완료: {LOGIC_PY}의 notice/message 필드 {len(PATCHES)}곳을 전부 영어로 교체했습니다.")
    print("다음 단계:")
    print("  1) python -m py_compile app/logic.py")
    print('  2) git add -A && git commit -m "Translate all notice/message fields to English for agent readability" && git push')
    print("  3) 배포 확인되면 dump-risk / funding-rate / dex-liquidity-slippage 등 실제 호출해서 notice 필드 영어로 나오는지 확인")


if __name__ == "__main__":
    main()
