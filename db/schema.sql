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
-- plans: subscription catalogue (backend upserts the rows from app/billing/plans.py
-- on every start; -1 = unlimited, 0 = not included).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plans (
    id                   VARCHAR(20) PRIMARY KEY,                -- FREE / PRO / BUSINESS
    name                 VARCHAR(60) NOT NULL,
    price_monthly_cents  INTEGER     NOT NULL DEFAULT 0 CHECK (price_monthly_cents >= 0),
    currency             VARCHAR(3)  NOT NULL DEFAULT 'USD',
    monthly_tokens       INTEGER     NOT NULL DEFAULT 0,
    max_machines         INTEGER     NOT NULL DEFAULT 1,
    max_tasks_per_day    INTEGER     NOT NULL DEFAULT 0,
    max_webhooks         INTEGER     NOT NULL DEFAULT 0,
    max_api_keys         INTEGER     NOT NULL DEFAULT 0,
    training_access      BOOLEAN     NOT NULL DEFAULT TRUE,
    inbound_webhooks     BOOLEAN     NOT NULL DEFAULT FALSE,
    features_json        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    sort_order           INTEGER     NOT NULL DEFAULT 0,
    is_active            BOOLEAN     NOT NULL DEFAULT TRUE
);
ALTER TABLE plans ADD COLUMN IF NOT EXISTS max_bots INTEGER NOT NULL DEFAULT 1;   -- Universal Bot API limit
ALTER TABLE plans ADD COLUMN IF NOT EXISTS max_tunnels INTEGER NOT NULL DEFAULT 1;  -- HTTP tunnel limit
INSERT INTO plans (id, name, price_monthly_cents, currency, monthly_tokens, max_machines, max_tasks_per_day,
                   max_webhooks, max_api_keys, training_access, inbound_webhooks, features_json, sort_order, is_active, max_bots, max_tunnels) VALUES
    ('FREE',     'Free',        0, 'USD',   5000,  1,   50,  1,  1, TRUE, FALSE, '[]'::jsonb, 0, TRUE,  1,  1),
    ('PRO',      'Pro',      1900, 'USD', 100000,  5, 1000, 10,  5, TRUE, TRUE,  '[]'::jsonb, 1, TRUE, 10,  5),
    ('BUSINESS', 'Business', 7900, 'USD', 500000, 25,   -1, 50, 20, TRUE, TRUE,  '[]'::jsonb, 2, TRUE, 50, 20)
