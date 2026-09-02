"""
간단한 인메모리 캐시.
- unlock_cache: 하루 1회 스케줄러가 갱신 (락업 해제 데이터는 자주 안 바뀜)
- price_cache: 초 단위 TTL로 실시간성 유지하면서 API 폭주 방지

(이전에는 결제 tx_hash 재사용 방지용 tx_seen_cache도 여기 있었는데, 공식 x402
Facilitator로 결제 검증을 이전하면서 재생 공격 방지는 Facilitator/EIP-3009
서명 체계가 대신 처리하게 되어 제거했다 - app/payment.py 참고.)
"""
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
