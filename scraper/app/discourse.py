"""
Discourse JSON API fetcher.

Uses Discourse's documented JSON API to fetch full threads with pagination.
No HTML scraping. No /print endpoint. No rate-limit games.

API docs: https://docs.discourse.org/
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from .util import sha256_hex, log_event
import time


async def fetch_discourse_thread(url: str) -> dict:
    """
    Fetch a complete Discourse thread via the JSON API.

    Returns a dict matching Seed's context shape:
    {
        "source_uri": str,
        "source_title": str,
        "selected_text": str (clean markdown),
        "content_hash": str,
        "captured_at": str,
        "source_external": {...}
    }
    """
    topic_id = _extract_topic_id(url)
    if not topic_id:
        raise ValueError(f"Cannot extract Discourse topic ID from URL: {url}")

    start = time.time()
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"Accept": "application/json", "User-Agent": "Seed/1.0"}
    ) as client:
        # Step 1: Get topic metadata + first page of posts + full post ID stream
        topic_url = _normalize_base_url(url) + f"/t/{topic_id}.json"
        log_event(msg="discourse_api_fetching_topic", url=topic_url)

        resp = await client.get(topic_url)
        resp.raise_for_status()
        topic = resp.json()

        title = topic.get("title", "")
        all_post_ids = topic.get("post_stream", {}).get("stream", [])
        first_posts = topic.get("post_stream", {}).get("posts", [])

        # Collect first page
        posts_by_id = {p["id"]: p for p in first_posts}
        fetched_ids = set(posts_by_id.keys())

        # Step 2: Paginate remaining posts in chunks of 20
        remaining = [pid for pid in all_post_ids if pid not in fetched_ids]

        for i in range(0, len(remaining), 20):
            chunk = remaining[i:i + 20]
            params = "&".join(f"post_ids[]={pid}" for pid in chunk)
            chunk_url = _normalize_base_url(url) + f"/t/{topic_id}/posts.json?{params}"

            log_event(msg="discourse_api_fetching_chunk", start=i, count=len(chunk))

            chunk_resp = await client.get(chunk_url)
            chunk_resp.raise_for_status()
            chunk_data = chunk_resp.json()

            for p in chunk_data.get("post_stream", {}).get("posts", []):
                posts_by_id[p["id"]] = p

            # Polite pause between requests
            if i + 20 < len(remaining):
                await asyncio.sleep(1.5)
    elapsed_ms = int((time.time() - start) * 1000)

    # Step 3: Assemble posts in order
    ordered_posts = []
    for pid in all_post_ids:
        if pid in posts_by_id:
            ordered_posts.append(posts_by_id[pid])

    # Step 4: Convert to clean markdown
    markdown = _posts_to_markdown(title, ordered_posts)

    content_hash = sha256_hex(markdown)
    now = datetime.now(timezone.utc).isoformat()

    log_event(
        msg="discourse_api_complete",
        title=title,
        post_count=len(ordered_posts),
        total_expected=len(all_post_ids),
        content_length=len(markdown),
        duration_ms=elapsed_ms,
    )

    return {
        "source_uri": url,
        "source_title": title,
        "selected_text": markdown,
        "content_hash": content_hash,
        "captured_at": now,
        "source_external": {
            "platform": "scraper",
            "capture_mode": "full_thread",
            "discourse_post_count": len(ordered_posts),
            "discourse_total_ids": len(all_post_ids),
            "path_taken": "discourse_api",
            "fallback": False,
            "scrape_duration_ms": elapsed_ms,
        }
    }


def _extract_topic_id(url: str) -> str | None:
    """Extract the numeric topic ID from a Discourse URL."""
    # Handles:
    #   /t/some-slug/12345
    #   /t/some-slug/12345/114
    #   /t/12345
    parts = url.rstrip("/").split("/")
    for i, part in enumerate(parts):
        if part == "t" and i + 1 < len(parts):
            # Walk forward to find the numeric ID
            for j in range(i + 1, len(parts)):
                if parts[j].isdigit():
                    return parts[j]
    return None


def _normalize_base_url(url: str) -> str:
    """Extract the base URL (scheme + domain) from a full URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _posts_to_markdown(title: str, posts: list[dict]) -> str:
    """Convert a list of Discourse post objects to clean markdown."""
    lines = [f"# {title}", ""]

    for p in posts:
        username = p.get("username", "unknown")
        created = p.get("created_at", "")
        post_number = p.get("post_number", "?")

        # Format timestamp
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            date_str = dt.strftime("%B %d, %Y %I:%M %p UTC")
        except (ValueError, AttributeError):
            date_str = created

        lines.append(f"## Post #{post_number} by {username}")
        lines.append(f"*{date_str}*")
        lines.append("")

        # Post body is HTML — convert to readable text
        body_html = p.get("cooked", "")
        body_md = _html_to_markdown(body_html)
        lines.append(body_md)
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _html_to_markdown(html: str) -> str:
    """Convert Discourse post HTML to clean markdown."""
    try:
        from markdownify import markdownify
        md = markdownify(html, heading_style="ATX", strip=["img"])
    except ImportError:
        # Fallback: strip HTML tags with regex
        import re
        md = re.sub(r"<[^>]+>", "", html)

    # Clean up whitespace
    lines = md.split("\n")
    cleaned = []
    blank_count = 0
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            blank_count += 1
            if blank_count <= 2:
                cleaned.append("")
        else:
            blank_count = 0
            cleaned.append(stripped)

    return "\n".join(cleaned).strip()


async def scrape(url: str) -> dict:
    result = await fetch_discourse_thread(url)
    return {
        "markdown": result["selected_text"],
        "title": result["source_title"],
        "post_count": result["source_external"]["discourse_post_count"],
        "duration_ms": result["source_external"]["scrape_duration_ms"],
        "source_external": result["source_external"],
    }
