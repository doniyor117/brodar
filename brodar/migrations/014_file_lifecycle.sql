-- Existing objects remain retained; never infer consent to delete old data.
ALTER TABLE brodar_v2.artifacts ALTER COLUMN object_key DROP NOT NULL;
ALTER TABLE brodar_v2.artifacts ADD COLUMN storage_kind text NOT NULL DEFAULT 'retained';
ALTER TABLE brodar_v2.artifacts ADD COLUMN local_key text;
ALTER TABLE brodar_v2.artifacts ADD COLUMN state text NOT NULL DEFAULT 'active';
ALTER TABLE brodar_v2.artifacts ADD COLUMN last_used_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE brodar_v2.artifacts ADD COLUMN expires_at timestamptz;
ALTER TABLE brodar_v2.artifacts ADD COLUMN retained_at timestamptz;
ALTER TABLE brodar_v2.artifacts ADD COLUMN retention_request jsonb;
ALTER TABLE brodar_v2.artifacts ADD CONSTRAINT artifact_lifecycle CHECK (
 storage_kind IN ('temporary','retained') AND state IN ('pending','active','promoting','expired') AND
 ((storage_kind='retained' AND object_key IS NOT NULL) OR
  (storage_kind='temporary' AND local_key IS NOT NULL)));
CREATE INDEX artifact_expiry ON brodar_v2.artifacts(expires_at) WHERE storage_kind='temporary' AND state <> 'expired';
-- Aggregate counters contain no audience names, file names, or private material.
CREATE TABLE brodar_v2.storage_usage (kind text PRIMARY KEY CHECK(kind IN ('temporary','retained')), bytes bigint NOT NULL CHECK(bytes>=0));
INSERT INTO brodar_v2.storage_usage VALUES ('temporary',0),('retained',0);
DO $$ DECLARE a record; n bigint; BEGIN
 FOR a IN SELECT audience FROM brodar_v2.audience_registry LOOP
  PERFORM set_config('brodar.audience',a.audience,true);
  SELECT coalesce(sum(size_bytes),0) INTO n FROM brodar_v2.artifacts WHERE audience=a.audience;
  UPDATE brodar_v2.storage_usage SET bytes=bytes+n WHERE kind='retained';
 END LOOP;
END $$;
