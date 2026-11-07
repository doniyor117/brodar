CREATE TABLE brodar_v2.bot_exchanges (
 audience text NOT NULL, peer_id bigint NOT NULL, exchange_id uuid NOT NULL,
 origin_chat_id bigint NOT NULL, used integer NOT NULL DEFAULT 0 CHECK(used BETWEEN 0 AND 10),
 closed boolean NOT NULL DEFAULT false, last_digest text, last_incoming_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(audience,peer_id)
);
CREATE TABLE brodar_v2.bot_reservations (
 audience text NOT NULL, operation_key text NOT NULL, peer_id bigint NOT NULL,
 exchange_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(audience,operation_key)
);
CREATE TABLE brodar_v2.research_tasks (
 audience text NOT NULL,id uuid PRIMARY KEY,origin_chat_id bigint NOT NULL,origin_topic_id bigint NOT NULL,
 requester_id bigint,request_id text,brief text NOT NULL,status text NOT NULL DEFAULT 'pending',
 created_at timestamptz NOT NULL DEFAULT now(),deadline timestamptz NOT NULL DEFAULT now()+interval '20 minutes'
);
ALTER TABLE brodar_v2.bot_exchanges ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.bot_exchanges FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.bot_exchanges USING(audience=current_setting('brodar.audience',true)) WITH CHECK(audience=current_setting('brodar.audience',true));
ALTER TABLE brodar_v2.bot_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.bot_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.bot_reservations USING(audience=current_setting('brodar.audience',true)) WITH CHECK(audience=current_setting('brodar.audience',true));
ALTER TABLE brodar_v2.research_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE brodar_v2.research_tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY audience_policy ON brodar_v2.research_tasks USING(audience=current_setting('brodar.audience',true)) WITH CHECK(audience=current_setting('brodar.audience',true));
