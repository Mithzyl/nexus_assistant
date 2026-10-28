-- AMap places use GCJ-02 and retain their normalized source identifiers.
ALTER TABLE schedules ADD COLUMN coordinate_system VARCHAR(16) NULL;
ALTER TABLE schedules ADD COLUMN location_city VARCHAR(64) NULL;
ALTER TABLE schedules ADD COLUMN location_address VARCHAR(255) NULL;
ALTER TABLE schedules ADD COLUMN location_source VARCHAR(32) NULL;
ALTER TABLE schedules ADD COLUMN location_external_id VARCHAR(128) NULL;
