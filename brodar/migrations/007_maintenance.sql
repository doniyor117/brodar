ALTER TABLE brodar_v2.reflection_state ADD COLUMN chunk_offset integer NOT NULL DEFAULT 0;
ALTER TABLE brodar_v2.reflection_state ADD COLUMN consolidation_at timestamptz;
CREATE TABLE brodar_v2.maintenance_runs (
 id uuid PRIMARY KEY, audience text NOT NULL, owner text NOT NULL,
 kind text NOT NULL CHECK(kind IN ('incremental','consolidation')),
 status text NOT NULL DEFAULT 'running', input_manifest jsonb NOT NULL,
 started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 outcome text, changed_revisions jsonb NOT NULL DEFAULT '[]'::jsonb
);
ALTER TABLE brodar_v2.maintenance_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.maintenance_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.maintenance_runs
 USING(audience=current_setting('brodar.audience',true))
 WITH CHECK(audience=current_setting('brodar.audience',true));
CREATE INDEX maintenance_audience ON brodar_v2.maintenance_runs(audience,started_at);

ALTER TABLE brodar_v2.memory_documents ADD COLUMN last_maintained_at timestamptz;
