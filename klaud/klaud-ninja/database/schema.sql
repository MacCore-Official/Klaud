-- ═══════════════════════════════════════════════════════════════
-- KLAUD-NINJA — Supabase Schema
-- Run this once in the Supabase SQL editor to create all tables.
-- ═══════════════════════════════════════════════════════════════

-- ── guild_settings ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id          TEXT        PRIMARY KEY,
    moderation_level  TEXT        NOT NULL DEFAULT 'MEDIUM',
    log_channel       TEXT,
    ai_enabled        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  guild_settings IS 'Per-server configuration for Klaud-Ninja.';
COMMENT ON COLUMN guild_settings.moderation_level IS 'LOW | MEDIUM | HIGH | EXTREME';

-- ── infractions ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS infractions (
    id         BIGSERIAL   PRIMARY KEY,
    guild_id   TEXT        NOT NULL,
    user_id    TEXT        NOT NULL,
    reason     TEXT        NOT NULL,
    action     TEXT        NOT NULL,
    timestamp  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_infractions_guild_user
    ON infractions (guild_id, user_id);

COMMENT ON TABLE infractions IS 'Record of every moderation action taken by Klaud.';
COMMENT ON COLUMN infractions.action IS 'warn | delete | timeout | kick | ban';

-- ── ai_logs ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_logs (
    id               BIGSERIAL   PRIMARY KEY,
    guild_id         TEXT        NOT NULL,
    input            TEXT        NOT NULL,
    ai_response      TEXT        NOT NULL,
    executed_action  TEXT        NOT NULL,
    timestamp        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_logs_guild
    ON ai_logs (guild_id);

COMMENT ON TABLE ai_logs IS 'Full audit trail of every AI action executed by Klaud.';

-- ── Row-Level Security (recommended) ─────────────────────────
-- Enable RLS so only your service role key can read/write.
ALTER TABLE guild_settings  ENABLE ROW LEVEL SECURITY;
ALTER TABLE infractions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_logs         ENABLE ROW LEVEL SECURITY;

-- Grant full access to the service role (your SUPABASE_KEY)
CREATE POLICY "service_role_all" ON guild_settings
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "service_role_all" ON infractions
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "service_role_all" ON ai_logs
    FOR ALL USING (true) WITH CHECK (true);
