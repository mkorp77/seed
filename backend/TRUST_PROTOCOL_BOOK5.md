# Trust Protocol — Book 5

## CONFIRMED

- The live API prefix belongs in `seed_api.py` as `prefix="/api"`; Book 5 routers use relative paths only.
- Existing dependency conventions are used: `get_db` from `seed_deps.py` and `get_api_key` from `seed_auth.py`.
- Routes are sync `def` functions with `db = Depends(get_db)`.
- Provider adapters return structured `ProviderResponse` errors instead of raising provider exceptions to caller routes.
- Capability profiles decay after three days via `stale_after`.
- Smoke tests assert every requested route: `/route`, `/route/exec`, `/compare`, and `/collab`.

## INFERRED

- The existing app can include three routers from `seed_api.py` by importing `seed_router`, `seed_compare`, and `seed_collab`.
- A raw SQL migration is safer than requiring immediate edits to `seed_models.py`; Book 5 reads/writes the new tables using SQLAlchemy `text()` to stay lean.
- If no fresh profiles exist, the router should still return a deterministic recommendation, but it marks the reasoning as uncalibrated and requires soaking for risky tasks.
- `models` entries may use either provider names (`"claude"`) or provider/model shorthand (`"gpt:gpt-4o"`).

## PROPOSED

- `seed_model_feedback` is created as a lean collaboration turn log because Book 5 requires storing each collab turn there but the brief did not provide its schema.
- `/api/collab` verify mode calls `/api/brain` through `SEED_INTERNAL_API_BASE_URL` when configured. Without that variable, it returns verification status `unavailable` rather than guessing.
- High-risk routing sets `soak_required=true`; route execution still calls the selected model because the model call itself is read-only.

## UNKNOWN

- The exact existing `seed_model_feedback` schema, if any.
- The exact request shape of the existing `/api/brain` endpoint. Verify mode tries POST `{query, limit}`, then GET `q`, then GET `query`.
- Whether all provider SDK versions deployed on the server support the newest thinking parameters. Adapters include fallback/error paths where SDK support differs.
