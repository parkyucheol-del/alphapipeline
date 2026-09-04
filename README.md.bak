# AlphaPipeline MVP

사업계획서 "실행 4단계 로드맵" 중 Step 1~3을 구현한 코드입니다.
`GET /v1/unlocks/dump-risk`, `GET /v1/market/kimchi-alert`, `GET /v1/tools/ai-markdown` 세 엔드포인트와,
Base 체인 USDC 기반 **공식 x402 결제 미들웨어(Coinbase CDP Facilitator)**가 포함되어 있습니다.
(2026-09: 자체 web3 검증 코드를 걷어내고 공식 `x402` SDK + CDP Facilitator로 교체 — x402 Bazaar /
Agentic.Market 등록을 위한 전제조건입니다. 자세한 내용은 아래 "2) 결제 흐름" 참고.)

**출시 전략**: `dump-risk`는 데이터 소스(DropsTab)가 유료(월 $59~)라서, 실제 수요가 검증되기 전까지는
`DUMP_RISK_ENABLED=false`(기본값)로 꺼둔 채 출시합니다. 꺼져 있는 동안 이 엔드포인트는 결제 없이
503("coming_soon")만 반환하므로 호출자에게 요금이 청구되지 않습니다. 원가가 거의 0인
`kimchi-alert`/`ai-markdown` 두 개만으로 먼저 유통해보고, 실제로 트래픽/매출이 생기면 그때
`DUMP_RISK_ENABLED=true`로 바꾸고 `DROPSTAB_API_KEY`를 채워서 켜면 됩니다.

## 폴더 구조

```
alphapipeline/
├── main.py                 # FastAPI 엔트리포인트 (3개 엔드포인트 라우팅)
├── app/
│   ├── config.py            # .env 설정 로더
│   ├── data_sources.py      # 업비트/바이낸스/DropsTab 원본 데이터 fetch
│   ├── logic.py              # dump-risk / kimchi-alert 가공 로직
│   ├── markdown_tool.py      # ai-markdown 웹 정제기
│   ├── payment.py            # 공식 x402 SDK + CDP Facilitator 결제 미들웨어 조립 + Bazaar 메타데이터
│   ├── schemas.py            # OpenAPI 응답 스키마 (Pydantic) + Bazaar output 예시 값
│   ├── scheduler.py          # UNLOCK_REFRESH_INTERVAL_HOURS 주기(기본 24h)로 락업 데이터 자동 갱신
│   └── cache.py              # 인메모리 캐시 (가격 2초, 락업 26h)
├── examples/client_example.py  # 봇 클라이언트에서 결제→호출하는 예시 코드
├── mcp_server.py             # MCP 서버 (Claude Desktop/Cursor용 도구 2개 노출)
├── claude_desktop_config.json  # Claude Desktop 연동 설정 예시
├── requirements.txt
├── Dockerfile
├── render.yaml               # Render.com 배포 설정
└── .env.example
```

## 1) 로컬 실행

```bash
cd alphapipeline
python -m venv .venv && source .venv/bin/activate   # 윈도우는 .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# .env를 열어 RECEIVER_WALLET_ADDRESS에 실제 지갑 주소(0x...)를 넣으세요.
# 지갑이 없다면 메타마스크 또는 코인베이스 월렛 앱에서 Base 네트워크로 만들면 됩니다.
# DUMP_RISK_ENABLED는 기본 false라서 dump-risk는 "coming_soon" 503만 반환합니다 (정상).
# 나머지 두 엔드포인트(kimchi-alert, ai-markdown)는 지갑 주소만 있으면 바로 정상 동작합니다.
# dump-risk까지 켜고 싶어지면 아래 "8) 락업 데이터 소스 변경 이력" 참고.
# CDP_API_KEY_ID/SECRET은 비워둬도 서버가 뜹니다 (테스트넷 파실리테이터로 자동 대체,
# 아래 "2) 결제 흐름" 참고) - 실제 메인넷 결제를 받으려면 반드시 채워야 합니다.

uvicorn main:app --reload --port 8000
```

