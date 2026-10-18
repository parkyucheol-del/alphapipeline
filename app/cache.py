"""
간단한 인메모리 캐시.
- unlock_cache: 하루 1회 스케줄러가 갱신 (락업 해제 데이터는 자주 안 바뀜)
- price_cache: 초 단위 TTL로 실시간성 유지하면서 API 폭주 방지
- markdown_cache: 같은 URL 반복 호출 시 원본 페이지를 다시 안 긁어오도록 함

(이전에는 결제 tx_hash 재사용 방지용 tx_seen_cache도 여기 있었는데, 공식 x402
Facilitator로 결제 검증을 이전하면서 재생 공격 방지는 Facilitator/EIP-3009
서명 체계가 대신 처리하게 되어 제거했다 - app/payment.py 참고.)
"""
import functools
import time
from collections import OrderedDict
from typing import Callable

from cachetools import TTLCache
from app.config import settings


class VariableTTLCache:
    """
    2026-10-18 Engineering Precision Backlog 2 "가변형 TTL 캐시 엔진"용 경량 캐시.

    cachetools.TTLCache는 인스턴스 전체에 고정된 ttl 하나만 지원한다(생성 시
    박아넣은 값, 항목별로 다르게 줄 수 없음) - 그래서 "시장 변동성/호가 갱신
    빈도에 따라 항목마다 TTL을 1~5초 사이에서 다르게" 주는 용도로는 못 쓴다.
    이 클래스는 그 제약만 없앤 아주 단순한 대체재 - (값, 만료시각) 튜플을
    그대로 들고 있다가 get() 시점에 만료 여부를 직접 비교한다.

    ttl_cached()의 ttl_fn 파라미터와 함께 쓰는 용도로 설계됨 - 단독으로는
    쓰지 않는다 (app/data_sources.py의 polymarket_book_cache 참고).

    maxsize 초과 시 가장 먼저 삽입된 항목을 버리는 단순 근사 FIFO/LRU
    (조회 시점에 순서를 갱신하지 않는 순수 FIFO - maxsize가 작고(256) TTL도
    1~5초로 짧아서 완벽한 LRU일 필요가 없다고 판단, cachetools.TTLCache도
    내부적으로 같은 수준의 근사를 쓴다).
    """

    def __init__(self, maxsize: int):
        self.maxsize = maxsize
        self._store: OrderedDict[str, tuple[object, float]] = OrderedDict()

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: object, ttl: float) -> None:
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self.maxsize:
            self._store.popitem(last=False)
        self._store[key] = (value, time.monotonic() + ttl)


# 락업 해제(dump-risk) 데이터: 스케줄러가 UNLOCK_REFRESH_INTERVAL_HOURS 주기로 갱신.
# TTL은 그 주기보다 넉넉하게 잡아서(주기+2시간) 스케줄러가 한 번 실패해도
# 캐시가 비어버리지 않도록 함.
unlock_cache: TTLCache = TTLCache(
    maxsize=8, ttl=60 * 60 * (settings.UNLOCK_REFRESH_INTERVAL_HOURS + 2)
)

# 김프/시세 데이터: 짧은 TTL로 실시간성 유지 + 업비트/바이낸스 레이트리밋 보호
price_cache: TTLCache = TTLCache(maxsize=64, ttl=settings.KIMCHI_CACHE_TTL_SECONDS)

# ai-markdown 변환 결과: 같은 URL 반복 호출 시 10분 캐시
markdown_cache: TTLCache = TTLCache(maxsize=256, ttl=600)

# GoPlus token_security 원본 응답: security.token_risk/contract_health_audit/
# token_diagnostic 3개 엔드포인트가 공유하는 캐시. 같은 컨트랙트 주소를 반복
# 조회할 때 GoPlus 무료 쿼터 소진을 막는 게 목적(2026-09, "업스트림 무료
# 쿼터 보호" 요청으로 추가) - 짧은 TTL이라 실시간성 손실은 미미함.
goplus_cache: TTLCache = TTLCache(maxsize=256, ttl=settings.GOPLUS_CACHE_TTL_SECONDS)

