"""Generic Playwright lane.

Renders the page in headless Chromium then extracts main content via
trafilatura. Default wait_until is 'domcontentloaded' because Discourse
(and many other sites) hold long-polling connections that prevent
'networkidle' from ever firing.
"""
from __future__ import annotations
from typing import Literal

import trafilatura
from playwright.async_api import async_playwright

from .util import log_event


WaitUntil = Literal["domcontentloaded", "load", "networkidle"]


async def scrape(
    url: str,
    wait_until: WaitUntil = "domcontentloaded",
    timeout_ms: int = 30000,
) -> dict:
    """Render URL and extract main content as markdown.

    Returns {title, markdown}.
    Raises RuntimeError if the page renders empty or extraction yields nothing useful.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="seed-scraper/0.1 (+https://seed.wiki)"
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            html = await page.content()
            title = await page.title()
        finally:
            await context.close()
            await browser.close()

    markdown = trafilatura.extract(
        html,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        include_comments=False,
        favor_recall=True,
    )

    if not markdown or len(markdown.strip()) < 200:
        log_event(msg="generic_extract_short", url=url, length=len(markdown or ""))
        raise RuntimeError(
            f"Generic extraction yielded {len(markdown or '')} chars; likely a render or paywall failure"
        )

    return {
        "title": (title or "").strip(),
        "markdown": markdown.strip() + "\n",
    }
