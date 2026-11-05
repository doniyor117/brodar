CREATE TABLE brodar_v2.audience_registry (
    audience text PRIMARY KEY, instance_id text NOT NULL, chat_id bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id)
SELECT DISTINCT audience,split_part(audience,':',2),split_part(audience,':',3)::bigint
FROM brodar_v2.messages WHERE audience LIKE 'telegram:%' ON CONFLICT DO NOTHING;
ALTER TABLE brodar_v2.poll_state ADD COLUMN lease_owner uuid;
ALTER TABLE brodar_v2.poll_state ADD COLUMN lease_until timestamptz;
ALTER TABLE brodar_v2.inbox ADD COLUMN lease_owner uuid;
CREATE SCHEMA IF NOT EXISTS brodar_ops;
REVOKE ALL ON SCHEMA brodar_ops FROM PUBLIC;
CREATE TABLE brodar_ops.backup_keys (
    id uuid PRIMARY KEY, name text UNIQUE NOT NULL, encryption_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
