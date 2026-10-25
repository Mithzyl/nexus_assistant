-- SQLite additive migration. The migrator checks PRAGMA table_info before
-- each ALTER, so a manually pre-added column is recorded, not applied twice.
ALTER TABLE commute_checks ADD COLUMN route_snapshot_json JSON NULL;
ALTER TABLE commute_checks ADD COLUMN route_snapshot_hash VARCHAR(64) NULL;
ALTER TABLE commute_advisories ADD COLUMN ai_analysis_json JSON NULL;
ALTER TABLE commute_checks ADD COLUMN ai_run_id VARCHAR(36) NULL;