# Polymarket CLOB 오더북: prediction.neg_risk_arbitrage가 neg-risk 그룹의 여러
# outcome 레그를 한 번에 조회할 때, 그리고 prediction.exit_capacity_audit이
# 직후 같은 토큰을 다시 조회할 때 중복 호출을 흡수해서 Polymarket CLOB의 IP
# 레이트리밋(429)을 방어한다(2026-09, 배포 전 외부 리뷰 피드백 반영).
#
# 2026-10-18 Engineering Precision Backlog 2 "가변형 TTL 캐시 엔진"로 고정
# 3초 TTL에서 VariableTTLCache로 교체 - 레이트리밋 방어와 실시간성 사이의
# 절충점을 매번 "3초"라는 하나의 숫자로 뭉개는 대신, 시장이 조용할 때는
# POLYMARKET_BOOK_CACHE_MAX_TTL_SECONDS(기본 5초)까지 늘려 레이트리밋 방어를
# 더 강하게, 시장이 움직이는 중이거나 호가가 막 갱신됐을 때는
# POLYMARKET_BOOK_CACHE_MIN_TTL_SECONDS(기본 1초)로 줄여 실시간성을 지킨다
# (실제 계산은 app/data_sources.py의 _polymarket_book_ttl_fn 참고). x402
# 결제는 캐시 히트와 무관하게 매 호출 그대로 징수되므로 마진에는 영향
# 없다(위 ttl_cached() 설명 참고).
polymarket_book_cache: VariableTTLCache = VariableTTLCache(maxsize=256)

# Polymarket CLOB fee-rate(GET /fee-rate) 원본 응답 캐시. prediction.
# polymarket_fee_estimate가 쓴다(2026-10-18, Engineering Precision Backlog 3).
# 오더북과 달리 수수료율은 초 단위로 변하는 데이터가 아니라서(공식 문서/제3자
# 기사 기준 카테고리별 수수료율이 여러 차례 바뀐 적은 있지만 그 주기는 일/주
# 단위) 오더북 캐시처럼 짧게 잡을 필요가 없다 - 적당히 넉넉한 고정 TTL로 같은
# token_id 반복 조회 시
# Polymarket CLOB 레이트리밋을 방어한다. x402 결제는 캐시 히트와 무관하게 매
# 호출 그대로 징수되므로 마진에는 영향 없다(위 ttl_cached() 설명 참고).
polymarket_fee_rate_cache: TTLCache = TTLCache(
    maxsize=256, ttl=settings.POLYMARKET_FEE_RATE_CACHE_TTL_SECONDS
)

# Hyperliquid HIP-4 outcomeMeta/allMids 원본 응답 캐시. prediction.hip4_snapshot이
# 매 호출마다 두 API를 새로 때리지 않도록 짧은 TTL로 흡수한다(2026-09-28) - allMids는
# 실시간성이 중요해서 TTL을 짧게 잡았다. x402 결제는 캐시 히트와 무관하게 매 호출
# 그대로 징수되므로 마진에는 영향 없다(위 ttl_cached() 설명 참고).
hip4_cache: TTLCache = TTLCache(maxsize=4, ttl=settings.HIP4_CACHE_TTL_SECONDS)

# Hyperliquid 무기한선물(perp) metaAndAssetCtxs 원본 응답(mid/mark/oracle 가격
# 전체 유니버스) 캐시. market.oracle_gap이 쓴다(2026-10-13). 짧은 TTL인 이유는
# polymarket_book_cache와 같다 - 이 데이터는 실시간성이 핵심이라 TTL이 길면
# 이미 지나간 괴리를 보여줄 위험이 있다. x402 결제는 캐시 히트와 무관하게 매
# 호출 그대로 징수되므로 마진에는 영향 없다(ttl_cached() 설명 참고).
oracle_gap_cache: TTLCache = TTLCache(maxsize=4, ttl=settings.ORACLE_GAP_CACHE_TTL_SECONDS)


