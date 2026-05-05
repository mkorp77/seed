# seed-scraper

HTTP scraper for Seed. One endpoint: `POST /scrape`. Routes Discourse forums to a fast HTTP path, everything else to a Playwright fallback. Optionally persists to the Seed API.

## Run

```bash
docker compose up --build
```

Health check:

```bash
curl http://localhost:3000/healthz
```

## Environment

| Var | Default | Notes |
|---|---|---|
| `SEED_API_URL` | `https://api.seed.wiki` | |
| `SEED_API_KEY` | unset | Set when auth ships. Format: `seed_pk_<prefix>_<secret>` |
| `DETECTION_CACHE_PATH` | `/app/data/detection_cache.json` | Persisted per-domain routing decisions |

## Wire format

### Request

```json
{
  "url": "https://forums.developer.nvidia.com/t/introducing-prismaquant/367085",
  "project_id": "uuid-or-null",
  "post_to_seed": true,
  "force_path": null
}
```

`force_path`: `"discourse_api" | "playwright" | null` (null = auto-route).

### Response — success

```json
{
  "ok": true,
  "path_taken": "discourse_api",
  "fallback": false,
  "context": { "...": "matches Seed API contract" },
  "context_id": "uuid-from-seed-api"
}
```

`context_id` is null if `post_to_seed: false` or if the Seed POST failed (in which case `source_external.seed_post_error` is populated and the caller can retry).

### Response — failure

```json
{
  "ok": false,
  "error": "both_paths_failed",
  "attempts": [
    {"path": "discourse_api", "status": 503, "error": "..."},
    {"path": "playwright", "status": null, "error": "timeout after 30s"}
  ]
}
```

## Routing

1. Resolve domain.
2. Check `detection_cache.json` (30-day TTL).
3. Cache miss → probe `https://{domain}/about.json`; valid Discourse JSON ⇒ `discourse`, else `generic`.
4. Stale entries return cached value and refresh in the background.
5. `discourse` → fast lane (`/print` + httpx + bs4). `generic` → Playwright + trafilatura.
6. `force_path` overrides routing.

## Discourse fast lane

- Hits `/t/{slug}/{topic_id}/print` (server-side concatenation of all posts).
- Strips: nav breadcrumbs, avatar images, related-topics block, footer, header.
- Preserves: post bodies, quoted blocks, code fences, tables, inline links — verbatim.
- Counts posts by date stamp.

## Failure semantics

`discourse_api` failure → automatic Playwright retry, response carries `fallback: true`. `playwright` failure with no further fallback → 502 with both attempts logged. The scraper never silently drops a successful scrape: if the Seed POST fails, the scrape result is still returned with the error recorded inline.

## Logging

Every meaningful event is one JSON line on stdout. View with:

```bash
docker logs -f seed-scraper
```

Fields seen: `ts`, `msg`, `url`, `path_taken`, `fallback`, `duration_ms`, `status`, `content_length`, `context_id`, plus event-specific extras.
