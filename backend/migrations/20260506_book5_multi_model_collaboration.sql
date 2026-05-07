-- Seed Book 5: Multi-Model Collaboration
-- Apply with psql against the Seed database before enabling Book 5 routers.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS seed_capability_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    thinking_level TEXT NOT NULL DEFAULT 'normal',
    domain_scores JSONB NOT NULL DEFAULT '{}',
    total_probes INT NOT NULL DEFAULT 0,
    total_passed INT NOT NULL DEFAULT 0,
    tested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stale_after TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '3 days'),
    raw_results JSONB NULL
);

CREATE INDEX IF NOT EXISTS idx_cap_profiles_provider_model
    ON seed_capability_profiles(provider, model);

CREATE INDEX IF NOT EXISTS idx_cap_profiles_stale_after
    ON seed_capability_profiles(stale_after);

-- Book 5 collaboration turn log. Originally named seed_model_feedback in GPT's
-- delivery, but Book 1 already owns that name with a different schema (context-
-- anchored, append-only). Renamed here to seed_collab_turns to avoid collision.
CREATE TABLE IF NOT EXISTS seed_collab_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task TEXT NOT NULL,
    pattern TEXT NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    turn_number INT NOT NULL,
    prompt TEXT NULL,
    response_text TEXT NOT NULL DEFAULT '',
    tokens_in INT NOT NULL DEFAULT 0,
    tokens_out INT NOT NULL DEFAULT 0,
    latency_ms INT NOT NULL DEFAULT 0,
    error TEXT NULL,
    raw JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_seed_collab_turns_task_created
    ON seed_collab_turns(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_seed_collab_turns_provider_model
    ON seed_collab_turns(provider, model);
