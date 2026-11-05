CREATE TABLE brodar_v2.artifacts (
    id uuid PRIMARY KEY, audience text NOT NULL, topic_id bigint NOT NULL DEFAULT 0,
    turn_id uuid NOT NULL, operation_id text NOT NULL, request_hash text NOT NULL,
    filename text NOT NULL, mime_type text NOT NULL, object_key text NOT NULL,
    size_bytes bigint NOT NULL, content_hash text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(audience,turn_id,operation_id)
);
CREATE INDEX artifacts_recent ON brodar_v2.artifacts(audience,created_at);
ALTER TABLE brodar_v2.artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.artifacts FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.artifacts
    USING(audience=current_setting('brodar.audience',true))
    WITH CHECK(audience=current_setting('brodar.audience',true));
