ALTER TABLE brodar_v2.embeddings ADD COLUMN chunk_index integer NOT NULL DEFAULT 0;
ALTER TABLE brodar_v2.embeddings DROP CONSTRAINT embeddings_pkey;
ALTER TABLE brodar_v2.embeddings ADD PRIMARY KEY(audience,kind,source_id,model,chunk_index);
CREATE TABLE brodar_v2.reflection_state (
    audience text PRIMARY KEY, last_received_at timestamptz NOT NULL DEFAULT '-infinity',
    last_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    last_run_at timestamptz NOT NULL DEFAULT '-infinity'
);
ALTER TABLE brodar_v2.reflection_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.reflection_state FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.reflection_state
    USING(audience=current_setting('brodar.audience',true))
    WITH CHECK(audience=current_setting('brodar.audience',true));
CREATE TABLE brodar_v2.forgotten_sources (
    audience text NOT NULL, source text NOT NULL, source_id text NOT NULL,
    forgotten_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(audience,source,source_id)
);
ALTER TABLE brodar_v2.forgotten_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.forgotten_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.forgotten_sources
    USING(audience=current_setting('brodar.audience',true))
    WITH CHECK(audience=current_setting('brodar.audience',true));
