# Seed Book 5 Apply Notes

## Files

Copy these files into the Seed backend package root:

- `seed_provider_config.py`
- `seed_providers.py`
- `seed_probe_bank.py`
- `seed_probes.py`
- `seed_router.py`
- `seed_compare.py`
- `seed_collab.py`

Apply the SQL migration:

- `migrations/20260506_book5_multi_model_collaboration.sql`

Then update `seed_api.py` using `seed_api_book5_include_snippet.py`:

```python
from seed_router import router as seed_model_router
from seed_compare import router as seed_compare_router
from seed_collab import router as seed_collab_router

router.include_router(seed_model_router)
router.include_router(seed_compare_router)
router.include_router(seed_collab_router)
```

The route decorators are intentionally relative:

- `POST /route`
- `POST /route/exec`
- `POST /compare`
- `POST /collab`

Because the live router owns `prefix="/api"`, the externally visible routes become:

- `POST /api/route`
- `POST /api/route/exec`
- `POST /api/compare`
- `POST /api/collab`

## Optional package dependencies

Book 5 degrades gracefully when a provider SDK or API key is missing. Install only the providers you intend to use:

```bash
pip install anthropic openai httpx google-generativeai
```

Environment variables:

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
GOOGLE_AI_API_KEY=...
SEED_LOCAL_ENDPOINT=http://gb10-host:port
SEED_LOCAL_API_KEY=...          # optional, only if your local endpoint requires it
SEED_INTERNAL_API_BASE_URL=...  # optional, used by /api/collab verify pattern to call /api/brain
SEED_INTERNAL_API_KEY=...       # optional, bearer token for internal brain call
```

## Smoke tests

Copy `tests/test_book5_smoke.py` into the backend test suite and run:

```bash
pytest tests/test_book5_smoke.py
```

The smoke tests monkeypatch provider calls and do not require external API keys.

## Calibration

`seed_probes.build_profile(adapter, domain="all")` builds a profile from the probe bank. `seed_probes.save_profile(db, profile)` persists it to `seed_capability_profiles` with a three-day stale window.

Example from a Seed shell:

```python
from seed_providers import get_adapter
from seed_probes import build_profile, save_profile
from seed_deps import SessionLocal

adapter = get_adapter("claude")
profile = build_profile(adapter, domain="all", thinking_level="normal")
with SessionLocal() as db:
    save_profile(db, profile)
```