ON CONFLICT (id) DO NOTHING;
UPDATE plans SET max_tunnels = CASE id WHEN 'PRO' THEN 5 WHEN 'BUSINESS' THEN 20 ELSE 1 END WHERE id IN ('FREE','PRO','BUSINESS');

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
-- Account + subscription columns (ADD COLUMN IF NOT EXISTS = safe upgrade of v1 databases).
-- role is derived from ADMIN_EMAIL by the backend on every request.
ALTER TABLE users ADD COLUMN IF NOT EXISTS role                 VARCHAR(10) NOT NULL DEFAULT 'user';
ALTER TABLE users ADD COLUMN IF NOT EXISTS status               VARCHAR(10) NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_id              VARCHAR(20) NOT NULL DEFAULT 'FREE' REFERENCES plans(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status  VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE users ADD COLUMN IF NOT EXISTS current_period_start TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS current_period_end   TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used_period   INTEGER     NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tasks_today          INTEGER     NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tasks_day            VARCHAR(10);
ALTER TABLE users ADD COLUMN IF NOT EXISTS inbound_hook_token   VARCHAR(64) UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS billing_customer_ref VARCHAR(120);
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at        TIMESTAMPTZ;
DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_role_chk   CHECK (role IN ('user','admin'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_status_chk CHECK (status IN ('ACTIVE','SUSPENDED','BANNED'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE users ADD CONSTRAINT users_sub_status_chk CHECK (subscription_status IN ('ACTIVE','PENDING_UPGRADE','PAST_DUE','CANCELED'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at);
CREATE INDEX IF NOT EXISTS idx_users_plan_id    ON users (plan_id);
CREATE INDEX IF NOT EXISTS ix_users_email       ON users (email);

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
-- upgrade_requests: "request upgrade" flow → admin approval (or provider webhook).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS upgrade_requests (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    from_plan_id       VARCHAR(20) NOT NULL REFERENCES plans(id),
    requested_plan_id  VARCHAR(20) NOT NULL REFERENCES plans(id),
    status             VARCHAR(10) NOT NULL DEFAULT 'PENDING'
                       CONSTRAINT upgrade_status_chk CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELED')),
    payment_provider   VARCHAR(20) NOT NULL DEFAULT 'manual',      -- manual | stripe | paddle
    payment_reference  VARCHAR(200),
    amount_cents       INTEGER     NOT NULL DEFAULT 0,
    months             INTEGER     NOT NULL DEFAULT 1,
    user_note          TEXT,
    admin_note         TEXT,
    decided_by         UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_upgrade_requests_user_id ON upgrade_requests (user_id);
CREATE INDEX IF NOT EXISTS idx_upgrade_status         ON upgrade_requests (status);

-- -----------------------------------------------------------------------------
-- payments: revenue ledger (manual approvals today; Stripe/Paddle later).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id             VARCHAR(20) NOT NULL REFERENCES plans(id),
    upgrade_request_id  UUID        REFERENCES upgrade_requests(id) ON DELETE SET NULL,
    amount_cents        INTEGER     NOT NULL DEFAULT 0,
    currency            VARCHAR(3)  NOT NULL DEFAULT 'USD',
    provider            VARCHAR(20) NOT NULL DEFAULT 'manual',
    reference           VARCHAR(200),
    status              VARCHAR(12) NOT NULL DEFAULT 'SUCCEEDED',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_payments_user_id    ON payments (user_id);
CREATE INDEX IF NOT EXISTS ix_payments_created_at ON payments (created_at);

-- -----------------------------------------------------------------------------
-- api_keys: personal REST API keys (bbu_…); SHA-256 stored, raw shown once.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          VARCHAR(80) NOT NULL,
    prefix        VARCHAR(16) NOT NULL,
    key_hash      VARCHAR(64) NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_api_keys_user_id ON api_keys (user_id);

-- -----------------------------------------------------------------------------
-- webhooks + webhook_deliveries: outbound HMAC-signed events with retry log.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS webhooks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url          VARCHAR(1000) NOT NULL,
    events_json  JSONB         NOT NULL DEFAULT '[]'::jsonb,
    secret       VARCHAR(80)   NOT NULL,
    description  VARCHAR(200),
    is_active    BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_webhooks_user_id ON webhooks (user_id);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id       UUID        NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    event            VARCHAR(50) NOT NULL,
    payload_json     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status           VARCHAR(10) NOT NULL DEFAULT 'PENDING'
                     CONSTRAINT delivery_status_chk CHECK (status IN ('PENDING','SUCCESS','FAILED')),
    attempts         INTEGER     NOT NULL DEFAULT 0,
    response_code    INTEGER,
    response_body    TEXT,
    error            TEXT,
    next_attempt_at  TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_webhook_id ON webhook_deliveries (webhook_id);
CREATE INDEX IF NOT EXISTS idx_delivery_pending             ON webhook_deliveries (status, next_attempt_at);

-- =============================================================================
-- Universal Bot Bridge + Master Bot Marketplace
-- =============================================================================
-- marketplace_masters: Master Bots published by the platform owner (admin).
CREATE TABLE IF NOT EXISTS marketplace_masters (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug               VARCHAR(80)  NOT NULL UNIQUE,
    name               VARCHAR(120) NOT NULL,
    avatar             VARCHAR(16)  NOT NULL DEFAULT '🤖',
    tagline            VARCHAR(200) NOT NULL DEFAULT '',
    description        TEXT         NOT NULL DEFAULT '',
    persona            TEXT         NOT NULL DEFAULT '',
    skill_payload_json JSONB        NOT NULL DEFAULT '{}'::jsonb,
    tools_json         JSONB        NOT NULL DEFAULT '[]'::jsonb,
    welcome_message    TEXT         NOT NULL DEFAULT '',
    test_prompt        TEXT         NOT NULL DEFAULT '',
    test_expect        VARCHAR(200),
    min_plan_id        VARCHAR(20)  NOT NULL DEFAULT 'FREE' REFERENCES plans (id),
    is_published       BOOLEAN      NOT NULL DEFAULT FALSE,
    created_by         UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- bots: any agent bot linked to the platform (connector or generic Bot API).
CREATE TABLE IF NOT EXISTS bots (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name          VARCHAR(120) NOT NULL,
    framework     VARCHAR(30)  NOT NULL DEFAULT 'custom',     -- hermes, openclaw, n8n, langchain, http, grok, cli, custom
    delivery      VARCHAR(12)  NOT NULL DEFAULT 'poll'
                  CONSTRAINT bots_delivery_chk CHECK (delivery IN ('poll','webhook','websocket','connector')),
    webhook_url   VARCHAR(1000),
    connector_id  UUID REFERENCES connectors (id) ON DELETE SET NULL,
    agent_name    VARCHAR(120) NOT NULL DEFAULT 'default',
    token_hash    VARCHAR(64)  NOT NULL UNIQUE,               -- SHA-256 of the bbt_ token
    token_prefix  VARCHAR(16)  NOT NULL,
    secret        VARCHAR(80)  NOT NULL,                      -- whsec_ used to sign pushes to the bot
    status        VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE' CONSTRAINT bots_status_chk CHECK (status IN ('ACTIVE','DISABLED')),
    persona       TEXT,
    skills_json   JSONB        NOT NULL DEFAULT '[]'::jsonb,
    last_seen_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bots_user_id ON bots (user_id);

-- bot_messages: inbox ('in', polled via GET /api/bot/updates?offset=) and outbox ('out').
CREATE TABLE IF NOT EXISTS bot_messages (
    id           SERIAL PRIMARY KEY,
    bot_id       UUID NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    direction    VARCHAR(3)  NOT NULL CONSTRAINT bot_msg_direction_chk CHECK (direction IN ('in','out')),
    kind         VARCHAR(20) NOT NULL DEFAULT 'message',    -- message, welcome, skill_inject, task, system
    chat         VARCHAR(10) NOT NULL DEFAULT 'direct',     -- direct | group
    group_id     UUID REFERENCES marketplace_masters (id) ON DELETE SET NULL,
    sender       VARCHAR(160) NOT NULL DEFAULT 'owner',
    text         TEXT NOT NULL DEFAULT '',
    payload_json JSONB,
    reply_to     INTEGER,
    delivered_at TIMESTAMPTZ,
    acked_at     TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bot_messages_bot_id ON bot_messages (bot_id);
CREATE INDEX IF NOT EXISTS idx_bot_msg_poll       ON bot_messages (bot_id, direction, id);

-- master_group_members: bots inside a Master Bot's messenger group + onboarding state.
CREATE TABLE IF NOT EXISTS master_group_members (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    master_id       UUID NOT NULL REFERENCES marketplace_masters (id) ON DELETE CASCADE,
    bot_id          UUID NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    status          VARCHAR(10) NOT NULL DEFAULT 'JOINED'
                    CONSTRAINT group_member_status_chk CHECK (status IN ('JOINED','WAITING','INJECTING','INJECTED','TESTING','GRADUATED','FAILED')),
    is_master_clone BOOLEAN NOT NULL DEFAULT FALSE,
    skill_id        VARCHAR(120),
    inject_ref      VARCHAR(64),
    test_ref        VARCHAR(64),
    last_error      TEXT,
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    injected_at     TIMESTAMPTZ,
    graduated_at    TIMESTAMPTZ,
    CONSTRAINT uq_group_member UNIQUE (master_id, bot_id)
);
CREATE INDEX IF NOT EXISTS ix_master_group_members_master_id ON master_group_members (master_id);
CREATE INDEX IF NOT EXISTS ix_master_group_members_bot_id    ON master_group_members (bot_id);
CREATE INDEX IF NOT EXISTS ix_master_group_members_user_id   ON master_group_members (user_id);

-- master_group_messages: the group feed.
CREATE TABLE IF NOT EXISTS master_group_messages (
    id            SERIAL PRIMARY KEY,
    master_id     UUID NOT NULL REFERENCES marketplace_masters (id) ON DELETE CASCADE,
    sender_type   VARCHAR(10)  NOT NULL,                     -- master | bot | system
    sender_bot_id UUID REFERENCES bots (id) ON DELETE SET NULL,
    sender_name   VARCHAR(160) NOT NULL,
    text          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_master_group_messages_master_id ON master_group_messages (master_id);

-- -----------------------------------------------------------------------------
-- HTTP tunnels (ngrok-style): https://<host>/t/{slug}/… → connector WS → 127.0.0.1:{local_port}
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tunnels (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    connector_id   UUID NOT NULL REFERENCES connectors (id) ON DELETE CASCADE,
    slug           VARCHAR(48)  NOT NULL UNIQUE,              -- public path segment /t/{slug}/
    name           VARCHAR(120) NOT NULL DEFAULT '',
    local_port     INTEGER      NOT NULL CONSTRAINT tunnels_port_chk CHECK (local_port BETWEEN 1 AND 65535),
    token_hash     VARCHAR(64),                               -- SHA-256 of optional tnl_ access token
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    requests_count INTEGER      NOT NULL DEFAULT 0,
    bytes_in       BIGINT       NOT NULL DEFAULT 0,
    bytes_out      BIGINT       NOT NULL DEFAULT 0,
    last_status    INTEGER,
    last_used_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tunnels_user_id      ON tunnels (user_id);
CREATE INDEX IF NOT EXISTS ix_tunnels_connector_id ON tunnels (connector_id);

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
ALTER TABLE plans              ENABLE ROW LEVEL SECURITY;
ALTER TABLE upgrade_requests   ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments           ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys           ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhooks           ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketplace_masters   ENABLE ROW LEVEL SECURITY;
ALTER TABLE bots                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_messages          ENABLE ROW LEVEL SECURITY;
ALTER TABLE master_group_members  ENABLE ROW LEVEL SECURITY;
ALTER TABLE master_group_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE tunnels               ENABLE ROW LEVEL SECURITY;
