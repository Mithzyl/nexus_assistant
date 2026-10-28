-- Reminder lifecycle and matter-record deletion markers are additive and nullable.
ALTER TABLE matter_todos ADD COLUMN deleted_at DATETIME NULL;
ALTER TABLE reminders ADD COLUMN schedule_id VARCHAR(36) NULL;
ALTER TABLE reminders ADD COLUMN acknowledged_at DATETIME NULL;
ALTER TABLE reminders ADD COLUMN deleted_at DATETIME NULL;
