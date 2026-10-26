-- Source identity, field/scope authorization and batch content hash (additive).
ALTER TABLE schedules ADD COLUMN source_scope_key VARCHAR(255) NULL;
ALTER TABLE reminders ADD COLUMN source_scope_key VARCHAR(255) NULL;
ALTER TABLE reminders ADD COLUMN data_source_id VARCHAR(36) NULL;
ALTER TABLE content_sync_batches ADD COLUMN payload_sha256 VARCHAR(64) NULL;
ALTER TABLE nexus_runs ADD COLUMN context_json JSON NULL;

DROP INDEX IF EXISTS uq_schedules_user_source_external;
DROP INDEX IF EXISTS uq_reminders_user_source_external;
CREATE UNIQUE INDEX IF NOT EXISTS uq_schedules_user_source_device_external
    ON schedules (user_id, source, data_source_id, source_external_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reminders_user_source_device_external
    ON reminders (user_id, source, data_source_id, source_external_id);
