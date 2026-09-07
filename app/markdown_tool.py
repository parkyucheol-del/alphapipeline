"""
/v1/tools/ai-markdown : AI 에이전트용 웹 정제기.
임의 URL을 받아 광고/네비게이션/스크립트를 제거하고 본문만 마크다운으로 변환.

## SSRF 방어 (2026-09-07 추가)
이 엔드포인트는 서버가 "사용자가 지정한 URL"을 대신 요청해주는 구조라, 방어가
없으면 호출자가 $0.005만 내고 서버 내부망이나 클라우드 인스턴스 메타데이터
(예: 169.254.169.254), localhost, 사설 IP 대역(10.x/172.16.x/192.168.x)을
스캔/조회하는 데 이 엔드포인트를 악용할 수 있다(SSRF).

_assert_public_host()가 요청 전에 URL의 호스트명을 실제로 DNS resolve해서
나온 모든 IP(IPv4-mapped IPv6 포함)가 사설/루프백/링크로컬/예약/멀티캐스트/
미지정 대역이 아닌지 검사한다. 리다이렉트는 httpx의 자동 follow_redirects를
쓰지 않고 한 홉씩 수동으로 따라가며, 매 홉마다 동일 검증을 다시 수행한다
(리다이렉트로 우회하는 SSRF 방지).

**알려진 한계 (정직하게 명시)**: 이건 "resolve 시점"의 IP만 검사하므로, 이론상
DNS TTL이 매우 짧은 도메인을 이용한 DNS 리바인딩 공격(resolve 시점엔 공개 IP를
주고, 실제 커넥션 시점엔 다른 IP로 바꿔치기)까지는 막지 못한다 - 완전히
막으려면 resolve한 IP로 직접 커넥션하면서 Host 헤더만 원래 도메인으로 보내는
커스텀 트랜스포트가 필요한데, 이 프로젝트 규모/위협 모델(금융기관이 아닌
소규모 x402 마이크로페이먼트 API)에서는 과한 엔지니어링이라 판단해 보류함.
실무에서 흔한 공격 벡터(사설 IP/메타데이터 주소를 URL에 직접 넣는 경우)는
이 구현으로 막힌다.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from readability import Document
from markdownify import markdownify as md
from app.cache import markdown_cache

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AlphaPipelineBot/1.0; +https://example.com/bot)"
}
MAX_HTML_BYTES = 3_000_000  # 3MB 초과 페이지는 거부 (비용/시간 보호)
MAX_REDIRECTS = 5
ALLOWED_SCHEMES = {"http", "https"}

# 클래식 사설/예약 대역(RFC1918 등)은 ipaddress의 is_private 등으로 커버되지만,
# CGNAT 대역(100.64.0.0/10, 일부 클라우드가 내부망에 씀)은 구버전 Python의
# is_private가 놓칠 수 있어 명시적으로 추가 차단한다.
_EXTRA_BLOCKED_NETWORKS = [ipaddress.ip_network("100.64.0.0/10")]


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    candidates = [ip]
    mapped = getattr(ip, "ipv4_mapped", None)  # ::ffff:169.254.169.254 형태 우회 방지
    if mapped is not None:
        candidates.append(mapped)

    for c in candidates:
        if (
            c.is_private
            or c.is_loopback
            or c.is_link_local  # 169.254.0.0/16, fe80::/10 (클라우드 메타데이터 포함)
            or c.is_reserved
            or c.is_multicast
            or c.is_unspecified
        ):
            return True
        if c.version == 4 and any(c in net for net in _EXTRA_BLOCKED_NETWORKS):
            return True
    return False


async def _assert_public_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"허용되지 않는 URL 스킴입니다: {parsed.scheme!r} (http/https만 허용)")
    host = parsed.hostname
    if not host:
        raise ValueError("URL에서 호스트를 확인할 수 없습니다")

    try:
        # getaddrinfo는 블로킹 호출이라 스레드로 넘겨서 이벤트 루프를 막지 않는다.
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except socket.gaierror as e:
        raise ValueError(f"호스트 이름을 확인할 수 없습니다: {host}") from e

    resolved_ips = {info[4][0] for info in infos}
    if not resolved_ips:
        raise ValueError(f"호스트 이름을 확인할 수 없습니다: {host}")

    for ip_str in resolved_ips:
        if _is_blocked_ip(ip_str):
            raise ValueError(
                "내부/사설 네트워크로 확인되는 호스트는 요청할 수 없습니다 (SSRF 방지)"
            )


async def url_to_markdown(url: str) -> dict:
    cached = markdown_cache.get(url)
    if cached:
        return cached

    await _assert_public_host(url)

    current_url = url
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            resp = await client.get(current_url)

            if 300 <= resp.status_code < 400 and "location" in resp.headers:
                next_url = str(httpx.URL(current_url).join(resp.headers["location"]))
                await _assert_public_host(next_url)  # 매 리다이렉트 홉마다 재검증
                current_url = next_url
                continue

            resp.raise_for_status()
            break
        else:
            raise ValueError(f"리다이렉트가 너무 많습니다 ({MAX_REDIRECTS}회 초과)")

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            raise ValueError(f"HTML 문서가 아닙니다 (content-type: {content_type})")

        raw_html = resp.text
        if len(raw_html.encode("utf-8", errors="ignore")) > MAX_HTML_BYTES:
            raise ValueError("페이지가 너무 큽니다 (3MB 초과)")

    doc = Document(raw_html)
    title = doc.title()
    cleaned_html = doc.summary(html_partial=True)

    markdown_body = md(cleaned_html, heading_style="ATX", strip=["script", "style", "nav", "footer", "aside"])
    markdown_body = "\n".join(line for line in markdown_body.splitlines() if line.strip() != "" or True).strip()

    payload = {
        "url": url,
        "title": title,
        "markdown": markdown_body,
        "char_count": len(markdown_body),
    }
    markdown_cache[url] = payload
    return payload
