-- Direct-edit drafts reuse matter_drafts and need their own idempotency key.
ALTER TABLE matter_drafts ADD COLUMN origin_key VARCHAR(128) NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_matter_drafts_user_origin
    ON matter_drafts (user_id, origin_key);
