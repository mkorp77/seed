-- Book 4 migration: Context Brain, Auth, and Search
-- Apply with the same database role used for existing Seed migrations.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS seed_api_keys (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           TEXT NOT NULL,
    key_prefix     TEXT NOT NULL UNIQUE,
    key_hash       TEXT NOT NULL,
    role           TEXT NOT NULL,
    domains        TEXT[] NOT NULL DEFAULT '{}',
    project_ids    UUID[] NULL,
    permissions    TEXT[] NOT NULL DEFAULT '{"read"}',
    format         TEXT NOT NULL DEFAULT 'plain',
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at   TIMESTAMPTZ NULL,
    expires_at     TIMESTAMPTZ NULL,
    revoked_at     TIMESTAMPTZ NULL,
    notes          TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_seed_api_keys_key_prefix ON seed_api_keys (key_prefix);
CREATE INDEX IF NOT EXISTS idx_seed_api_keys_active ON seed_api_keys (active) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_seed_api_keys_role ON seed_api_keys (role);

ALTER TABLE seed_knowledge_nodes
    ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'seed',
    ADD COLUMN IF NOT EXISTS summary_500 TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_seed_knowledge_nodes_domain
    ON seed_knowledge_nodes (domain);

CREATE INDEX IF NOT EXISTS idx_seed_knowledge_nodes_published_domain
    ON seed_knowledge_nodes (domain, published_at)
    WHERE status = 'published';

CREATE INDEX IF NOT EXISTS idx_seed_knowledge_nodes_fts
    ON seed_knowledge_nodes
    USING GIN (
        to_tsvector(
            'english',
            coalesce(title, '') || ' ' || coalesce(summary_500, '') || ' ' || coalesce(body_md, '')
        )
    );

COMMIT;
