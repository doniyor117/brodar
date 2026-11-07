CREATE TABLE brodar_v2.group_access (
 audience text NOT NULL, chat_id bigint NOT NULL CHECK(chat_id<0),
 approved_by bigint NOT NULL, approved_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(audience,chat_id)
);
ALTER TABLE brodar_v2.group_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.group_access FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.group_access
 USING(audience=current_setting('brodar.audience',true))
 WITH CHECK(audience=current_setting('brodar.audience',true));
