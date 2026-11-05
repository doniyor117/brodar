CREATE TABLE brodar_v2.tool_calls (
    id uuid PRIMARY KEY, audience text NOT NULL, turn_id uuid, name text NOT NULL,
    arguments jsonb NOT NULL, result jsonb, error text, duration_ms integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE brodar_v2.tool_calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.tool_calls FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.tool_calls
    USING(audience=current_setting('brodar.audience',true))
    WITH CHECK(audience=current_setting('brodar.audience',true));
ALTER TABLE brodar_v2.messages ADD COLUMN changed_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE brodar_v2.memory_documents ADD COLUMN needs_review boolean NOT NULL DEFAULT false;
CREATE INDEX messages_changes ON brodar_v2.messages(audience,changed_at,id);
