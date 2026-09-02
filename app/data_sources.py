"""
외부 데이터 소스 연동 모듈.
- 업비트/바이낸스 실시간 시세
- DropsTab 락업 해제(unlock) 일정
Render 배포 환경(전체 인터넷 접근 가능)을 기준으로 작성됨.
로컬/샌드박스에서 외부망이 막혀 있으면 예외를 던지고, 상위 로직이 캐시된
마지막 값으로 대체하도록 설계되어 있다.

참고: 원래는 DeFiLlama(api.llama.fi/emissions)를 무료로 썼는데, DeFiLlama가
해당 데이터를 유료 Pro API(월 $300~)로 옮겨버려서 DropsTab으로 교체했다.
DropsTab은 Builders Program(학생/스타트업 대상 무료 API 키 신청, 승인제)이나
유료 Advanced 플랜(월 $59, Basic $19에는 tokenUnlocks가 없음. DeFiLlama 대비 훨씬 저렴)으로 접근할 수 있다.
자세한 내용은 README.md의 "락업 데이터 소스 변경 이력" 참고.
"""
import httpx
from app.config import settings

UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
BINANCE_24H_URL = "https://api.binance.com/api/v3/ticker/24hr"

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


async def get_upbit_price_krw(market: str) -> dict:
    """예: market='KRW-BTC' -> 업비트 현재가(KRW)"""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(UPBIT_TICKER_URL, params={"markets": market})
        r.raise_for_status()
        data = r.json()
        return data[0] if data else {}


async def get_upbit_usdkrw_rate() -> float:
    """
    업비트 KRW-USDT 마켓 가격을 달러/원 환율의 근사치로 사용한다.
    (스테이블코인 USDT ≈ 1 USD 가정, 외부 유료 환율 API 불필요)
    """
    data = await get_upbit_price_krw("KRW-USDT")
    return float(data.get("trade_price", 0))


async def get_binance_price_usdt(symbol: str) -> float:
    """예: symbol='BTCUSDT' -> 바이낸스 현재가(USDT)"""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(BINANCE_TICKER_URL, params={"symbol": symbol})
        r.raise_for_status()
        return float(r.json()["price"])


async def get_binance_24h_stats(symbol: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(BINANCE_24H_URL, params={"symbol": symbol})
        r.raise_for_status()
        return r.json()


async def get_dropstab_token_unlocks(page: int = 0, page_size: int = 100) -> dict:
    """
    DropsTab의 전체 락업 해제 이벤트 개요 조회 (페이지네이션).
    DROPSTAB_API_KEY가 없으면 호출하지 않고 예외를 던진다(상위에서 처리).

    주의: 정확한 응답 필드명은 실제 API 키를 발급받아 1회 호출해보고
    app/logic.py의 파싱 로직과 맞는지 확인이 필요하다 (README 참고).
    """
    if not settings.DROPSTAB_API_KEY:
        raise RuntimeError("DROPSTAB_API_KEY가 설정되지 않았습니다")

    url = f"{settings.DROPSTAB_API_BASE_URL}/tokenUnlocks"
    headers = {"Authorization": f"Bearer {settings.DROPSTAB_API_KEY}"}
    params = {
        "page": page,
        "pageSize": page_size,
        "sortingField": "UNLOCK_PROGRESS_PERCENT",
        "sortingOrder": "DESC",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, headers=headers, params=params)
        r.raise_for_status()
        return r.json()


async def get_dropstab_token_unlock_detail(coin_slug: str) -> dict:
    """
    DropsTab의 특정 코인 락업 해제 상세 조회 (심볼 1개 지정).
    전체 스캔(get_dropstab_token_unlocks)과 달리 특정 티커에 대한 다음 언락
    이벤트를 바로 조회하는 용도 - MCP get_token_dump_risk 도구가 사용한다.

    DROPSTAB_API_KEY가 없으면 호출하지 않고 예외를 던진다(상위에서 처리).

    주의: 정확한 엔드포인트 경로/응답 필드명은 실제 API 키를 발급받아
    1회 호출해보고 확인이 필요하다 (README 참고). 여기서는 DropsTab의
    개요 API와 동일한 베이스에 코인 슬러그를 붙이는 관례를 따른다.
    """
    if not settings.DROPSTAB_API_KEY:
        raise RuntimeError("DROPSTAB_API_KEY가 설정되지 않았습니다")

    url = f"{settings.DROPSTAB_API_BASE_URL}/tokenUnlocks/{coin_slug.lower()}"
    headers = {"Authorization": f"Bearer {settings.DROPSTAB_API_KEY}"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        return r.json()
