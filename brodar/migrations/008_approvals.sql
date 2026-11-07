CREATE TABLE brodar_v2.approvals (
 id uuid PRIMARY KEY, audience text NOT NULL, topic_id bigint NOT NULL,
 turn_id uuid NOT NULL, action_hash text NOT NULL, action jsonb NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','denied','used')),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT now()+interval '15 minutes',
 decided_by bigint, decided_at timestamptz
);
ALTER TABLE brodar_v2.approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.approvals
 USING(audience=current_setting('brodar.audience',true))
 WITH CHECK(audience=current_setting('brodar.audience',true));
CREATE INDEX approvals_audience ON brodar_v2.approvals(audience,created_at);
