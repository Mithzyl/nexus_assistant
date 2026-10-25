-- Shared AI task metadata and the chat association (additive only).
-- Legacy rows keep NULL purpose until scripts/backfill_user_asks.py classifies
-- them from existing evidence (check link or internal call marker), never from
-- prompt text.
ALTER TABLE nexus_runs ADD COLUMN purpose VARCHAR(32) NULL;
ALTER TABLE nexus_runs ADD COLUMN business_ref VARCHAR(36) NULL;
ALTER TABLE nexus_runs ADD COLUMN input_version INTEGER NULL;
ALTER TABLE nexus_runs ADD COLUMN trigger VARCHAR(16) NULL;

CREATE INDEX IF NOT EXISTS ix_nexus_runs_purpose_business
    ON nexus_runs (purpose, business_ref);
CREATE INDEX IF NOT EXISTS ix_nexus_runs_user_purpose_created
    ON nexus_runs (user_id, purpose, created_at);

-- One row per question asked in chat. Execution state and the answer stay in
-- nexus_runs; this table never duplicates them.
CREATE TABLE IF NOT EXISTS user_asks (
    ask_id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    local_run_id VARCHAR(36) NOT NULL UNIQUE
        REFERENCES nexus_runs(local_run_id) ON DELETE CASCADE,
    request_text VARCHAR(4000) NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_user_asks_user_id ON user_asks (user_id);
CREATE INDEX IF NOT EXISTS ix_user_asks_user_created ON user_asks (user_id, created_at);
