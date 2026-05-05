# Seed — Build Briefing

## What We're Building

Open-source sovereign AI workbench. Multi-model chat with air-gapped compute containers that eliminate hallucinations. Bring your own keys, subscriptions, and hardware.

The models think. The containers compute. The wiki remembers. The human decides.

## What v0.1 Does

1. Chat with Claude, GPT, Gemini, and local models from ONE web interface
2. Send the same prompt to multiple models simultaneously and compare responses
3. Execute code in an air-gapped container against real data — computed answers, not guesses
4. Store all conversations, responses, and computed results in PostgreSQL
5. Wiki pages that compound knowledge across sessions
6. Credential management via HashiCorp Vault — no API keys in .env files

## Tech Stack

- Backend: Python FastAPI
- Frontend: Single HTML file, Tailwind CDN, vanilla JS. No build step.
- Database: PostgreSQL
- Credentials: HashiCorp Vault
- Search: Qdrant — wiki page embeddings
- Compute: Docker container, `network_mode: none`, Python + pandas + numpy

## Architecture

```
Browser (localhost:3000)
    │
    ▼
FastAPI Backend (port 3000)
    ├── /api/chat          → streaming responses from any provider
    ├── /api/compare       → same prompt to N models, parallel streams
    ├── /api/compute       → submit code to air-gapped container
    ├── /api/wiki          → CRUD wiki pages
    ├── /api/conversations → conversation history
    └── /api/providers     → list available models, health check
    │
    ├── PostgreSQL
    ├── Vault
    ├── Qdrant — wiki search
    └── seed-compute (network_mode: none) — air-gapped execution
```

## Database Tables

### seed_conversations
```sql
CREATE TABLE seed_conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

### seed_messages
```sql
CREATE TABLE seed_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES seed_conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'compute')),
    content         TEXT NOT NULL,
    provider        TEXT,
    model           TEXT,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    latency_ms      INTEGER,
    cost_usd        NUMERIC(10,6),
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_seed_messages_conv ON seed_messages(conversation_id, created_at);
```

### seed_providers
```sql
CREATE TABLE seed_providers (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    base_url        TEXT,
    vault_key_path  TEXT,
    vault_key_name  TEXT,
    is_active       BOOLEAN DEFAULT true,
    models          JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### seed_wiki
```sql
CREATE TABLE seed_wiki (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    verified        BOOLEAN DEFAULT false,
    verified_at     TIMESTAMPTZ,
    source          TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_seed_wiki_slug ON seed_wiki(slug);
```

### seed_compute_jobs
```sql
CREATE TABLE seed_compute_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES seed_conversations(id),
    code            TEXT NOT NULL,
    status          TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'success', 'error')),
    stdout          TEXT,
    stderr          TEXT,
    result_json     JSONB,
    runtime_ms      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
```

## Provider Adapter Pattern

Each adapter implements one interface:

```python
async def stream_response(
    system_prompt: str,
    messages: list[dict],
    model: str,
    max_tokens: int,
    temperature: float,
) -> AsyncGenerator[str, None]:
    """Yield text chunks as they arrive."""
```

Adapters to build:
1. anthropic_provider.py — `anthropic` SDK, streaming
2. openai_provider.py — `openai` SDK, streaming
3. google_provider.py — `google-genai` SDK, streaming
4. openrouter_provider.py — OpenAI-compatible endpoint
5. local_provider.py — Ollama-compatible endpoint at localhost:11434

All keys fetched from Vault at startup.

## Compute Container

```yaml
seed-compute:
  build: ./Compute
  network_mode: none          # AIR-GAPPED
  deploy:
    resources:
      limits:
        memory: 8g
        cpus: '4'
  volumes:
    - ./data:/workspace/Data:ro
    - ./sandbox:/workspace/Sandbox
    - ./jobs:/workspace/jobs
```

How it works:
1. Model writes Python code in the chat
2. User clicks "Run in Sandbox"
3. Backend writes code to job directory
4. Backend runs code via docker exec
5. stdout/stderr captured, stored in seed_compute_jobs
6. Result displayed in chat as a compute role message

The model NEVER runs code. The container runs code.

## Frontend Layout

```
┌─────────────────────────────────────────────────────┐
│  Seed                              [Wiki] [Settings] │
├──────────┬──────────────────────────────────────────┤
│          │                                          │
│ History  │  Chat Area                               │
│          │                                          │
│ Conv 1   │  [User message]                          │
│ Conv 2   │  [Claude response]     [GPT response]    │
│ Conv 3   │                                          │
│          │  [User message]                          │
│          │  [Gemini response]                       │
│          │                                          │
│          ├──────────────────────────────────────────┤
│          │  Model: [dropdown]  [Compare mode: off]  │
│          │  [Type message...]            [Send]     │
│          │  [Run in Sandbox]                        │
└──────────┴──────────────────────────────────────────┘
```

## Build Phases

| Phase | Deliverable | Days |
|---|---|---|
| 1 | FastAPI skeleton + Vault client + Postgres + Anthropic provider + minimal HTML | 1-2 |
| 2 | All providers + /api/compare + side-by-side UI | 1-2 |
| 3 | Compute container + "Run in Sandbox" | 1 |
| 4 | Wiki CRUD + Qdrant search + conversation history | 1 |
| 5 | Polish + README + GitHub + MIT license | 1 |

## Design Principles

- The model proposes. The container executes. The result is real or it's an error. No third option.
- Priority ladder: real result → disclosed fallback → clear error → never silent degradation
- Container stderr is always surfaced. Never swallowed.
- Gather before building. Ask until 1+1=2.
- If the pattern exists in the wiki, use it. Don't rediscover what's verified.

## Open Source

- License: MIT
- No telemetry, no analytics, no phone-home
- Bring your own keys, subscriptions, and hardware
- One `docker compose up` to start
