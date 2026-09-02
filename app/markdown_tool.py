"""
/v1/tools/ai-markdown : AI 에이전트용 웹 정제기.
임의 URL을 받아 광고/네비게이션/스크립트를 제거하고 본문만 마크다운으로 변환.
"""
import httpx
from readability import Document
from markdownify import markdownify as md
from app.cache import markdown_cache

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AlphaPipelineBot/1.0; +https://example.com/bot)"
}
MAX_HTML_BYTES = 3_000_000  # 3MB 초과 페이지는 거부 (비용/시간 보호)


async def url_to_markdown(url: str) -> dict:
    cached = markdown_cache.get(url)
    if cached:
        return cached

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()

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
