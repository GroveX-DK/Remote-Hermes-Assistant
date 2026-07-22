"""Local web tools: DuckDuckGo search and page fetching.

Replaces the Claude API's server-side web tools so the agent can research
using only local infrastructure (plus the public web itself).
"""

from __future__ import annotations

import gzip
import re
import urllib.request
from html.parser import HTMLParser

try:
    from ddgs import DDGS
except ImportError:  # older package name
    from duckduckgo_search import DDGS

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_MAX_FETCH_BYTES = 2 * 1024 * 1024
# Keep pages small enough that a few fetches fit in a local model's context.
_MAX_PAGE_CHARS = 12000


def search(query: str, max_results: int = 8) -> str:
    """Web search via DuckDuckGo. Returns formatted results with URLs."""
    results = list(DDGS().text(query, max_results=max_results))
    if not results:
        return f"No results found for: {query}"
    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "(no title)")
        url = r.get("href") or r.get("url") or ""
        body = (r.get("body") or "").strip()
        lines.append(f"{i}. {title}\n   {url}\n   {body}")
    return "\n".join(lines)


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    _BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "header", "footer", "table", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):  # noqa: ARG002
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n+", "\n\n", raw)
        return raw.strip()


def fetch(url: str) -> str:
    """Fetch a URL and return its readable text content (truncated)."""
    if not url.lower().startswith(("http://", "https://")):
        return f"Refusing to fetch non-http URL: {url}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
            "Accept-Encoding": "gzip, identity",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read(_MAX_FETCH_BYTES)
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
        ctype = resp.headers.get("Content-Type", "")

    charset_match = re.search(r"charset=([\w-]+)", ctype)
    charset = charset_match.group(1) if charset_match else "utf-8"
    text = data.decode(charset, errors="replace")

    if "html" in ctype or text.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        parser = _TextExtractor()
        parser.feed(text)
        text = parser.text()
    elif not ctype.startswith("text/") and "json" not in ctype and "xml" not in ctype:
        return f"Unsupported content type '{ctype}' at {url} — only text-based pages can be read."

    if len(text) > _MAX_PAGE_CHARS:
        text = text[:_MAX_PAGE_CHARS] + f"\n\n[... truncated, page continues — {url}]"
    return text or f"(page at {url} contained no readable text)"
