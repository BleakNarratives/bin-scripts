# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: stdlib only, DuckDuckGo HTML endpoint
# ROLE: Keyless web search for Anchor
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""web_search — online search without an API key.

Uses DuckDuckGo's HTML endpoint, which needs no key and no account. It is
HTML-scraped, so it can break if their markup changes; the parser is kept
narrow and returns an honest empty result rather than raising.
"""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

ENDPOINT = "https://html.duckduckgo.com/html/"
UA = "Mozilla/5.0 (compatible; anchor-search/1.0)"
RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.S | re.I)
SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return html.unescape(TAG_RE.sub("", fragment)).strip()


def _unwrap(url: str) -> str:
    """DuckDuckGo wraps results as /l/?uddg=<encoded>. Return the real URL."""
    if "uddg=" in url:
        query = urllib.parse.urlparse(url).query
        target = urllib.parse.parse_qs(query).get("uddg", [""])[0]
        if target:
            return target
    return url


def search(query: str, n: int = 5, timeout: int = 15) -> list[dict]:
    data = urllib.parse.urlencode({"q": query}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, method="POST")
    req.add_header("User-Agent", UA)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return [{"title": "search failed", "url": "", "snippet": str(exc)}]

    titles = RESULT_RE.findall(body)
    snippets = SNIPPET_RE.findall(body)
    results: list[dict] = []
    for idx, (url, title) in enumerate(titles[:n]):
        snippet = _clean(snippets[idx]) if idx < len(snippets) else ""
        results.append({"title": _clean(title), "url": _unwrap(url),
                        "snippet": snippet[:300]})
    return results


def format_results(results: list[dict]) -> str:
    if not results:
        return "[web] no results"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(format_results(search(" ".join(sys.argv[1:]) or "python sqlite3")))
