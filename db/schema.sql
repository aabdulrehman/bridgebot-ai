-- =============================================================================
-- BridgeBot AI — PostgreSQL / Supabase schema
-- -----------------------------------------------------------------------------
-- Run in the Supabase SQL editor (or `psql "$DATABASE_URL" -f db/schema.sql`).
-- The FastAPI backend can also create these tables automatically on startup
-- (AUTO_CREATE_TABLES=true), but this file is the canonical, fully-indexed DDL.
-- Idempotent: safe to run multiple times.
-- =============================================================================

-- gen_random_uuid() lives in pgcrypto (enabled by default on Supabase).
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -----------------------------------------------------------------------------
-- users: platform accounts. token_balance = platform credits used by Service 2.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email          VARCHAR(320) NOT NULL UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,                 -- bcrypt hash, never plaintext
    token_balance  INTEGER      NOT NULL DEFAULT 10000
                   CONSTRAINT users_token_balance_nonneg CHECK (token_balance >= 0),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at);

-- -----------------------------------------------------------------------------
-- connectors: one row per local machine running local_connector.py.
-- api_key_hash = SHA-256 of the raw key (raw key is shown to the user once).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS connectors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    machine_name    VARCHAR(120) NOT NULL,
    api_key_hash    CHAR(64)     NOT NULL UNIQUE,
    status          VARCHAR(10)  NOT NULL DEFAULT 'OFFLINE'
                    CONSTRAINT connectors_status_chk CHECK (status IN ('ONLINE', 'OFFLINE')),
    last_heartbeat  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT connectors_user_machine_uq UNIQUE (user_id, machine_name)
);
CREATE INDEX IF NOT EXISTS idx_connectors_user_id ON connectors (user_id);
CREATE INDEX IF NOT EXISTS idx_connectors_status  ON connectors (status);

-- -----------------------------------------------------------------------------
-- agents: AI runtimes (n8n, Hermes, LangChain, Ollama...) living behind a connector.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agents (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connector_id                  UUID         NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    agent_name                    VARCHAR(120) NOT NULL,
    agent_type                    VARCHAR(50)  NOT NULL DEFAULT 'generic'
                                  CONSTRAINT agents_type_chk CHECK (agent_type IN
                                  ('generic','n8n','hermes','langchain','ollama','llamacpp','custom')),
    current_injected_skills_json  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_at                    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT agents_connector_name_uq UNIQUE (connector_id, agent_name)
);
CREATE INDEX IF NOT EXISTS idx_agents_connector_id ON agents (connector_id);

-- -----------------------------------------------------------------------------
-- master_bots: platform-side "teacher" bots that synthesize skill payloads.
-- owner_id NULL = built-in platform bot.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS master_bots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            UUID         REFERENCES users(id) ON DELETE SET NULL,
    bot_name            VARCHAR(120) NOT NULL UNIQUE,
    description         TEXT         NOT NULL DEFAULT '',
    pricing_tier        VARCHAR(20)  NOT NULL DEFAULT 'FREE'
                        CONSTRAINT master_bots_tier_chk CHECK (pricing_tier IN ('FREE','STANDARD','PREMIUM')),
    skill_payload_json  JSONB        NOT NULL DEFAULT '{}'::jsonb,   -- base template / system prompt
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_master_bots_owner_id ON master_bots (owner_id);

-- -----------------------------------------------------------------------------
-- training_requests: Service 2 lifecycle PENDING -> TRAINING -> READY -> APPROVED
-- (FAILED is an extra terminal state used when generation errors out).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS training_requests (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    master_bot_id            UUID        REFERENCES master_bots(id) ON DELETE SET NULL,
    target_connector_id      UUID        REFERENCES connectors(id) ON DELETE SET NULL,
    target_agent_name        VARCHAR(120) NOT NULL DEFAULT 'default',
    niche_requirements_text  TEXT        NOT NULL,
    status                   VARCHAR(10) NOT NULL DEFAULT 'PENDING'
                             CONSTRAINT training_status_chk CHECK
                             (status IN ('PENDING','TRAINING','READY','APPROVED','FAILED')),
    token_cost               INTEGER     NOT NULL DEFAULT 0
                             CONSTRAINT training_token_cost_nonneg CHECK (token_cost >= 0),
    generated_payload_json   JSONB,                       -- compiled Skill Payload (READY+)
    generator                VARCHAR(120),                -- e.g. 'openrouter:<model>' or 'template-fallback'
    error_message            TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at              TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_training_user_id ON training_requests (user_id);
CREATE INDEX IF NOT EXISTS idx_training_status  ON training_requests (status);
CREATE INDEX IF NOT EXISTS idx_training_created ON training_requests (created_at DESC);

-- -----------------------------------------------------------------------------
-- audit_logs: every connector event (connect, task, output, skill inject...).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connector_id  UUID        REFERENCES connectors(id) ON DELETE CASCADE,
    event_type    VARCHAR(50) NOT NULL,
    payload_json  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_connector_created ON audit_logs (connector_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_event_type        ON audit_logs (event_type);

-- -----------------------------------------------------------------------------
-- Seed: built-in free master bot.
-- -----------------------------------------------------------------------------
INSERT INTO master_bots (bot_name, description, pricing_tier, skill_payload_json)
VALUES ('Genesis', 'Default platform master bot that compiles niche skill payloads.', 'FREE',
        '{"system_prompt_style":"expert-operator","version":1}'::jsonb)
ON CONFLICT (bot_name) DO NOTHING;

-- -----------------------------------------------------------------------------
-- Supabase hardening: the backend connects with the service/postgres role, so
-- enable RLS with no public policies to block direct anon-key access.
-- -----------------------------------------------------------------------------
ALTER TABLE users             ENABLE ROW LEVEL SECURITY;
ALTER TABLE connectors        ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents            ENABLE ROW LEVEL SECURITY;
ALTER TABLE master_bots       ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs        ENABLE ROW LEVEL SECURITY;
