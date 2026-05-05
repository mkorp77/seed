"""Discourse fast lane.

Hits /t/{slug}/{topic_id}/print. The print endpoint is a server-side
concatenation of all posts on one HTML page — no JS, no pagination.
We strip nav chrome, avatar images, and the related-topics footer,
then convert what remains to markdown verbatim.
"""
from __future__ import annotations
import re
from urllib.parse import urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from .util import log_event


# Matches /t/{slug}/{id}, /t/{id}, /t/{slug}/{id}/{post_num}, /t/{slug}/{id}/print
_TOPIC_PATH = re.compile(r"^/t/(?:[\w.-]+/)?(\d+)(?:/.*)?/?$")
_DATE_STAMP = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d\d",
    re.IGNORECASE,
)
_AVATAR_PATTERNS = ("avatar", "profile-default", "user_avatar")


def _to_print_url(url: str) -> str | None:
    """Canonicalize any Discourse topic URL to its /print form."""
    parsed = urlparse(url)
    m = _TOPIC_PATH.match(parsed.path)
    if not m:
        return None
    # Discourse accepts /t/{id}/print without the slug; preserve slug if present.
    slug_match = re.match(r"^/t/(?:([\w.-]+)/)?(\d+)", parsed.path)
    if slug_match and slug_match.group(1):
        path = f"/t/{slug_match.group(1)}/{slug_match.group(2)}/print"
    else:
        path = f"/t/{slug_match.group(2)}/print"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _clean_title(raw: str) -> str:
    """Drop the trailing site/forum suffix from <title>."""
    # "Topic Title - DGX Spark / GB10 - NVIDIA Developer Forums"
    parts = re.split(r"\s+[-–|]\s+", raw)
    if len(parts) > 1:
        return parts[0].strip()
    return raw.strip()


def _strip_chrome(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove nav, avatars, related-topics, footer."""
    # Avatar images
    for img in soup.find_all("img"):
        src = (img.get("src") or "").lower()
        alt = (img.get("alt") or "").lower()
        if any(p in src for p in _AVATAR_PATTERNS) or "avatar" in alt:
            img.decompose()

    # Nav and footer elements
    for tag in soup.find_all(["nav", "footer", "header"]):
        tag.decompose()

    # Related topics block — usually a heading "Related topics" followed by a table
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        if h.get_text(strip=True).lower().startswith("related topic"):
            for sib in list(h.find_next_siblings()):
                sib.decompose()
            h.decompose()
            break

    # Skip-to-main and similar a11y links
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("#") and any(
            s in a.get_text(strip=True).lower()
            for s in ("skip to", "jump to", "select all", "cancel selecting")
        ):
            a.decompose()

    return soup


def _trim_to_first_post(markdown: str) -> str:
    """Drop everything before the title heading; keep title + posts onward.

    Discourse /print output usually starts with breadcrumb category links
    above the topic title. We anchor on the first '# ' heading.
    """
    title_match = re.search(r"^# .+$", markdown, re.MULTILINE)
    if not title_match:
        return markdown
    title_line = title_match.group(0)
    # Strip markdown link syntax in title: "# [Topic](/t/...)" -> "# Topic"
    title_clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", title_line)

    after_title = markdown[title_match.end():]
    # The first author profile link marks the start of post 1
    first_user = re.search(r"\[[^\]]+\]\([^)]*/u/[^)]+\)", after_title)
    if first_user:
        return title_clean + "\n\n" + after_title[first_user.start():].lstrip()
    return title_clean + "\n\n" + after_title.lstrip()


def _normalize_whitespace(markdown: str) -> str:
    # Collapse 3+ blank lines to 2
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip() + "\n"


async def scrape(url: str) -> dict:
    """Fetch, parse, and clean a Discourse topic.

    Returns {title, markdown, post_count}.
    Raises httpx.HTTPError on fetch failure, ValueError on URL shape failure.
    """
    print_url = _to_print_url(url)
    if not print_url:
        raise ValueError(f"Not a recognizable Discourse topic URL: {url}")

    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "seed-scraper/0.1 (+https://seed.wiki)"},
    ) as client:
        resp = await client.get(print_url)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    title_tag = soup.find("title")
    title = _clean_title(title_tag.get_text()) if title_tag else ""

    main = (
        soup.find("main")
        or soup.find(id="main-outlet")
        or soup.find(class_="container")
        or soup.body
        or soup
    )
    main = _strip_chrome(main)

    raw_md = markdownify(str(main), heading_style="ATX", bullets="-", strip=["script", "style"])
    raw_md = _trim_to_first_post(raw_md)
    clean_md = _normalize_whitespace(raw_md)

    post_count = len(_DATE_STAMP.findall(clean_md))

    log_event(
        msg="discourse_parsed",
        url=print_url,
        title=title,
        post_count=post_count,
        content_length=len(clean_md),
    )

    return {
        "title": title,
        "markdown": clean_md,
        "post_count": post_count,
        "fetched_url": print_url,
    }
