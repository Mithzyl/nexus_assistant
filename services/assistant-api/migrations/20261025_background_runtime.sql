-- SQLite background runtime metadata. Additive only; legacy rows get NULL phase.
ALTER TABLE commute_checks ADD COLUMN analysis_mode VARCHAR(16) NULL;
ALTER TABLE commute_checks ADD COLUMN phase VARCHAR(32) NULL;
ALTER TABLE commute_checks ADD COLUMN phase_updated_at DATETIME NULL;
CREATE TABLE IF NOT EXISTS service_heartbeats (
    component VARCHAR(32) PRIMARY KEY,
    instance_id VARCHAR(64) NULL,
    last_seen_at DATETIME NOT NULL,
    current_check_id VARCHAR(36) NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
