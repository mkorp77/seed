# Book 4 Apply Instructions

## Files delivered

- `migration_book4.sql` — DDL for `seed_api_keys` and Book 4 columns on `seed_knowledge_nodes`
- `seed_models_book4_delta.py` — `SeedApiKey` ORM model and Book 4 knowledge-node column delta
- `seed_schemas_book4.py` — Pydantic models for auth, brain, search, and nodes
- `seed_auth.py` — API-key auth dependency plus admin key-management router
- `seed_brain.py` — `/brain` endpoint and `json` / `plain` / `markdown` / `skill` renderers
- `seed_search.py` — `/search` Postgres FTS endpoint
- `seed_nodes.py` — knowledge-node lifecycle router
- `smoke_test_book4.py` — admin/auth/brain/search smoke test

## Trust protocol

### CONFIRMED

The implementation follows the locked Book 4 decisions:

- Role is derived from API key, not query param.
- Brain and search are separate endpoints.
- Roles are app-layer config, not a role table.
- Format selection is key default plus allowed override.
- ETag is per response.
- Auth uses prefix lookup plus bcrypt verification.
- Permissions are limited to `read`, `write`, `nodes`, `publish`, and `admin`.
- Route paths are relative to the existing `/api` convention.

### INFERRED

The repo was not attached, so these integration points are inferred:

- FastAPI + SQLAlchemy are the backend primitives.
- `get_db` lives at `seed_database.get_db` or `database.get_db`. If not, update imports in `seed_auth.py`.
- The canonical node model is importable as `seed_models.SeedKnowledgeNode` or `models.SeedKnowledgeNode`. If not, update the import blocks in `seed_brain.py`, `seed_search.py`, and `seed_nodes.py`.
- Empty `domains=[]` on an authenticated API key means no private domain access. Use `domains=["*"]` for all-domain service keys.
- Unauthenticated reads are allowed for `SEED_PUBLIC_DOMAINS`, defaulting to `seed`.
- Context linking assumes `seed_node_context_links(node_id, context_id)` and `seed_context_records(id, created_at, ...)` unless overridden by env vars.

### PROPOSED

Optional environment knobs are included to keep v1 deployable without schema redesign:

- `SEED_PUBLIC_DOMAINS=seed,hardware`
- `SEED_ROLE_FORMATS_JSON='{"reader":["plain","json","markdown","skill"]}'`
- `SEED_API_BASE_URL=https://api.seed.wiki`
- `SEED_VAULT_PATH=/path/to/vault`
- `SEED_SKIP_GIT_COMMIT=1` for local publish testing only
- `SEED_NODE_CONTEXT_LINK_TABLE=seed_node_context_links`
- `SEED_CONTEXT_TABLE=seed_context_records`

### UNKNOWN

These must be verified in the repo:

- Actual database dependency import path.
- Actual canonical ORM module path.
- Existing context-link table name and uniqueness constraints.
- Whether the existing publish implementation should replace the inline git commit in `seed_nodes.py`.

## 1. Copy files into the API service

Copy all Python files into the same module/package area as the existing FastAPI routers.

If the repo uses package-relative imports, convert imports such as:

```python
from seed_auth import get_api_key
```

to:

```python
from .seed_auth import get_api_key
```

Do the same for `seed_models_book4_delta` and `seed_schemas_book4` imports as needed.

## 2. Apply the migration

Run the SQL against the production API database:

```bash
psql "$DATABASE_URL" -f migration_book4.sql
```

If the repo uses Alembic, place the SQL into an Alembic revision and apply with the existing migration process instead.

## 3. Wire the ORM model delta

Add `SeedApiKey` to the ORM metadata import path so migrations and app startup can see the table.

Update the canonical `SeedKnowledgeNode` model with these fields:

```python
domain = mapped_column(Text, nullable=False, server_default=text("'seed'"))
summary_500 = mapped_column(Text, nullable=False, server_default=text("''"))
published_at = mapped_column(DateTime(timezone=True), nullable=True)
last_verified_at = mapped_column(DateTime(timezone=True), nullable=True)
```

You can paste them directly or inherit from `SeedKnowledgeNodeBook4Columns` in `seed_models_book4_delta.py`.

## 4. Install runtime dependencies

The auth implementation needs bcrypt verification:

```bash
pip install bcrypt
```

The smoke test uses `httpx`:

```bash
pip install httpx
```

## 5. Configure environment

Minimum required for admin key creation:

