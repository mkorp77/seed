# Seed v0.1 — Specification

## What This Is

Open-source sovereign AI workbench. Multi-model chat with deterministic compute containers that eliminate hallucinations. Bring your own keys, subscriptions, and hardware. Local-first, database-driven, air-gapped compute.

The models think. The containers compute. The wiki remembers. The human decides.

## What v0.1 Does

1. Chat with Claude, GPT, Gemini, and local models from ONE web interface
2. Send the same prompt to multiple models simultaneously and compare responses
3. Execute code in an air-gapped container against real data — computed answers, not guesses
4. Store all conversations, responses, and computed results in PostgreSQL
5. Wiki pages that compound knowledge across sessions (Karpathy pattern)
6. Credential management via HashiCorp Vault — no API keys in .env files

## What v0.1 Does NOT Do

- No multi-user auth (single operator for now)
- No cost routing tiers (v0.2)
- No consumer subscription bridging (v0.2)
- No drag-and-drop between models (v0.2)
- No MCP for sensitive data (air gap only — MCP is for non-sensitive tools)

---

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
    ├── PostgreSQL (existing, port 5432)
    ├── Vault (existing, port 8200)
    ├── Qdrant (existing, port 6333) — wiki search
    └── seed-compute (network_mode: none) — air-gapped execution
```

## Stack

- **Backend:** Python FastAPI (same pattern as existing NotAVault services)
- **Frontend:** Single HTML file with vanilla JS + Tailwind CDN (no build step, no npm, no React)
- **Database:** PostgreSQL (existing container, new tables in `notavault` database)
- **Credentials:** HashiCorp Vault (existing container)
- **Search:** Qdrant (existing container) — wiki page embeddings
- **Compute:** New container, `network_mode: none`, Python + pandas + numpy

---

## PostgreSQL Tables (new, in existing `notavault` database)

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
    provider        TEXT,           -- 'anthropic', 'openai', 'google', 'local', 'compute'
    model           TEXT,           -- 'claude-opus-4-6', 'gpt-5.4', 'gemini-3.1-pro', etc.
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
    id              TEXT PRIMARY KEY,  -- 'anthropic', 'openai', 'google', 'openrouter', 'local'
    display_name    TEXT NOT NULL,
    base_url        TEXT,
    vault_key_path  TEXT,             -- 'secret/data/api' → key name in Vault
    vault_key_name  TEXT,             -- 'anthropic_key', 'openai_key', etc.
    is_active       BOOLEAN DEFAULT true,
    models          JSONB,            -- available models with pricing info
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### seed_wiki
```sql
CREATE TABLE seed_wiki (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            TEXT UNIQUE NOT NULL,  -- 'database-schema', 'container-topology'
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,         -- markdown
    verified        BOOLEAN DEFAULT false, -- human-verified against source
    verified_at     TIMESTAMPTZ,
    source          TEXT,                  -- what was this verified against
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
    code            TEXT NOT NULL,         -- Python code submitted
    status          TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'success', 'error')),
    stdout          TEXT,
    stderr          TEXT,
    result_json     JSONB,                -- structured output if any
    runtime_ms      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ
);
```

---

## Provider Adapters

Same pattern as existing `providers.py`. Each adapter implements:

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

### Adapters to build:
1. **anthropic_provider.py** — `anthropic` SDK, streaming via `client.messages.stream()`
2. **openai_provider.py** — `openai` SDK, streaming via `client.chat.completions.create(stream=True)`
3. **google_provider.py** — existing `providers.py` from Gemini's Home (copy and adapt)
4. **openrouter_provider.py** — OpenAI-compatible endpoint at `https://openrouter.ai/api/v1`
5. **local_provider.py** — Ollama-compatible endpoint at `http://localhost:11434/v1`

All keys fetched from Vault at startup. Cached in memory. Same pattern as existing services.

---

## Compute Container

```yaml
# In docker-compose.yml
seed-compute:
  build: ../Seed_Compute
  network_mode: none          # AIR-GAPPED. No internet. No API calls. No exfiltration.
  deploy:
    resources:
      limits:
        memory: 8g
        cpus: '4'
  volumes:
    - E:/NotAVault/Data:/workspace/Data:ro          # read-only market data
    - E:/NotAVault/Sandbox:/workspace/Sandbox        # read-write scratch space
    - E:/NotAVault/Seed_Compute/jobs:/workspace/jobs  # job input/output
```

