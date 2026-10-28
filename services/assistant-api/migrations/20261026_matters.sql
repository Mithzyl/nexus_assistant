-- Matters, todos, progress, drafts, changes and reminders (additive only).
-- Existing `schedules` rows keep working: every new column is nullable and the
-- new unique index treats NULL external ids as distinct.

ALTER TABLE schedules ADD COLUMN source VARCHAR(24) NULL;
ALTER TABLE schedules ADD COLUMN sync_status VARCHAR(24) NULL;
ALTER TABLE schedules ADD COLUMN source_external_id VARCHAR(255) NULL;
ALTER TABLE schedules ADD COLUMN source_updated_at DATETIME NULL;
ALTER TABLE schedules ADD COLUMN source_version INTEGER NULL;
ALTER TABLE schedules ADD COLUMN matter_id VARCHAR(36) NULL;
ALTER TABLE schedules ADD COLUMN data_source_id VARCHAR(36) NULL;
ALTER TABLE schedules ADD COLUMN latitude FLOAT NULL;
ALTER TABLE schedules ADD COLUMN longitude FLOAT NULL;
ALTER TABLE schedules ADD COLUMN deleted_at DATETIME NULL;

CREATE INDEX IF NOT EXISTS ix_schedules_matter ON schedules (matter_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedules_user_source_external
    ON schedules (user_id, source, source_external_id);
