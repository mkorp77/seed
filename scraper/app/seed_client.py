"""Client for the Seed API.

Posts a context record to /api/contexts. The API enforces dedup:
  - 201 Created -> new record, returns id
  - 200 OK      -> existing record matched the dedup key, returns existing id
Either way we get a context_id back. Anything else is a persistence failure;
the scraper still returns the scraped payload so the caller can retry POST.
"""
from __future__ import annotations
import os
from typing import Optional

import httpx

from .util import log_event


SEED_API_URL = os.environ.get("SEED_API_URL", "https://api.seed.wiki")
SEED_API_KEY = os.environ.get("SEED_API_KEY")  # None until auth ships
POST_TIMEOUT_S = 15.0


class SeedPostError(Exception):
    def __init__(self, status: Optional[int], detail: str):
        super().__init__(f"Seed API post failed (status={status}): {detail}")
        self.status = status
        self.detail = detail


async def post_context(record: dict) -> str:
    """POST a context record. Returns context_id on 200 or 201."""
    headers = {"Content-Type": "application/json"}
    if SEED_API_KEY:
        headers["Authorization"] = f"Bearer {SEED_API_KEY}"

    url = f"{SEED_API_URL.rstrip('/')}/api/contexts"

    try:
        async with httpx.AsyncClient(timeout=POST_TIMEOUT_S) as client:
            resp = await client.post(url, json=record, headers=headers)
    except httpx.HTTPError as e:
        raise SeedPostError(None, str(e)) from e

    if resp.status_code in (200, 201):
        try:
            data = resp.json()
        except ValueError as e:
            raise SeedPostError(resp.status_code, f"non-json response: {e}") from e
        cid = data.get("id") or data.get("context_id")
        if not cid:
            raise SeedPostError(resp.status_code, f"response missing id: {data}")
        log_event(
            msg="seed_post",
            status=resp.status_code,
            context_id=cid,
            duplicate=(resp.status_code == 200),
        )
        return cid

    raise SeedPostError(resp.status_code, resp.text[:500])
