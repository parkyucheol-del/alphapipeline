"""
전역 설정 로더.
.env 파일에서 지갑 주소, RPC, 요금 등을 읽어온다.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # 결제 수신 지갑 (Base 체인). 공식 x402 SDK 예제의 X402_PAY_TO와 동일한 역할이며,
    # 기존 .env 값을 그대로 재사용하기 위해 변수명은 유지했다.
    RECEIVER_WALLET_ADDRESS: str = os.getenv(
        "RECEIVER_WALLET_ADDRESS", "0xYOUR_WALLET_ADDRESS_HERE"
    )

    # 호출당 가격 (USD 단위, 사람이 읽는 값. 예: 0.01 -> x402 SDK에 "$0.01"로 전달됨.
    # 메인넷에서는 이 달러 가격이 Base의 기본 USDC로 자동 환산되므로 컨트랙트 주소를
    # 직접 지정할 필요가 없다 - CDP Facilitator가 처리)
    # 2026-09 업데이트: 더 이상 전 엔드포인트에 똑같이 쓰이지 않는다 - 아래
    # PRICE_*_USDC로 엔드포인트별 차등 요금을 매기고, 이 값은 신규 엔드포인트
    # 추가 시 임시 기본값/legacy 폴백으로만 남겨둔다 (app/payment.py 참고).
    PRICE_PER_CALL_USDC: float = float(os.getenv("PRICE_PER_CALL_USDC", "0.01"))

    # ===== 엔드포인트별 차등 요금 (Tiered Pricing, 2026-09) =====
    # "데이터 가치 기반 차등 요금제" - 대체하기 쉬운 단순 유틸리티(ai-markdown)는
    # 싸게, 아무 데서나 못 구하는 핵심 알파 데이터(dump-risk)는 비싸게 매겨서
    # 마진 곡선을 데이터의 실제 가치에 맞춘다. 세 값 다 독립적으로 .env에서
    # 조정 가능 - 가격 실험(A/B, 수요 반응 테스트)을 코드 수정 없이 할 수 있다.
    PRICE_KIMCHI_ALERT_USDC: float = float(os.getenv("PRICE_KIMCHI_ALERT_USDC", "0.01"))
    PRICE_AI_MARKDOWN_USDC: float = float(os.getenv("PRICE_AI_MARKDOWN_USDC", "0.005"))
    PRICE_DUMP_RISK_USDC: float = float(os.getenv("PRICE_DUMP_RISK_USDC", "0.03"))
    PRICE_FUNDING_RATE_USDC: float = float(os.getenv("PRICE_FUNDING_RATE_USDC", "0.01"))
    PRICE_DEX_SLIPPAGE_USDC: float = float(os.getenv("PRICE_DEX_SLIPPAGE_USDC", "0.02"))
    PRICE_MACRO_DDAY_USDC: float = float(os.getenv("PRICE_MACRO_DDAY_USDC", "0.01"))
    PRICE_TOKEN_RISK_USDC: float = float(os.getenv("PRICE_TOKEN_RISK_USDC", "0.02"))
    PRICE_ARB_SPREAD_USDC: float = float(os.getenv("PRICE_ARB_SPREAD_USDC", "0.02"))
    PRICE_FUNDING_APR_USDC: float = float(os.getenv("PRICE_FUNDING_APR_USDC", "0.01"))
    PRICE_CONTRACT_HEALTH_USDC: float = float(os.getenv("PRICE_CONTRACT_HEALTH_USDC", "0.02"))
    PRICE_WHALE_AUDIT_USDC: float = float(os.getenv("PRICE_WHALE_AUDIT_USDC", "0.02"))
    PRICE_TOKEN_DIAGNOSTIC_USDC: float = float(os.getenv("PRICE_TOKEN_DIAGNOSTIC_USDC", "0.03"))
    # 2026-10-11 추가: 도그푸딩 피드백(유동성+LP락만 보는 저가 1차 스크리닝 티어)에서
    # 나온 가격대 - token_diagnostic/token_risk/contract_health_audit보다 싸게,
    # 고빈도 자동화 클라이언트가 "이 후보, 더 비싼 전체 검사를 돌릴 가치가 있나"를
    # 먼저 거를 수 있게 한다.
    PRICE_TOKEN_PRESCREEN_USDC: float = float(os.getenv("PRICE_TOKEN_PRESCREEN_USDC", "0.01"))
    # 2026-10-11 추가: "공식 컨트랙트 판별" 기능. GoPlus token_security 응답에
    # 이미 들어있던(미사용) fake_token/trust_list/honeypot_with_same_creator
    # 필드만 노출 - token_risk/contract_health_audit/token_prescreen과 동일한
    # GoPlus 호출 재사용, 추가 업스트림 비용 없음. token_prescreen과 같은
    # 최저 티어로 책정.
    PRICE_TOKEN_AUTHENTICITY_USDC: float = float(os.getenv("PRICE_TOKEN_AUTHENTICITY_USDC", "0.01"))
    # 2026-09 예측시장(Polymarket) 확장 Wave 1. neg_risk_arbitrage는 여러 outcome을
    # 동시에 계산하고 실행 가능성(유동성 병목)까지 판정하는 고부가 시그널이라
    # dump-risk/token-diagnostic과 같은 최고가 티어로, exit_capacity_audit은
    # 단일 오더북 조회라 token-risk/dex-slippage와 같은 중간 티어로 매겼다.
    PRICE_NEG_RISK_ARBITRAGE_USDC: float = float(os.getenv("PRICE_NEG_RISK_ARBITRAGE_USDC", "0.03"))
    PRICE_EXIT_CAPACITY_AUDIT_USDC: float = float(os.getenv("PRICE_EXIT_CAPACITY_AUDIT_USDC", "0.02"))
    # 2026-09-28 예측시장 확장 Wave 2. Hyperliquid HIP-4 무료 info API 두 개를
    # 조합해서 재구조화만 하는 엔드포인트라(자체 오더북 조회/바스켓 계산 없음),
    # funding_rate/macro_dday와 같은 기본 티어로 매겼다.
    PRICE_HIP4_SNAPSHOT_USDC: float = float(os.getenv("PRICE_HIP4_SNAPSHOT_USDC", "0.01"))
    # 2026-10-01 추가. hip4_snapshot이 이미 캐시해둔 동일한 두 무료 API 응답을
    # 재사용해서 재정렬만 하는 파생 뷰라(신규 업스트림 호출 없음), 같은 기본
    # 티어로 매겼다. Daily Alpha 아티팩트 2026-09-29 브리핑의 "BTC 확률 사다리"
    # 아이디어 구현 - "매크로 리스크 점수" 쪽은 의도적으로 제외함(아래
    # get_hip4_price_ladder() docstring 참고 - 정성적 합성 점수는 이미 두 번
    # 반려된 safe_to_execute/confidence 패턴과 같은 리스크라 채택 금지 확정).
    PRICE_HIP4_PRICE_LADDER_USDC: float = float(os.getenv("PRICE_HIP4_PRICE_LADDER_USDC", "0.01"))

    # 2026-10-10 추가. 원래 아이디어("과거 사이클 대비 도미넌스 괴리율")는 무료
    # 소스가 없어서(CoinGecko 과거 글로벌 시총 차트=Analyst 플랜 이상, CoinMetrics
    # Community=인증 필요, CoinGecko Demo 플랜도 과거 데이터 365일 제한, Kraken
    # 공개 OHLC=최근 720개봉 제한) 가격 기반 사이클 비교로 축소함(scripts/
    # collect_btc_cycle_metrics.py 모듈 docstring 참고). 매일 1회 크론이 BTC/ETH
    # 전체 가격 히스토리(Yahoo Finance)를 받아 반감기 사이클 비교를 미리 계산해
    # data-public/btc_cycle_metrics_cache.json에 캐시해두고, 이 엔드포인트는 그
    # 파일만 읽는다(요청 시 신규 업스트림 호출 없음) - hip4_price_ladder와 같은
    # "파생 재사용" 패턴이라 같은 기본 티어로 매겼다. 검증 안 된 시황 계열(
    # kimchi-alert/dump-risk와 같은 분류)이라 기대치는 낮게 잡을 것.
    PRICE_CYCLE_COMPARE_USDC: float = float(os.getenv("PRICE_CYCLE_COMPARE_USDC", "0.01"))

    # ===== HIP-4 확률 급변 웹훅 이벤트 (2026-09-30 추가, EXPERIMENTAL) =====
    # app/events.py 모듈 docstring 참고. 기본값 False - dump-risk/kimchi-alert와
    # 같은 패턴으로, 실제 webhook.site 등으로 직접 동작을 검증하기 전까지는
    # 스케줄러 폴링 job 자체를 등록하지 않는다(서버 기동/기존 기능에는 전혀
    # 영향 없음). 검증되면 true로 바꾼다.
    HIP4_EVENTS_ENABLED: bool = _get_bool("HIP4_EVENTS_ENABLED", False)
    # Hyperliquid HIP-4 무료 info API를 다시 조회하는 주기(초). 실시간 푸시가
    # 아니라 폴링이라, 이 값보다 짧게 일어난 확률 급변은 다음 주기에나 감지된다.
    # 오늘 데일리 알파 액션 카드의 30분 스파이크 스크립트와 동일하게 기본 300초(5분).
    HIP4_EVENTS_POLL_INTERVAL_SECONDS: int = int(os.getenv("HIP4_EVENTS_POLL_INTERVAL_SECONDS", "300"))
    # 확률(0.0-1.0 스케일)이 이 값 이상 움직이면 hip4.prob_jump 이벤트로 친다.
    # 기본 0.05 = 5%p, 액션 카드 스크립트와 동일한 기본 임계값. 구독 생성 시
    # threshold_pct로 구독별로 덮어쓸 수 있다.
    HIP4_EVENTS_JUMP_THRESHOLD_PCT: float = float(os.getenv("HIP4_EVENTS_JUMP_THRESHOLD_PCT", "0.05"))
    # 구독 유효기간(시간). 만료되면 자동으로 무효화 - 갱신은 v1에서는 재구독(새
    # subscription_id 발급)으로만 가능하다 (app/events.py 모듈 docstring 참고).
    HIP4_EVENTS_SUBSCRIPTION_TTL_HOURS: int = int(os.getenv("HIP4_EVENTS_SUBSCRIPTION_TTL_HOURS", "24"))
    # 동시 활성 구독 수 상한(메모리/디스크 보호용 안전장치).
    HIP4_EVENTS_MAX_SUBSCRIPTIONS: int = int(os.getenv("HIP4_EVENTS_MAX_SUBSCRIPTIONS", "200"))
    # 웹훅 전달이 이 횟수만큼 연속 실패하면(엔드포인트가 죽었다고 보고) 그
    # 구독을 자동으로 비활성화한다 - 죽은 엔드포인트에 계속 재시도하는 걸 방지.
    HIP4_EVENTS_MAX_CONSECUTIVE_FAILURES: int = int(os.getenv("HIP4_EVENTS_MAX_CONSECUTIVE_FAILURES", "5"))
    # 구독 생성(GET /v1/prediction/hip4-alerts/subscribe) 1건당 가격.
    # 단건 스냅샷 조회(PRICE_HIP4_SNAPSHOT_USDC=0.01)보다 비싸게 잡았다 - 구독은
    # 서버가 HIP4_EVENTS_SUBSCRIPTION_TTL_HOURS 동안 폴링 리소스를 계속
    # 써주는 대가라서다.
    PRICE_HIP4_EVENTS_SUBSCRIBE_USDC: float = float(os.getenv("PRICE_HIP4_EVENTS_SUBSCRIBE_USDC", "0.05"))

    # ===== HIP-4 이벤트 Clef-flash 노이즈 필터 (2026-10-12 추가, EXPERIMENTAL) =====
    # app/events.py의 poll_once()가 hip4.prob_jump 후보를 웹훅으로 보내기 전에,
    # Cloudflare Workers AI의 clef-flash 결정 모델에 "이게 진짜 신호인가"를
    # 한 번 더 물어보는 선택적 필터. HIP4_EVENTS_ENABLED와 별개 플래그다 - 이벤트
    # 기능 자체는 켜두고 필터만 끌 수도 있다(기본값). 끄면 이 필터는 전혀
    # 호출되지 않고 기존 동작(임계값만 넘으면 바로 발송) 그대로다.
    HIP4_EVENTS_CLEF_FILTER_ENABLED: bool = _get_bool("HIP4_EVENTS_CLEF_FILTER_ENABLED", False)
    # signal(noul) 확률이 이 값 미만이면 노이즈로 보고 웹훅 발송을 건너뛴다.
    # 2026-10-12 수동 테스트(합성 시나리오 6개, Haiku 5.5 대비 5/6 일치)에서
    # 나온 대략적인 값 - 실거래량 데이터 없이 운영되므로(Hyperliquid HIP-4 공개
    # API에 거래량 필드 자체가 없음) 실측 분포를 보고 재조정이 필요할 수 있다.
    HIP4_EVENTS_CLEF_SIGNAL_THRESHOLD: float = float(os.getenv("HIP4_EVENTS_CLEF_SIGNAL_THRESHOLD", "0.3"))
    # Cloudflare 호출이 실패/타임아웃되면 필터 없이 그대로 발송한다(fail-open).
    # 외부 서비스 장애 때문에 진짜 알림을 조용히 누락시키지 않기 위한 의도적 선택 -
    # 이 필터는 "있으면 노이즈를 줄여주는 보너스"지 "없으면 알림이 안 나가는
    # 필수 게이트"가 아니어야 한다.
    # Cloudflare Workers AI 계정 정보. clef-flash 호출에만 쓰인다. 비어있으면
    # HIP4_EVENTS_CLEF_FILTER_ENABLED=true여도 필터를 건너뛰고 경고 로그만 남긴다
    # (fail-open과 같은 원칙 - 설정 누락도 "장애"로 취급).
    CLOUDFLARE_ACCOUNT_ID: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    CLOUDFLARE_API_TOKEN: str = os.getenv("CLOUDFLARE_API_TOKEN", "")

    # ===== market.oracle_gap (2026-10-13 추가) =====
    # 2026-10-10 Papertrade 사건(HyperEVM 신생 DEX가 Hyperliquid 호가 중간값을
    # 그대로 기준가로 썼다가 약 128만 달러 털림) 계기. Hyperliquid 공식
    # metaAndAssetCtxs에서 mid/mark/oracle 가격을 가져와 mid가 oracle에서 몇
    # bp 벌어졌는지만 순수 계산해서 반환한다 - 데일리 알파 카드가 제안한
    # flow_skew(1분 순체결 쏠림)와 risk_score(복합 점수)는 의도적으로 뺐다:
    # flow_skew는 스푸핑 감지 때와 똑같이 이 프로젝트가 쓰지 않는 시계열 폴러
    # 인프라가 필요하고, risk_score는 "검증 안 된 복합/정성 판단 필드를 만들지
    # 않는다"는, 이미 두 번 반려된 원칙(claude/pending-backlog-2026-09-10.md)과
    # 정면으로 충돌한다. elevated는 risk_score와 달리 "하나의 실측 숫자가
    # 고정 임계값을 넘었는가"라는 결정론적 불리언이라 token_authenticity의
    # flagged_as_counterfeit과 같은 성격이라 원칙과 안 부딪힌다.
    ORACLE_GAP_CACHE_TTL_SECONDS: int = int(os.getenv("ORACLE_GAP_CACHE_TTL_SECONDS", "3"))
    # mid-oracle 괴리(bp)가 이 값 이상이면 elevated=true. 기본 5bp는 오늘 액션
    # 카드 스크립트의 ALERT 임계값을 그대로 가져온 잠정값 - 실측 로그(오늘 10분
    # 측정) 분포를 보고 재조정 예정.
    ORACLE_GAP_ALERT_THRESHOLD_BP: float = float(os.getenv("ORACLE_GAP_ALERT_THRESHOLD_BP", "5.0"))
    PRICE_ORACLE_GAP_USDC: float = float(os.getenv("PRICE_ORACLE_GAP_USDC", "0.01"))

    # ===== 2026-10-18 저가 티어 실험 (cheap single-number endpoints) =====
    # 데일리 알파 10/15-10/16 카드 근거: Bazaar 30일 데이터에서 지불자 10명
    # 이상인 엔드포인트 420개(전체 34,062개 중)가 거의 전부 $0.001~0.005짜리
    # "숫자 하나" 체인/시장 조회형이었다. 지금 AlphaPipeline 유료 엔드포인트는
    # 거의 전부 $0.01~0.03이라 이 가격대가 비어있었다(10/18 실측: 외부 지불자
    # 12명/6주 - claude/session-log-2026-10-18-real-paying-wallets-found.md).
    # 아래 3개는 기존 funding-rate/oracle-gap/hip4-price-ladder가 이미 캐시해둔
    # 데이터를 그대로 재사용하는 얇은 wrapper - 신규 업스트림 호출이 없다
    # (app/logic.py의 get_funding_rate_value/get_oracle_gap_bp/get_hip4_price
    # 참고). 기존 리치 엔드포인트 가격은 그대로 둔다 - 이건 추가 상품이지 대체가
    # 아니다.
    PRICE_FUNDING_RATE_VALUE_USDC: float = float(os.getenv("PRICE_FUNDING_RATE_VALUE_USDC", "0.001"))
    PRICE_ORACLE_GAP_BP_USDC: float = float(os.getenv("PRICE_ORACLE_GAP_BP_USDC", "0.001"))
    PRICE_HIP4_PRICE_USDC: float = float(os.getenv("PRICE_HIP4_PRICE_USDC", "0.002"))

    # ===== x402 공식 결제 레이어 (Coinbase CDP Facilitator) =====
    # Coinbase Developer Platform(https://portal.cdp.coinbase.com)에서 발급받는 API 키.
    # 둘 다 채워지면 CDP Facilitator(메인넷 실결제 검증/정산)를 쓰고,
    # 비어있으면 공개 테스트넷 파실리테이터(x402.org/facilitator)로 자동 대체된다
    # (이 상태에서는 실제 메인넷 USDC가 정산되지 않으니 실서비스 전에 반드시 채워야 함).
    CDP_API_KEY_ID: str = os.getenv("CDP_API_KEY_ID", "")
    CDP_API_KEY_SECRET: str = os.getenv("CDP_API_KEY_SECRET", "")

    # CAIP-2 네트워크 식별자. 기본값은 Base 메인넷(eip155:8453).
    # CDP 키가 없어서 테스트넷 파실리테이터로 대체되는 경우, 이 값과 무관하게
    # Base Sepolia(eip155:84532)로 강제 전환된다 (app/payment.py 참고).
    X402_NETWORK: str = os.getenv("X402_NETWORK", "eip155:8453")

    # 이 서비스가 실제로 서빙되는 공개 절대 URL (프로토콜+도메인, 끝에 / 없이).
    # x402 Bazaar 인덱서는 각 라우트의 절대 URL(RouteConfig.resource)을 요구하고
    # 상대경로는 등록 실패 사유로 명시되어 있어서, Render 배포 주소를 여기 넣는다.
    # (app/payment.py의 build_routes()가 이 값 + 각 라우트 경로로 절대 URL을 만든다)
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "https://alphapipeline.onrender.com")

    # 김치프리미엄/시세 캐시 TTL(초) - 짧을수록 실시간성은 올라가지만
    # 업비트/코인베이스/CoinGecko API 호출 빈도가 늘어남.
    # 2026-09 업데이트: 기존 2초에서 30초로 늘렸다 - 봇들이 초 단위로 같은 심볼을
    # 반복 결제 호출할 때, x402 결제는 요청마다 그대로 징수되면서(캐시와 무관)
    # 외부 API 호출만 캐시 히트만큼 아낄 수 있어 원가가 0에 수렴한다("순마진 100%
    # 방어" - app/cache.py의 ttl_cached() 참고). 김치프리미엄은 30초 안에 급변하는
    # 지표가 아니라 실시간성 손실도 체감상 미미하다.
    KIMCHI_CACHE_TTL_SECONDS: int = int(os.getenv("KIMCHI_CACHE_TTL_SECONDS", "30"))

    # GoPlus token_security 캐시 TTL(초). security.token_risk / contract_health_audit /
    # token_diagnostic 세 유료 엔드포인트가 전부 같은 GoPlus 무료 쿼터를 공유하므로,
    # 같은 (chain_id, contract_address) 조합이 짧은 시간 안에 반복 조회되면(봇의
    # 재시도, token_diagnostic의 내부 asyncio.gather 등) 캐시로 흡수해서 무료
    # 쿼터 소진/레이트리밋으로 인한 502를 방지한다. x402 결제는 캐시 히트와
    # 무관하게 매 호출 그대로 징수되므로 마진에는 영향 없다(app/cache.py 참고).
    GOPLUS_CACHE_TTL_SECONDS: int = int(os.getenv("GOPLUS_CACHE_TTL_SECONDS", "120"))

    # Polymarket CLOB 오더북 캐시 TTL(초). 짧게 잡아서 neg_risk_arbitrage가 여러
    # outcome 레그를 병렬 조회할 때/exit_capacity_audit이 직후 같은 토큰을 다시
    # 조회할 때만 중복 호출을 흡수하고, 오더북 실시간성은 거의 그대로 유지한다.
    POLYMARKET_BOOK_CACHE_TTL_SECONDS: int = int(os.getenv("POLYMARKET_BOOK_CACHE_TTL_SECONDS", "3"))

    # Hyperliquid HIP-4 outcomeMeta/allMids 캐시 TTL(초, 2026-09-28). allMids는
    # 실시간 가격이라 너무 길면 신선도를 잃고, 너무 짧으면 Hyperliquid 무료 API를
    # 불필요하게 자주 때린다 - 짧게 잡아 실시간성 위주로 절충.
    HIP4_CACHE_TTL_SECONDS: int = int(os.getenv("HIP4_CACHE_TTL_SECONDS", "15"))

    # 락업 해제(dump-risk) 스캔 주기(시간). 언락 일정은 몇 주 전에 미리 확정되는
    # 경우가 대부분이라 실시간일 필요가 없음 - 기본값 24시간(하루 1회)이 이미
    # "필요한 최소" 수준이며, 필요하면 이 값만 늘려서 더 낮출 수 있음.
    UNLOCK_REFRESH_INTERVAL_HOURS: int = int(os.getenv("UNLOCK_REFRESH_INTERVAL_HOURS", "24"))

    # dump-risk 엔드포인트를 실제로 서빙할지 여부. DropsTab 유료 플랜(월 $59~)을 결제하기 전까지는
    # False로 두고 나머지 두 무료-원가 엔드포인트(kimchi-alert, ai-markdown)만으로 먼저 출시해서
    # 실제 수요/매출을 검증한 뒤, 검증되면 이 값을 true로 바꾸고 DROPSTAB_API_KEY를 넣어 켠다.
    DUMP_RISK_ENABLED: bool = _get_bool("DUMP_RISK_ENABLED", False)

    # kimchi-alert 엔드포인트에 x402 과금을 적용할지 여부 (2026-09-19 추가).
    # 제미나이 PMF 진단(claude/gemini-pmf-diagnosis-2026-09-19.md) 결과 kimchi-alert가
    # 도그푸딩 봇의 실거래 전략으로 단 한 번도 전환되지 못했다는 실사용 신호가 확인되어,
    # 유료 핵심 자산(보안 3종)에 포지셔닝을 집중하기 위해 dump-risk와 동일한 패턴으로
    # 기본값 False(무료 온보딩 엔드포인트)로 전환. true로 바꾸면 과금이 부활한다.
    KIMCHI_ALERT_ENABLED: bool = _get_bool("KIMCHI_ALERT_ENABLED", False)

    # DropsTab API 키 (락업 해제 데이터 소스). Builders Program(무료, 승인제) 신청하거나
    # 유료 Advanced 플랜(월 $59~, Basic $19에는 tokenUnlocks 미포함)으로 발급받아 넣는다. 없으면 dump-risk 엔드포인트는
    # "데이터 소스 미설정" 상태로 빈 결과를 반환한다.
    DROPSTAB_API_KEY: str = os.getenv("DROPSTAB_API_KEY", "")
    DROPSTAB_API_BASE_URL: str = os.getenv("DROPSTAB_API_BASE_URL", "https://public-api.dropstab.com/api/v1")

    # 락업 스캔 시 한 페이지에 몇 개씩, 최대 몇 페이지까지 가져올지
    # (전체 코인 수가 많을 수 있어 과금/시간 보호용으로 상한을 둠)
    DROPSTAB_PAGE_SIZE: int = int(os.getenv("DROPSTAB_PAGE_SIZE", "100"))
    MAX_UNLOCK_SCAN_PAGES: int = int(os.getenv("MAX_UNLOCK_SCAN_PAGES", "20"))

    # 테스트용 결제 우회 스위치 (실서비스에서는 반드시 False로)
    PAYMENT_BYPASS_FOR_TESTING: bool = _get_bool(
        "PAYMENT_BYPASS_FOR_TESTING", True
    )

    PORT: int = int(os.getenv("PORT", "8000"))

    # --- 무료 CLI 프리뷰 (check-my-slippage) ---
    # exit_capacity_audit을 결제 없이 벤치마크 마켓 1개로 고정 노출 - 어뷰징 표면을
    # 줄이기 위해 임의 마켓 지정은 허용하지 않는다. build_routes()에 등록하지 않아야
    # PaymentMiddlewareASGI 게이트를 안 타고 무료로 열린다 (main.py 참고).
    # 2026-09-18: 이전 하드코딩 값("will-btc-hit-150k-by-2028")이 실제 폴리마켓에
    # 존재하지 않아 이 무료 프리뷰가 502로 계속 실패하던 걸 발견/수정함. 아래 값은
    # 2026-09-18 기준 실제 활성 마켓(연준 10월 금리 결정, 만기 2026-10-28)으로
    # 교체한 것 - 만기가 지나면 다시 깨지니, 그 전에 다른 장기 활성 마켓으로
    # 갱신 필요 (Render 환경변수 SLIPPAGE_PREVIEW_MARKET_SLUG로 코드 배포 없이도
    # 바로 덮어쓸 수 있음 - env가 우선 적용됨).
    SLIPPAGE_PREVIEW_MARKET_SLUG: str = os.getenv(
        "SLIPPAGE_PREVIEW_MARKET_SLUG",
        "will-the-fed-decrease-interest-rates-by-25-bps-after-the-october-2026-meeting-20260617190324030",
    )
    SLIPPAGE_PREVIEW_OUTCOME: str = os.getenv("SLIPPAGE_PREVIEW_OUTCOME", "yes")
    SLIPPAGE_PREVIEW_POSITION_SIZE_SHARES: float = float(
        os.getenv("SLIPPAGE_PREVIEW_POSITION_SIZE_SHARES", "500")
    )
    SLIPPAGE_PREVIEW_SIDE: str = os.getenv("SLIPPAGE_PREVIEW_SIDE", "sell")
    SLIPPAGE_PREVIEW_RATE_LIMIT_PER_MINUTE: int = int(
        os.getenv("SLIPPAGE_PREVIEW_RATE_LIMIT_PER_MINUTE", "10")
    )

    def is_wallet_configured(self) -> bool:
        addr = self.RECEIVER_WALLET_ADDRESS or ""
        return addr.startswith("0x") and len(addr) == 42 and "YOUR_WALLET" not in addr


settings = Settings()