브라우저에서 `http://localhost:8000/docs` 로 접속하면 Swagger UI로 바로 테스트할 수 있습니다.

기본값은 `.env`의 `PAYMENT_BYPASS_FOR_TESTING=true` 라서, x402 결제 미들웨어 자체가 장착되지 않고
바로 데이터가 나옵니다. **실전 배포 전에는 반드시 `false`로 바꾸고 CDP_API_KEY_ID/SECRET을
채우세요.**

### 빠른 테스트

```bash
curl "http://localhost:8000/v1/market/kimchi-alert?symbol=BTC"
curl "http://localhost:8000/v1/unlocks/dump-risk"
curl "http://localhost:8000/v1/tools/ai-markdown?url=https://example.com"
```

## 2) 결제 흐름 (공식 x402 SDK + CDP Facilitator)

**2026-09 업데이트**: 원래는 web3.py로 직접 온체인 트랜잭션을 조회하는 자체(비표준) 검증이었는데,
**x402 Bazaar / Agentic.Market 같은 공식 인덱서·마켓플레이스에 등록되려면 공식 파실리테이터가
검증·정산한 표준 결제만 인식**하기 때문에, 공식 Coinbase `x402` 파이썬 SDK와 CDP Facilitator로
완전히 교체했습니다. 자체 검증 코드는 이제 없습니다.

### 동작 원리

1. 서버(`main.py`)가 기동 시 `app.add_middleware(PaymentMiddlewareASGI, routes=..., server=...)`로
   x402 미들웨어를 장착합니다 (`app/payment.py`의 `build_resource_server()` / `build_routes()`가 조립).
   결제가 필요한 라우트(`kimchi-alert`, `ai-markdown`, 그리고 `DUMP_RISK_ENABLED=true`일 때는
   `dump-risk`도)는 이 미들웨어의 `routes` dict에 등록되어 있습니다.
2. 클라이언트(에이전트)가 결제 헤더 없이 그 라우트를 호출하면, 미들웨어가 라우트 핸들러에
   도달하기도 전에 표준 `402 Payment Required` 응답을 돌려줍니다. 이 응답 안의 `accepts` 배열에는
   결제 스킴(`exact`), 수신 주소(`payTo`), 가격, 네트워크(CAIP-2 형식, 예: `eip155:8453`)가 담겨 있고,
   Bazaar 인덱서가 이 스키마를 그대로 읽어서 서비스를 카탈로그에 등록합니다.
3. 클라이언트가 (공식 `x402` 클라이언트 SDK를 통해) EIP-3009(`transferWithAuthorization`) 서명을
   만들어 결제 헤더에 담아 같은 요청을 재호출합니다 — 온체인 트랜잭션을 직접 브로드캐스트하는 게
   아니라 "서명"만 만드는 방식이라 블록 컨펌을 기다릴 필요가 없습니다.
4. 서버가 그 서명을 CDP Facilitator(또는 개발 중에는 무료 테스트넷 파실리테이터)에 검증·정산을
   위임하고, 통과하면 곧바로 실제 데이터를 반환합니다.

### CDP Facilitator vs 테스트넷 파실리테이터