def ttl_cached(
    cache: TTLCache | VariableTTLCache,
    key_fn: Callable[..., str] | None = None,
    ttl_fn: Callable[[str, dict], float] | None = None,
):
    """
    비동기 함수 결과를 TTLCache에 저장하는 범용 데코레이터 (2026-09, "고마진 방어를
    위한 캐싱" 요청으로 추가). get_kimchi_alert()에 적용된 게 첫 사용례이고,
    앞으로 추가될 엔드포인트(예: funding-rate)도 이 데코레이터를 그대로 재사용하면
    된다 - 캐시 로직을 매번 손으로 다시 안 짜도 됨.

    ## 왜 이게 "순마진 100% 방어"인가
    x402 결제는 PaymentMiddlewareASGI가 HTTP 요청 하나하나마다 독립적으로
    징수한다(app/payment.py) - 캐시 히트/미스와 무관하게 호출자는 매번 똑같이
    과금된다. 캐시가 절약하는 건 오직 "우리 쪽 원가"(외부 API 호출)뿐이다.
    그래서 봇이 1초 간격으로 같은 심볼을 두들겨도: 매출은 호출 횟수만큼 그대로
    들어오고, 원가(외부 API 호출 수)만 캐시 히트율만큼 줄어든다 - 캐시 히트율이
    오를수록 한계 마진이 100%에 가까워진다. (참고로 지금 쓰는 CoinGecko/코인베이스/
    Sablier/업비트는 전부 무료 API라 "원가 절감"의 실질적 의미는 비용보다는
    "레이트리밋에 안 걸려서 서비스가 끊기지 않는다"는 안정성 쪽이 크다 - 레이트리밋에
    걸려 502가 나면 그 호출은 고객에게 데이터를 못 주고도 이미 과금된 상태라 오히려
    평판/환불 리스크가 생긴다.)

    ## 설계 결정
    - 예외는 캐싱하지 않는다 - 업스트림이 일시적으로 오류를 내는 순간에 그 오류를
      TTL 동안 그대로 재사용해버리면 캐시가 장애를 오히려 연장시키게 된다.
    - `None`을 유효한 캐시값으로 취급하지 않는다 - 이 프로젝트의 함수들은 성공 시
      항상 dict를 반환하므로 문제되지 않는다.
    - `key_fn`을 안 주면 인자를 그대로 문자열화해서 키로 쓴다 - 다만 여러 함수가
      같은 TTLCache 인스턴스를 공유할 때(예: price_cache) 키가 서로 충돌하지
      않도록, 함수별로 접두어를 붙인 key_fn을 넘기는 걸 권장한다.

    ## 가변형 TTL (2026-10-18, Engineering Precision Backlog 2 확장)
    `ttl_fn`을 주면 캐시미스 때마다 고정 TTL 대신 "이 결과를 몇 초간 캐시할지"를
    `ttl_fn(key, result) -> float`로 매번 새로 계산한다 - 이때 `cache`는
    `VariableTTLCache` 인스턴스여야 한다 (cachetools.TTLCache는 인스턴스 전체에
    고정 ttl 하나만 지원해서 항목별 TTL을 못 받는다 - 위 VariableTTLCache
    클래스 참고). `ttl_fn`을 안 주면(기본값) 기존 동작과 완전히 동일 - 기존
    6개 캐시(unlock/price/markdown/goplus/hip4/oracle_gap)는 이 파라미터
    없이 그대로 쓰고 있고 전혀 영향받지 않는다. 첫 적용 사례는
    app/data_sources.py의 polymarket_book_cache(`_polymarket_book_ttl_fn`) -
    시장 변동성(직전 대비 mid price 변화량)과 호가 갱신 빈도(책의 timestamp가
    실제로 갱신됐는지)로 1~5초 사이에서 TTL을 정한다.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs) if key_fn else str((args, tuple(sorted(kwargs.items()))))
            cached = cache.get(key)
            if cached is not None:
                return cached
            result = await func(*args, **kwargs)
            if ttl_fn is not None:
                cache.set(key, result, ttl_fn(key, result))
            else:
                cache[key] = result
            return result
        return wrapper
    return decorator
