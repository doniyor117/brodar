CREATE TABLE brodar_v2.bot_aliases (
 audience text NOT NULL, bot_id bigint NOT NULL, peer_id bigint NOT NULL,
 PRIMARY KEY(audience,bot_id)
);
ALTER TABLE brodar_v2.bot_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.bot_aliases FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.bot_aliases
 USING(audience=current_setting('brodar.audience',true))
 WITH CHECK(audience=current_setting('brodar.audience',true));