```bash
export SEED_ADMIN_KEY='replace-with-long-random-admin-secret'
```

Recommended:

```bash
export SEED_API_BASE_URL='https://api.seed.wiki'
export SEED_PUBLIC_DOMAINS='seed'
export SEED_VAULT_PATH='/absolute/path/to/seed/vault'
```

For local publish tests without creating git commits:

```bash
export SEED_SKIP_GIT_COMMIT=1
```

## 6. Register routers without double `/api`

If the app has a root API router already mounted at `/api`, include the Book 4 routers without another `/api` prefix:

```python
from seed_auth import router as seed_auth_router
from seed_brain import router as seed_brain_router
from seed_search import router as seed_search_router
from seed_nodes import router as seed_nodes_router

api_router.include_router(seed_auth_router)
api_router.include_router(seed_brain_router)
api_router.include_router(seed_search_router)
api_router.include_router(seed_nodes_router)
```

If the app mounts routers directly on `app`, use exactly one prefix:

```python
app.include_router(seed_auth_router, prefix="/api")
app.include_router(seed_brain_router, prefix="/api")
app.include_router(seed_search_router, prefix="/api")
app.include_router(seed_nodes_router, prefix="/api")
```

Expected routes:

```text
POST   /api/admin/keys
GET    /api/admin/keys
PATCH  /api/admin/keys/{id}
DELETE /api/admin/keys/{id}
GET    /api/brain
GET    /api/search
POST   /api/nodes
GET    /api/nodes
GET    /api/nodes/{id}
PATCH  /api/nodes/{id}
POST   /api/nodes/{id}/publish
POST   /api/nodes/{id}/link
DELETE /api/nodes/{id}/link/{ctx_id}
```

## 7. Create the first reader key

```bash
curl -sS -X POST 'https://api.seed.wiki/api/admin/keys' \
  -H "Authorization: Bearer $SEED_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "default-reader",
    "role": "reader",
    "domains": ["seed"],
    "permissions": ["read"],
    "format": "plain"
  }'
```

The response includes `key` once. Store it immediately. The database stores only prefix plus bcrypt hash.

## 8. Test brain and search

```bash
export SEED_READER_KEY='seed_pk_prefix_secret'

curl -i 'https://api.seed.wiki/api/brain?domain=seed&format=json' \
  -H "Authorization: Bearer $SEED_READER_KEY"

curl -i 'https://api.seed.wiki/api/search?q=thermal&domain=hardware' \
  -H "Authorization: Bearer $SEED_READER_KEY"
```

ETag test:

```bash
ETAG=$(curl -sI 'https://api.seed.wiki/api/brain?domain=seed&format=json' \
  -H "Authorization: Bearer $SEED_READER_KEY" | awk -F': ' 'tolower($1)=="etag" {print $2}' | tr -d '\r')

curl -i 'https://api.seed.wiki/api/brain?domain=seed&format=json' \
  -H "Authorization: Bearer $SEED_READER_KEY" \
  -H "If-None-Match: $ETAG"
```

Expected second response: `304 Not Modified`.

## 9. Run the smoke test

Against local API:

```bash
SEED_ADMIN_KEY="$SEED_ADMIN_KEY" \
SEED_SMOKE_BASE_URL='http://localhost:8000/api' \
SEED_SMOKE_DOMAIN='seed' \
python smoke_test_book4.py
```

Against production API:

```bash
SEED_ADMIN_KEY="$SEED_ADMIN_KEY" \
SEED_SMOKE_BASE_URL='https://api.seed.wiki/api' \
SEED_SMOKE_DOMAIN='seed' \
python smoke_test_book4.py
```

The smoke test creates a temporary reader key, tests admin list/patch/revoke, tests `/brain`, verifies `If-None-Match`, tests `/search`, then verifies revoked-key auth returns `401`.

## 10. Publish behavior

`POST /api/nodes/{id}/publish` does this in order:

1. Loads the node and enforces domain visibility plus `publish` permission.
2. Sets `status='published'`.
3. Sets `published_at=now()`.
4. Computes `body_md_hash`.
5. Writes markdown under `wiki/{domain}/{slug}.md` in `SEED_VAULT_PATH`.
6. Sets `git_path`.
7. Runs `git add` and `git commit` unless `SEED_SKIP_GIT_COMMIT=1`.
8. Commits the database row.

If the repo already has a publish service from earlier books, replace `_write_node_to_vault()` and `_git_commit()` in `seed_nodes.py` with that service call to avoid duplicate publish logic.
