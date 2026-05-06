"""Book 4 API smoke test.

Usage:
    SEED_ADMIN_KEY=... python smoke_test_book4.py

Optional env:
    SEED_SMOKE_BASE_URL=http://localhost:8000/api
    SEED_SMOKE_DOMAIN=seed
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx


BASE_URL = os.getenv("SEED_SMOKE_BASE_URL", "http://localhost:8000/api").rstrip("/")
ADMIN_KEY = os.getenv("SEED_ADMIN_KEY")
DOMAIN = os.getenv("SEED_SMOKE_DOMAIN", "seed")


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text


def _assert_status(resp: httpx.Response, allowed: set[int], label: str) -> None:
    if resp.status_code not in allowed:
        raise AssertionError(f"{label} expected {sorted(allowed)}, got {resp.status_code}: {_json(resp)}")


async def main() -> int:
    if not ADMIN_KEY:
        print("SKIP: SEED_ADMIN_KEY is not set", file=sys.stderr)
        return 2

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        admin_headers = _headers(ADMIN_KEY)

        create_payload = {
            "name": "book4-smoke-reader",
            "role": "reader",
            "domains": [DOMAIN],
            "permissions": ["read"],
            "format": "json",
            "notes": "Created by smoke_test_book4.py; safe to revoke.",
        }
        created = await client.post("/admin/keys", json=create_payload, headers=admin_headers)
        _assert_status(created, {200}, "POST /admin/keys")
        created_json = created.json()
        raw_key = created_json["key"]
        key_id = created_json["record"]["id"]
        api_headers = _headers(raw_key)
        print(f"created_key_prefix={created_json['record']['key_prefix']}")

        listed = await client.get("/admin/keys", headers=admin_headers)
        _assert_status(listed, {200}, "GET /admin/keys")
        assert any(row["id"] == key_id for row in listed.json()), "created key missing from key list"
        print(f"admin_key_count={len(listed.json())}")

        brain = await client.get(f"/brain?domain={DOMAIN}&format=json", headers=api_headers)
        _assert_status(brain, {200}, "GET /brain")
        brain_json = brain.json()
        assert "etag" in brain_json, "brain response missing etag"
        print(f"brain_node_count={brain_json.get('node_count')} etag={brain_json.get('etag')}")

        etag = brain.headers.get("etag") or brain_json.get("etag")
        cached = await client.get(
            f"/brain?domain={DOMAIN}&format=json",
            headers={**api_headers, "If-None-Match": etag},
        )
        _assert_status(cached, {304}, "GET /brain If-None-Match")
        print("brain_etag_304=true")

        search = await client.get(f"/search?q=seed&domain={DOMAIN}", headers=api_headers)
        _assert_status(search, {200}, "GET /search")
        search_json = search.json()
        assert search_json.get("degraded") is False, "search degraded should be false for v1"
        print(f"search_results={len(search_json.get('results', []))}")

        patched = await client.patch(
            f"/admin/keys/{key_id}",
            json={"name": "book4-smoke-reader-patched", "format": "plain"},
            headers=admin_headers,
        )
        _assert_status(patched, {200}, "PATCH /admin/keys/{id}")
        assert patched.json()["format"] == "plain", "patch did not update key format"
        print("admin_patch=true")

        revoked = await client.delete(f"/admin/keys/{key_id}", headers=admin_headers)
        _assert_status(revoked, {200}, "DELETE /admin/keys/{id}")
        assert revoked.json()["revoked_at"], "revoked key missing revoked_at"
        print("admin_revoke=true")

        revoked_check = await client.get(f"/brain?domain={DOMAIN}&format=json", headers=api_headers)
        _assert_status(revoked_check, {401}, "revoked key auth check")
        print("revoked_key_401=true")

    print("BOOK4_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