- `.env`의 `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET`을 채우면 Coinbase Developer Platform
  ([portal.cdp.coinbase.com](https://portal.cdp.coinbase.com))의 공식 CDP Facilitator를 사용합니다.
  이게 실제 메인넷(Base, `eip155:8453`) USDC 결제를 검증·정산할 수 있는 유일한 경로입니다.
- 비워두면 Coinbase가 운영하는 무료 공개 테스트넷 파실리테이터(`https://x402.org/facilitator`)로
  자동 대체됩니다. 이 경우 `X402_NETWORK` 설정과 무관하게 네트워크가 Base Sepolia(`eip155:84532`)로
  강제 전환됩니다 — **개발/테스트 전용이며, 이 상태에서는 실제 메인넷 USDC가 정산되지 않습니다.**
- `PAYMENT_BYPASS_FOR_TESTING=true`면 위 둘 다와 무관하게 미들웨어 자체를 장착하지 않아서
  결제 검사가 전혀 없습니다 (가장 빠른 로컬 개발용).

### 클라이언트(구매자) 쪽

`examples/client_example.py`에 봇이 공식 `x402` 클라이언트 SDK로 결제하고 호출하는 예시가 있습니다
(`pip install "x402[httpx]" eth_account` 필요). 402 감지, 서명 생성, 헤더 첨부, 재시도를 SDK가
전부 자동으로 처리해줘서, 이전 버전처럼 직접 온체인 transfer 트랜잭션을 만드는 코드는 없습니다.

### x402 Bazaar / Agentic.Market 등록

공식 SDK로 교체한 것 자체가 등록을 위한 **필요조건**이었고, 2026-09 업데이트로 실제 Bazaar 노출
메타데이터까지 채워 넣었습니다 (`app/payment.py`의 `_bazaar_extension()`, `_resource_url()`).

**지금 코드가 실제로 하는 일**
- 각 결제 라우트(`RouteConfig`)에 `resource`(절대 URL, `PUBLIC_BASE_URL` + 경로)와
  `extensions.bazaar`(입력 파라미터 스펙, 출력 예시, 서비스명, 태그)를 채워 넣습니다.
  이 값들은 공식 Bazaar 문서(`docs.x402.org/extensions/bazaar`)의 실제 JSON 예시 구조를 그대로
  따랐습니다 (`extensions.bazaar.info.input`/`output`, `serviceName`, `tags`).
- `output.example` 값은 `app/schemas.py`의 `KIMCHI_ALERT_EXAMPLE`/`MARKDOWN_EXAMPLE`/`DUMP_RISK_EXAMPLE`을
  그대로 재사용합니다 — 이 예시들은 동시에 FastAPI의 OpenAPI 응답 스키마(Pydantic 모델)와 짝을
  이루고 있어서, `/docs`·`/openapi.json`에 나오는 응답 구조와 Bazaar에 보고되는 예시가 항상 같은
  모양을 유지합니다. 새 필드를 추가/변경할 때는 `app/schemas.py`도 같이 고쳐야 합니다.
- `PUBLIC_BASE_URL`(.env, 기본값이 이미 `https://alphapipeline.onrender.com`)로 절대 URL을
  만듭니다 — Bazaar 문서가 "relative URLs"를 흔한 등록 실패 사유로 명시하고 있어서입니다.

**등록/노출이 실제로 되는 절차**
1. CDP Facilitator를 실제로 붙인 상태(`CDP_API_KEY_ID`/`SECRET` 설정, `PAYMENT_BYPASS_FOR_TESTING=false`)로
   서버를 배포합니다 — 이미 Render에 이 상태로 배포되어 있다면 완료된 단계입니다.
2. 별도의 "등록 신청" 절차는 없습니다. 공식 문서에 따르면, 클라이언트가 결제 시 "bazaar extension"을
   echo해서 보내면 **facilitator가 그 결제 페이로드를 처리하는 순간 자동으로 카탈로그에 반영**됩니다.
   즉 이 메타데이터를 코드에 넣어둔 것만으로는 카탈로그에 즉시 뜨지 않고, 공식 x402 클라이언트
   SDK를 쓰는 누군가의 **실제 결제가 최소 1건** 있어야 합니다.
3. Agentic.Market은 x402로 결제받는 API를 모아 보여주는 별도 마켓플레이스입니다 - 자체 등록
   양식/심사 절차가 있을 가능성이 높은데, 이 리포 작성 시점에는 공식 문서에서 셀프서비스 등록
   경로를 확인하지 못했습니다. 실제 트래픽이 좀 쌓인 뒤 다시 조사해드릴 수 있습니다.

**OpenAPI 메타데이터 (Bazaar와는 별개)**
- FastAPI가 자동 생성하는 `/openapi.json`, `/docs`도 함께 다듬었습니다 (`main.py`의 각 라우트에
  `summary`/`description`/`tags`/`responses` 추가, `app/schemas.py`의 Pydantic 모델을
  `response_model`로 연결). Bazaar는 자체 `extensions` 스키마를 쓰지 그대로 OpenAPI 스펙을
  읽지는 않지만, OpenAPI 스펙을 직접 읽어 도구를 등록하는 다른 에이전트 디렉토리(Agentic.Market
  포함, 확인 안 됨)를 위한 대비 차원이자, 사람이 `/docs`에서 API를 파악하기에도 훨씬 좋아집니다.

> **정직하게 밝혀둠**: `RouteConfig`의 `extensions`/`resource` 필드는 공식 GitHub 소스
> (`coinbase/x402`, `python/x402/http/types.py`)로 실제 필드명을 확인하고 작성했지만, `serviceName`/
> `tags`를 `extensions.bazaar` 안 어디에 정확히 두는지는 문서의 두 예시가 조금 다르게 보여주고
> 있어서(하나는 `info` 옆, 하나는 별도 `resource` 객체 안) 확신도가 100%는 아닙니다. 실제 카탈로그에
> 반영된 결과를 보고 위치를 조정해야 할 수 있습니다. 이 환경은 여전히 `x402`/`cdp-sdk` 패키지를 직접
> 실행해볼 수 없어서, `python3 -m py_compile`로 문법만 검증했습니다.

## 3) GitHub 업로드 & Render 무료 배포 (Step 3)

```bash
git init
git add .
git commit -m "AlphaPipeline MVP"
# GitHub에서 새 저장소 만든 뒤:
git remote add origin <당신의 저장소 URL>
git push -u origin main
```

이후 [render.com](https://render.com) 에서:
1. New → Web Service → 방금 만든 GitHub 저장소 선택
2. 이 저장소에 있는 `render.yaml`을 자동으로 인식합니다 (Blueprint 배포)
3. 환경변수 `RECEIVER_WALLET_ADDRESS`, `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`에 실제 값 입력
   (render.yaml에 `sync: false`로 되어있어 대시보드에서 직접 입력해야 함 — CDP 키는
   [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com)에서 발급)
4. `PAYMENT_BYPASS_FOR_TESTING`이 `false`로 설정되어 있는지 확인 (render.yaml 기본값은 이미 false)
5. Deploy → 몇 분 뒤 `https://alphapipeline-api.onrender.com` 같은 공용 도메인이 발급됩니다.

무료 플랜은 트래픽이 없으면 슬립되어 첫 요청 응답이 느릴 수 있습니다. 트래픽이 늘면 유료 플랜으로 전환을 고려하세요.

## 4) 남은 것 (Step 4, 이번 범위 밖)

사업계획서 Step 4(텔레그램 알림 봇 연동, 오픈소스 예제 배포, MCP 도구 등록소 등록)는
이번 MVP 범위에 포함되지 않았습니다. API가 실제로 배포되고 나면 이어서 만들 수 있습니다.

## 5) MCP 서버로 노출하기 (Claude Desktop / Cursor 연동)

기존 x402 유료 API(main.py)와는 별개로, 같은 로직을 [Model Context Protocol(MCP)](https://modelcontextprotocol.io)
서버로도 래핑해뒀습니다. `mcp_server.py`가 그 파일이고, 도구 2개를 제공합니다.

| MCP 도구 | 파라미터 | 재사용하는 로직 |
|---|---|---|
| `convert_to_markdown` | `url` (string, 필수) | `app/markdown_tool.py`의 `url_to_markdown` |
| `get_token_dump_risk` | `symbol` (string, 필수, 예: "ATH", "AO", "CPOOL") | `app/logic.py`의 `get_symbol_dump_risk` (신규) |

**⚠️ 이걸로 바로 돈이 벌리는 건 아닙니다.** Claude Desktop에서 로컬로 이 MCP 서버를 호출하는 것은
x402 결제 게이트(`app/payment.py`)를 거치지 않습니다 — 즉 이 경로로는 0.01 USDC 과금이 발생하지
않습니다. 이 MCP 서버를 만든 목적은 **배포(distribution)**입니다: AI 에이전트 생태계(Claude
Desktop, Cursor, 나중에는 MCP 레지스트리)에 우리 도구를 노출시켜서 존재를 알리고, 그중 일부가
실제 x402 유료 API 트래픽으로 이어지도록 유도하는 게 현실적인 역할입니다. 앞선 대화에서 이미
짚었듯, "수익화"는 여전히 배포된 x402 API를 실제로 쓰는 봇/에이전트를 얼마나 확보하느냐에 달려
있습니다.

`get_token_dump_risk`는 `DUMP_RISK_ENABLED=false`이거나 `DROPSTAB_API_KEY`가 비어있으면(현재
기본 상태 — 위 "출시 전략" 참고), 에러 없이 `{"available": false, "reason": "feature_not_enabled", ...}`
형태의 정상 응답을 돌려줍니다. 즉 이 사업 결정(유료 DropsTab 플랜은 수요 검증 후 결제)이 MCP
쪽에도 그대로 반영되어 있고, 이 기능을 켠다고 해서 별도로 코드를 고칠 필요는 없습니다 — main.py와
같은 `.env`를 공유합니다.

### 실행 방법

```bash
cd alphapipeline
source .venv/bin/activate   # 윈도우는 .venv\Scripts\activate
pip install -r requirements.txt   # mcp 패키지 포함되어 있음

python mcp_server.py
```

정상 기동되면 프로세스가 대기 상태로 유지됩니다(Claude Desktop 등 클라이언트가 stdin/stdout으로
붙어서 통신하는 방식이라, 터미널에서 직접 실행하면 그냥 멈춰있는 것처럼 보이는 게 정상입니다).
`Ctrl+C`로 종료하세요.

### Claude Desktop에 연결하기

1. `claude_desktop_config.json` 예시 파일을 참고해서, Claude Desktop의 설정 파일에 `alphapipeline`
   항목을 추가합니다. 설정 파일 위치는 보통 아래와 같습니다.
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
2. `command`와 `args`의 `/절대경로/alphapipeline/...` 부분을 실제 이 프로젝트를 클론한 절대경로로
   바꿔주세요 (venv 안의 python 실행파일 경로 + `mcp_server.py`의 절대경로).
3. Claude Desktop을 완전히 재시작하면, 새 대화에서 망치(도구) 아이콘 목록에 `convert_to_markdown`,
   `get_token_dump_risk`가 나타납니다.
4. Cursor 등 다른 MCP 클라이언트도 각자의 MCP 설정 파일에 같은 형식(`mcpServers.alphapipeline`)으로
   등록하면 동일하게 동작합니다.

### 에러 처리

두 도구 모두 예외를 그대로 던지지 않고, AI 에이전트가 바로 파싱해서 판단할 수 있도록
`{"success": false, "error": {"type": "...", "message": "..."}}` 형태로 구조화된 실패 응답을
반환합니다 (`invalid_input`, `invalid_url`, `timeout`, `http_error`, `network_error`,
`unsupported_content`, `unknown_error` 등). 성공 시에는 `{"success": true, ...실제 데이터}` 형태입니다.

## 6) 갱신 주기 설계 (왜 이렇게 다른가)

두 엔드포인트는 성격이 달라서 갱신 주기도 의도적으로 다르게 설계했습니다.

* **`/v1/market/kimchi-alert`**: 시세는 초 단위로 바뀌기 때문에 실시간성이 곧 상품성입니다.
  `.env`의 `KIMCHI_CACHE_TTL_SECONDS`(기본 2초)만큼만 캐시하고, 그 외엔 매 호출마다 업비트/바이낸스에서
  직접 가격을 가져옵니다. 이 값을 더 줄이면(1초 등) 더 실시간에 가까워지지만 API 레이트리밋에 더 가까워지니
  트래픽이 커지면 1~2 사이에서 조절하세요. 심볼도 `?symbol=` 파라미터로 아무 코인이나 조회됩니다
  (업비트 KRW마켓 + 바이낸스 USDT마켓에 둘 다 있는 코인이면 전부 가능, 특정 코인에 고정 안 됨).
* **`/v1/unlocks/dump-risk`**: 특정 코인 몇 개가 아니라 **DropsTab이 제공하는 전체 락업 해제 이벤트
  목록**을 페이지네이션으로 훑습니다(`DROPSTAB_PAGE_SIZE` x `MAX_UNLOCK_SCAN_PAGES`). 락업 해제 일정은
  몇 주 전에 미리 확정되어 하루 안에 자주 바뀌지 않으므로, 이 스캔은 `.env`의
  `UNLOCK_REFRESH_INTERVAL_HOURS`(기본 24시간 = 하루 1회) 주기로만 실행되고 그 결과가 다음 스캔까지
  캐시된 채로 서빙됩니다. 이미 "필요한 최소 빈도"에 가깝다고 보지만, 서버 자원(과 DropsTab API
  호출 쿼터)을 더 아끼고 싶으면 48이나 72로 늘려도 됩니다 — 그만큼 새로 잡히는 언락 이벤트를 며칠
  늦게 발견하게 되는 트레이드오프만 감안하면 됩니다.

## 7) 알아두어야 할 부분

* **DropsTab 응답 필드명 미검증**: `app/logic.py`의 `_process_event()`는 DropsTab 응답 필드명을
  여러 후보(`date`/`unlockDate`, `percentage`/`unlockPercentage` 등)로 방어적으로 파싱하지만,
  실제 API 키로 호출해본 응답 구조로 검증된 건 아닙니다. `/v1/unlocks/dump-risk` 응답의
  `protocols_scanned`는 0이 아닌데 `count`가 계속 0이라면 필드명이 안 맞는 것이니, DropsTab
  API 문서(https://api-docs.dropstab.com)에서 실제 응답 예시를 확인하고 `_first(ev, ...)` 호출부의
  키 후보를 맞춰야 합니다.
* **개발 환경 네트워크 제약**: 이 코드는 외부망이 막힌 샌드박스에서 작성되어, 실제 업비트/바이낸스/
  DropsTab API 호출과 `x402`/`cdp-sdk` 패키지 설치·실행은 로컬 PC나 Render 배포 환경(둘 다 인터넷
  접근 가능)에서 처음 실행해봐야 합니다.
* **x402/cdp-sdk 패키지 버전**: `requirements.txt`는 2026-09 기준 PyPI 최신 버전으로 맞췄지만
  실제 설치 테스트는 못 했습니다. import 에러가 나면 "2) 결제 흐름"의 안내를 참고해 조정하세요.

## 8) 락업 데이터 소스 변경 이력 (DeFiLlama → DropsTab)

이 MVP는 원래 DeFiLlama의 무료 API(`api.llama.fi/emissions`)로 락업 해제 데이터를 가져오도록
만들었었는데, 실제로 돌려보니 그 엔드포인트가 `402 Payment Required`를 반환했습니다. 확인해보니
DeFiLlama가 토큰 언락/이미션 데이터를 유료 Pro API(월 $300, 연 $3,000)로 옮겨서 무료로는 더 이상
접근할 수 없게 바뀌어 있었습니다.

그래서 데이터 소스를 [DropsTab](https://dropstab.com/products/commercial-api)으로 교체했습니다.
DropsTab은 두 가지 방법으로 접근할 수 있습니다.

1. **Builders Program(추천, 무료)**: 학생/스타트업/오픈소스 프로젝트 대상으로 API 키를 무료로
   내주는 프로그램입니다 ([소개 글](https://dropstab.com/research/product/how-to-get-free-crypto-data-with-drops-tab-builders-program)).
   신용카드 없이 신청서를 제출하면 보통 며칠 내로 승인되고, 3개월 단위(오픈소스 프로젝트는 연장 가능)로
   무료 키를 받을 수 있습니다. 신청 폼이 안 보이면 support@dropstab.com으로 문의하세요.
2. **유료 Advanced 플랜(월 $59~, 연간 결제 시 $49/월)**: Builders Program 승인이 안 되거나
   상업적으로 계속 쓸 계획이면 [dropstab.com/products/commercial-api](https://dropstab.com/products/commercial-api)에서
   가입하면 됩니다. **주의: Basic($19/월) 플랜에는 tokenUnlocks 엔드포인트가 포함되어 있지
   않습니다 — 락업 데이터가 필요하면 반드시 Advanced 이상을 선택하세요.** 그래도 DeFiLlama
   Pro($300/월)보다는 훨씬 저렴합니다.

**현재 결정**: 매달 $59를 내는 게 검증도 안 된 기능에 대한 과한 선지출이라고 판단해서, 일단
`DUMP_RISK_ENABLED=false`로 꺼둔 채 나머지 두 엔드포인트만으로 먼저 출시하기로 했습니다
(위 "출시 전략" 참고). 키를 발급받아 이 기능을 켤 때는 `.env`에서 `DUMP_RISK_ENABLED=true`로
바꾸고 `DROPSTAB_API_KEY`를 채우면 됩니다. 두 값 다 없어도 서버는 정상적으로 켜지고,
`/v1/unlocks/dump-risk`만 503("coming_soon")을 반환합니다 — 나머지 두 엔드포인트는 영향받지 않습니다.

**참고 자료**
- [DefiLlama Pro API 요금제](https://docs.llama.fi/pro-api)
- [DefiLlama API 개요 (api-evangelist)](https://github.com/api-evangelist/defillama)
- [DropsTab 상업용 API](https://dropstab.com/products/commercial-api)
- [DropsTab Builders Program 소개](https://dropstab.com/research/product/how-to-get-free-crypto-data-with-drops-tab-builders-program)
- [DropsTab API 문서](https://api-docs.dropstab.com/reference/alloverview)

## 9) 자주 겪는 설치 오류

* **`ImportError: lxml.html.clean module is now a separate project`**: 최신 버전의 `lxml`이 `html.clean`
  기능을 별도 패키지로 분리하면서 생기는 오류입니다. `requirements.txt`에 `lxml==5.1.0`으로 이미
  버전을 고정해뒀으니, `pip install -r requirements.txt`를 다시 실행하면 자동으로 해결됩니다.
* **`pip install -r requirements.txt` 실행 시 "No such file or directory: 'requirements.txt'"**:
  터미널이 `alphapipeline` 폴더 안에 있지 않은 경우입니다. `dir`(윈도우) 또는 `ls`(맥/리눅스)로
  `main.py`, `requirements.txt`가 보이는 위치인지 먼저 확인하세요.
* **PowerShell에서 `.venv\Scripts\activate` 실행이 안 될 때**: 보안 정책 때문일 수 있습니다.
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 를 먼저 실행한 뒤 다시 시도하세요.

## 10) 이번 업데이트: 공식 x402 결제 레이어로 전환 (파일별 변경점)

자체 web3 결제 검증을 걷어내고 공식 Coinbase `x402` SDK + CDP Facilitator로 교체하면서 바뀐 파일들입니다.

| 파일 | 변경 내용 |
|---|---|
| `app/payment.py` | **전면 재작성.** web3 온체인 조회 코드 삭제 → `x402ResourceServer`/`PaymentMiddlewareASGI`를 조립하는 `build_resource_server()`, `build_routes()` 함수로 교체. CDP 키 유무에 따라 CDP Facilitator(메인넷) 또는 테스트넷 파실리테이터를 자동 선택. |
| `main.py` | 각 라우트 핸들러 안에서 `payment_gate(request)`를 직접 호출하던 코드를 전부 제거. 대신 `app = FastAPI(...)` 직후 `app.add_middleware(PaymentMiddlewareASGI, routes=..., server=...)`로 한 번만 장착. `/`(root) 응답의 결제 정보 필드를 SDK 기반 정보(`payment.network`, `payment.facilitator`)로 교체. |
| `app/config.py` | 제거: `BASE_RPC_URL`, `USDC_CONTRACT_ADDRESS`, `USDC_DECIMALS`, `TX_CACHE_TTL_SECONDS` (더 이상 자체 검증을 안 하므로 불필요). 추가: `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `X402_NETWORK`. |
| `app/cache.py` | `tx_seen_cache`(재생 공격 방지용 캐시) 제거 — 이제 Facilitator/EIP-3009 서명 체계가 이 역할을 대신함. |
| `requirements.txt` | `web3==6.20.3` 제거, `x402[fastapi]==2.21.0`과 `cdp-sdk>=0.10.2` 추가. |
| `.env`, `.env.example`, `render.yaml` | `BASE_RPC_URL`/`USDC_CONTRACT_ADDRESS`/`TX_CACHE_TTL_SECONDS` 제거, `CDP_API_KEY_ID`/`CDP_API_KEY_SECRET`/`X402_NETWORK` 추가. `RECEIVER_WALLET_ADDRESS`, `PRICE_PER_CALL_USDC`, `PAYMENT_BYPASS_FOR_TESTING`은 그대로 유지. |
| `examples/client_example.py` | **전면 재작성.** web3로 직접 USDC transfer 트랜잭션을 만들던 코드 삭제 → 공식 `x402` 클라이언트 SDK(`x402Client`, `x402HttpxClient`, `EthAccountSigner`)로 402 감지·서명·재시도를 자동화. |

**바뀌지 않은 것**: `app/logic.py`, `app/data_sources.py`, `app/markdown_tool.py`, `app/scheduler.py`,
`mcp_server.py` — 결제 레이어와 무관한 데이터 가공 로직/MCP 서버는 그대로입니다.

## 11) 이번 업데이트: x402 Bazaar 메타데이터 + OpenAPI 문서화 (파일별 변경점)

Render 배포 확인 후, 외부 AI 에이전트가 이 API를 검색/호출할 수 있도록 Bazaar 노출 메타데이터와
OpenAPI 문서를 채워 넣으면서 바뀐 파일들입니다.

| 파일 | 변경 내용 |
|---|---|
| `app/schemas.py` | **신규 파일.** 각 엔드포인트 응답의 Pydantic 모델(`KimchiAlertResponse`, `MarkdownResponse`, `DumpRiskResponse` 등)과, OpenAPI 예시·Bazaar `output.example`이 동시에 참조하는 예시 값(`KIMCHI_ALERT_EXAMPLE` 등)을 정의. |
| `app/payment.py` | `build_routes()`의 각 `RouteConfig`에 `resource`(절대 URL)와 `extensions`(Bazaar `info.input`/`output`/`serviceName`/`tags`) 추가. `_resource_url()`, `_bazaar_extension()` 헬퍼 신규 추가. |
| `app/config.py` | `PUBLIC_BASE_URL` 추가 (기본값이 이미 실제 Render 배포 주소로 설정됨 — Bazaar가 요구하는 절대 URL을 만드는 데 사용). |
| `main.py` | 세 엔드포인트 각각에 `summary`/`description`/`tags`/`responses`(+ `response_model`)를 추가해 `/docs`·`/openapi.json`을 풍부하게 함. `FastAPI(...)`에 `openapi_tags` 추가. root(`/`) 응답에 `openapi_spec` 필드 추가. |
| `.env`, `.env.example`, `render.yaml` | `PUBLIC_BASE_URL` 추가 (선택사항 — 기본값이 이미 맞게 설정되어 있어 안 넣어도 동작함). |

**바뀌지 않은 것**: `app/logic.py`, `app/data_sources.py`, `app/markdown_tool.py`, `app/cache.py`,
`app/scheduler.py`, `mcp_server.py`, `examples/client_example.py` — 데이터 가공/MCP/클라이언트
예제는 이번 변경과 무관합니다.

> 이 업데이트는 여러 파일에 걸쳐있어서, 로컬(또는 GitHub 모바일 편집기)에 반영한 뒤
> `git add . && git commit -m "..." && git push`로 GitHub에 올리면 Render가 자동으로
> 재배포합니다. Render 대시보드에서 `PUBLIC_BASE_URL` 환경변수를 따로 안 넣어도, 코드
> 기본값이 이미 실제 배포 주소와 같아서 정상 동작합니다.
