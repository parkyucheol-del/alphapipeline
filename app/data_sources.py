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
# 바이낸스는 (1) 미국 리전 IP를 451로 차단하고 (2) 공유 IP에서 짧은 시간에 요청이
# 몰리면 418(자동 밴)을 돌려주는 걸 실제로 겪었다. 도메인을 시세 조회 전용
# data-api.binance.vision으로 바꿔도 여전히 418이 났는데, 이건 도메인이 아니라
# "이 서버 IP" 자체가 바이낸스 쪽에서 이미 밴 상태라는 뜻이다(Render 무료 플랜은
# 여러 사용자가 같은 지역 IP를 공유하므로, 다른 사용자의 트래픽 때문에 밴 먹었을 수 있다).
# 그래서 김치프리미엄 계산에 쓰는 "글로벌 USD 시세"를 바이낸스 대신 CoinGecko
# 공개 API로 교체했다 - 지금까지 이런 지역차단/IP밴 이슈가 보고되지 않았고
# API 키도 필요 없다. (거래대금 기반 dump-risk 계산용 get_binance_24h_stats는
# 어차피 DUMP_RISK_ENABLED=false로 꺼져있어서 당장 급하지 않아 그대로 둔다.)
BINANCE_TICKER_URL = "https://data-api.binance.vision/api/v3/ticker/price"
BINANCE_24H_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"
COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"

# 심볼(BTC 등) -> CoinGecko 코인 id. 자주 쓰이는 것 위주로 등록해뒀고,
# 목록에 없는 심볼이 필요해지면 https://api.coingecko.com/api/v3/coins/list 에서
# 정확한 id를 찾아 추가하면 된다.
_COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "DOGE": "dogecoin", "ADA": "cardano", "AVAX": "avalanche-2", "DOT": "polkadot",
    "LINK": "chainlink", "LTC": "litecoin", "BCH": "bitcoin-cash", "TRX": "tron",
    "ATOM": "cosmos", "UNI": "uniswap", "NEAR": "near", "MATIC": "matic-network",
    "BNB": "binancecoin", "SHIB": "shiba-inu", "PEPE": "pepe", "SUI": "sui",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
}

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
    """
    예: symbol='BTCUSDT' -> 글로벌 USD 시세.
    함수 이름은 호출부(app/logic.py)와의 호환을 위해 그대로 뒀지만, 실제로는
    바이낸스가 아니라 CoinGecko를 호출한다 (위 상단 주석 참고).
    """
    base = symbol.upper().removesuffix("USDT")
    coin_id = _COINGECKO_IDS.get(base)
    if not coin_id:
        raise ValueError(
            f"지원하지 않는 심볼입니다: {base} "
            f"(현재 지원 목록: {', '.join(sorted(_COINGECKO_IDS))})"
        )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            COINGECKO_SIMPLE_PRICE_URL, params={"ids": coin_id, "vs_currencies": "usd"}
        )
        r.raise_for_status()
        return float(r.json()[coin_id]["usd"])


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
