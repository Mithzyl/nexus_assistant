-- Option switching needs an immutable preview baseline; direct todo edits
-- need the version they were based on. Additive and nullable.
ALTER TABLE matter_drafts ADD COLUMN option_base_json JSON NULL;
ALTER TABLE matter_drafts ADD COLUMN base_todo_id VARCHAR(36) NULL;
ALTER TABLE matter_drafts ADD COLUMN base_todo_updated_at DATETIME NULL;