**How it works:**
1. Model writes Python code in the chat
2. User clicks "Run in Sandbox" (or model proposes it)
3. Backend writes code to `/workspace/jobs/{job_id}/input.py`
4. Backend runs `docker exec seed-compute python /workspace/jobs/{job_id}/input.py`
5. stdout/stderr captured, written to `seed_compute_jobs` table
6. Result displayed in chat as a `compute` role message

The model NEVER runs code. The container runs code. The model reads the result. That's how you kill hallucinations on computed answers.

---

## Frontend (v0.1 — single HTML file)

### Layout
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

### Key interactions:
- **Single model mode:** Pick a model from dropdown, chat normally
- **Compare mode:** Toggle on, pick 2-3 models, same prompt sent to all, responses shown side-by-side
- **Run in Sandbox:** Code block in chat gets a button, click sends to compute container
- **Wiki panel:** Slide-out panel showing wiki pages, searchable, editable

---

## File Structure

```
E:\Seed\
├── docker-compose.yml          # Seed services only (uses existing notavault-net)
├── .env                        # Points to existing Vault
├── Backend\
│   ├── main.py                 # FastAPI app
│   ├── providers\
│   │   ├── __init__.py
│   │   ├── anthropic_provider.py
│   │   ├── openai_provider.py
│   │   ├── google_provider.py
│   │   ├── openrouter_provider.py
│   │   └── local_provider.py
│   ├── routes\
│   │   ├── chat.py
│   │   ├── compare.py
│   │   ├── compute.py
│   │   ├── wiki.py
│   │   └── conversations.py
│   ├── vault_client.py         # Copy from existing services
│   ├── database.py             # PostgreSQL connection
│   └── Dockerfile
├── Frontend\
│   └── index.html              # Single file. Tailwind CDN. Vanilla JS. No build step.
├── Compute\
│   ├── Dockerfile              # Python 3.12 + pandas + numpy + scipy
│   └── requirements.txt
└── CLAUDE.md                   # Boot file for Claude Code sessions
```

---

## Build Order

### Phase 1: Backend skeleton (Day 1)
- [ ] FastAPI app with health check
- [ ] Vault client (copy from existing)
- [ ] PostgreSQL connection + table creation
- [ ] One working provider (Anthropic — you use it most)
- [ ] `/api/chat` streaming endpoint
- [ ] Minimal HTML page that sends a message and shows the stream

### Phase 2: Multi-model (Day 2)
- [ ] OpenAI provider
- [ ] Google provider (adapt existing providers.py)
- [ ] Model selector in frontend
- [ ] `/api/compare` endpoint
- [ ] Side-by-side display

### Phase 3: Compute (Day 3)
- [ ] Compute container Dockerfile
- [ ] `/api/compute` endpoint
- [ ] "Run in Sandbox" button on code blocks
- [ ] Results displayed as compute messages

### Phase 4: Wiki (Day 4)
- [ ] Wiki CRUD routes
- [ ] Wiki panel in frontend
- [ ] First page: database schema (verified today)
- [ ] Qdrant embedding on wiki save for search

### Phase 5: Polish + Ship (Day 5)
- [ ] Conversation history sidebar
- [ ] Settings panel (provider config)
- [ ] README.md for GitHub
- [ ] MIT license
- [ ] First commit

---

## Connection to Existing Stack

Seed runs on the SAME `notavault-net` Docker network. It connects to:
- PostgreSQL at `postgresql:5432` (existing container)
- Vault at `vault:8200` (existing container)
- Qdrant at `qdrant:6333` (existing container)

Seed does NOT replace NotAVault. It's the workbench you use to BUILD NotAVault. The chart app, the lab pipe, the studies — those stay. Seed is how you talk to the models, compute results, and accumulate knowledge while building everything else.

---

## Open Source Notes

- License: MIT
- Repo: github.com/[tbd]/seed
- No telemetry, no analytics, no phone-home
- Bring your own keys, subscriptions, and hardware
- One `docker compose up` to start
- Works on a laptop with 16GB RAM or a Grace Blackwell cluster
- Medical records, financial data, trade secrets — never leave your machine
