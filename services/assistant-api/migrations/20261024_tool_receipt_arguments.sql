-- SQLite additive migration; idempotency is enforced by the migrator's
-- PRAGMA table_info check, not by swallowing SQL errors.
ALTER TABLE tool_call_receipts ADD COLUMN arguments_sha256 VARCHAR(64);
