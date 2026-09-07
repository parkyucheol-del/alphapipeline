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
from typing import Callable

from cachetools import TTLCache
from app.config import settings

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


def ttl_cached(cache: TTLCache, key_fn: Callable[..., str] | None = None):
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
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs) if key_fn else str((args, tuple(sorted(kwargs.items()))))
            cached = cache.get(key)
            if cached is not None:
                return cached
            result = await func(*args, **kwargs)
            cache[key] = result
            return result
        return wrapper
    return decorator
