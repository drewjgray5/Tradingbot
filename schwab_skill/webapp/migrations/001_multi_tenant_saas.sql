-- PostgreSQL migration: multi-tenant SaaS foundation
-- Run with: psql "$DATABASE_URL" -f webapp/migrations/001_multi_tenant_saas.sql

BEGIN;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(128) PRIMARY KEY,
    email VARCHAR(255),
    auth_provider VARCHAR(32) NOT NULL DEFAULT 'supabase',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_credentials (
    user_id VARCHAR(128) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token_enc TEXT,
    refresh_token_enc TEXT,
    token_type VARCHAR(32),
    expires_at TIMESTAMPTZ,
    scopes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS orders (
    id VARCHAR(40) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker VARCHAR(16) NOT NULL,
    qty INTEGER NOT NULL,
    side VARCHAR(8) NOT NULL DEFAULT 'BUY',
    order_type VARCHAR(16) NOT NULL DEFAULT 'MARKET',
    price DOUBLE PRECISION,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    broker_order_id VARCHAR(128),
    result_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id_created_at ON orders (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_user_id_status ON orders (user_id, status);

CREATE TABLE IF NOT EXISTS scan_results (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id VARCHAR(64) NOT NULL,
    ticker VARCHAR(16) NOT NULL,
    signal_score DOUBLE PRECISION,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scan_results_user_id_created_at ON scan_results (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scan_results_user_id_job_id ON scan_results (user_id, job_id);

CREATE TABLE IF NOT EXISTS positions (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol VARCHAR(16) NOT NULL,
    qty DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_cost DOUBLE PRECISION,
    market_value DOUBLE PRECISION,
    as_of TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_positions_user_id_symbol_as_of ON positions (user_id, symbol, as_of DESC);

CREATE TABLE IF NOT EXISTS app_state (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key VARCHAR(64) NOT NULL,
    value_json TEXT NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_app_state_user_key ON app_state (user_id, key);

ALTER TABLE pending_trades
    ADD COLUMN IF NOT EXISTS user_id VARCHAR(128);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_pending_trades_user_id'
    ) THEN
        ALTER TABLE pending_trades
            ADD CONSTRAINT fk_pending_trades_user_id
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pending_trades_user_id_created_at ON pending_trades (user_id, created_at DESC);

COMMIT;
