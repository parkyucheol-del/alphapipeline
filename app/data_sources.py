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
import logging

import httpx
from app.config import settings

logger = logging.getLogger("alphapipeline")

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
COINGECKO_COIN_DETAIL_URL = "https://api.coingecko.com/api/v3/coins/{coin_id}"
COINBASE_SPOT_PRICE_URL = "https://api.coinbase.com/v2/prices/{base}-USD/spot"

# dump-risk 온체인 재설계(Sablier)용 GraphQL 엔드포인트.
# Envio가 운영하는 무료/무인증 공개 인덱서 - Base + Ethereum 통합.
# (The Graph의 공식 프로덕션 엔드포인트는 유료/API 키가 필요하고, 무료 "testing"
# 엔드포인트는 일 3,000쿼리로 제한돼 있어서 이쪽을 1순위로 채택함)
SABLIER_GRAPHQL_URL = "https://indexer.hyperindex.xyz/53b7e25/v1/graphql"

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
    바이낸스가 아니다 (위 상단 주석 참고). CoinGecko 무료 API도 같은 공유 IP
    문제로 429(레이트리밋)를 겪어서, 코인베이스 공개 스팟 시세를 1순위로 쓰고
    CoinGecko는 그게 실패했을 때만 쓰는 폴백으로 내렸다. 코인베이스는 이미
    결제(CDP Facilitator)에도 쓰고 있는 인프라라 시세 조회도 상대적으로
    안정적일 가능성이 높다.
    """
    base = symbol.upper().removesuffix("USDT")

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(COINBASE_SPOT_PRICE_URL.format(base=base))
            r.raise_for_status()
            return float(r.json()["data"]["amount"])
    except Exception as e:
        # 조용히 넘어가지 않고 로그를 남긴다 - 이전에 COINBASE_SPOT_PRICE_URL이
        # 정의조차 안 되어 있던 버그를 이 exception이 숨겨버려서 한참 헤맸었다.
        logger.warning("코인베이스 시세 조회 실패, CoinGecko로 폴백합니다: %s", e)

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


# ============================================================================
# dump-risk 온체인(Sablier) 재설계 - DropsTab 유료 플랜 없이 무료로 서빙하기 위한 경로.
#
# 설계 배경(자세한 비교는 alphapipeline_dump_risk_design.md 참고):
#   1) DropsTab/TokenUnlocks 내부 API 역공학 - ToS 위반/차단 리스크가 커서 기각.
#   2) 무료 공개 API + 자체 스코어링 - 정작 핵심 데이터(락업 캘린더)를 무료로
#      주는 곳이 없어서(DeFiLlama/DropsTab/CryptoRank/CMC 전부 유료화) 기각.
#   3) 온체인 베스팅 컨트랙트(Sablier) 직접 조회 - 채택. "순수 온체인 검증 데이터"
#      라는 타이틀 자체가 차별화 포인트가 되고, 제3자 API 정책에 종속되지 않는다.
#
# 정직하게 밝혀둘 한계: Sablier GraphQL 스키마 중 "cliff/시작/종료 타임스탬프"
# 필드명은 이 세션에서 끝내 확인하지 못했다 (GraphQL introspection을 이 서버
# 환경에서 실행할 방법이 없었음 - WebFetch는 GET 기반이라
# "PersistedQueryNotSupported"로 거부됐고, 직접 POST도 아웃바운드 네트워크가
# 막혀 있어 실패함). 그래서 v1은 "언제" 대신 "지금 얼마나 잠겨있는가"만으로
# 위험도를 계산한다 - 틀린 필드명을 추측해서 쿼리 에러를 내거나, 더 나쁘게는
# 그럴듯하지만 틀린 날짜를 보여주는 것보다 안전한 선택이다.
# 타임스탬프 필드까지 확인되면 get_sablier_active_streams()의 GraphQL 쿼리에
# 필드를 추가하고 app/logic.py의 _refresh_unlock_cache_onchain()에서
# days_until_unlock을 채워주면 된다. 아래가 사용자가 직접 확인할 수 있는 방법:
#
#   curl -X POST https://indexer.hyperindex.xyz/53b7e25/v1/graphql \
#     -H "Content-Type: application/json" \
#     -d '{"query": "{ __type(name: \"LockupStream\") { fields { name } } }"}'
#
# (또는 그냥 브라우저로 위 URL에 접속하면 뜨는 GraphiQL 콘솔에서 실행해도 됨)
# ============================================================================

# 확인된(Sablier 공식 문서 예시에 실제로 등장하는) 필드만 사용한다.
# cliff/startTime/endTime 등은 미확인이라 의도적으로 제외했다 - 위 설명 참고.
_SABLIER_ACTIVE_STREAMS_QUERY = """
query ActiveStreams($limit: Int!) {
  lockupStreams(
    where: {canceled: {_eq: false}, intactAmount: {_gt: "0"}}
    limit: $limit
  ) {
    id
    chainId
    depositAmount
    intactAmount
    withdrawnAmount
    canceled
    asset {
      address
      symbol
      decimals
    }
  }
}
"""

_SABLIER_STREAMS_FOR_TOKEN_QUERY = """
query StreamsForToken($tokenAddress: String!, $limit: Int!) {
  lockupStreams(
    where: {
      canceled: {_eq: false}
      intactAmount: {_gt: "0"}
      asset: {address: {_eq: $tokenAddress}}
    }
    limit: $limit
  ) {
    id
    chainId
    depositAmount
    intactAmount
    withdrawnAmount
    canceled
    asset {
      address
      symbol
      decimals
    }
  }
}
"""


async def _sablier_graphql(query: str, variables: dict) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(
            SABLIER_GRAPHQL_URL,
            json={"query": query, "variables": variables},
        )
        r.raise_for_status()
        body = r.json()
        if "errors" in body and body["errors"]:
            raise RuntimeError(f"Sablier GraphQL 오류: {body['errors']}")
        return body.get("data", {})


async def get_sablier_active_streams(limit: int = 200) -> list[dict]:
    """
    현재 취소되지 않고 잠긴 물량(intactAmount > 0)이 남아있는 베스팅 스트림을
    최신순 상위 `limit`개 스캔한다. dump-risk 전체 목록(/v1/unlocks/dump-risk)
    스캔용 - app/logic.py의 _refresh_unlock_cache_onchain()이 사용한다.

    주의: 이건 "Sablier 프로토콜로 베스팅되는 물량"만 잡는다. 다른 베스팅
    방식(커스텀 컨트랙트, 거래소 자체 락업 등)은 이 방법으로는 안 잡히므로,
    "이 목록에 없다고 락업이 없는 게 아니다"라는 커버리지 한계가 있다
    (응답에 coverage_notice로 명시함).
    """
    data = await _sablier_graphql(_SABLIER_ACTIVE_STREAMS_QUERY, {"limit": limit})
    return data.get("lockupStreams", []) or []


async def get_sablier_streams_for_token(contract_address: str, limit: int = 200) -> list[dict]:
    """
    특정 토큰 컨트랙트 주소 하나에 대한 활성 베스팅 스트림만 조회.
    MCP get_token_dump_risk / GET /v1/unlocks/dump-risk?symbol=... 같은
    "심볼 1개 지정" 조회용 - app/logic.py의 _get_symbol_dump_risk_onchain()이 사용한다.
    """
    data = await _sablier_graphql(
        _SABLIER_STREAMS_FOR_TOKEN_QUERY,
        {"tokenAddress": contract_address.lower(), "limit": limit},
    )
    return data.get("lockupStreams", []) or []


async def get_coingecko_token_contract_and_supply(coin_id: str) -> dict:
    """
    CoinGecko 코인 상세 조회에서 (1) 체인별 컨트랙트 주소와 (2) 유통량을 뽑아온다.
    Sablier는 "토큰 컨트랙트 주소" 기준으로 스트림을 색인하기 때문에, 심볼(BTC 등)
    -> 컨트랙트 주소로 변환하는 다리 역할이며, 유통량은 "유통량 대비 잠긴 비율(%)"
    계산의 분모로 쓰인다.

    반환 예: {"circulating_supply": 19_800_000.0,
              "platforms": {"ethereum": "0xabc...", "base": "0xdef...", ...}}
    """
    url = COINGECKO_COIN_DETAIL_URL.format(coin_id=coin_id)
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        body = r.json()

    market_data = body.get("market_data") or {}
    platforms = body.get("platforms") or {}
    # 값이 빈 문자열인 체인은 걸러낸다 (CoinGecko가 미지원 체인은 "" 로 채워둠)
    platforms = {chain: addr for chain, addr in platforms.items() if addr}

    return {
        "circulating_supply": market_data.get("circulating_supply"),
        "platforms": platforms,
    }
