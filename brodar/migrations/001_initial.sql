-- Additive: never alter or prune the legacy public schema.
CREATE SCHEMA IF NOT EXISTS brodar_v2;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS brodar_v2.schema_migrations (
    version integer PRIMARY KEY, checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE brodar_v2.messages (
    id uuid PRIMARY KEY, audience text NOT NULL, topic_id bigint NOT NULL DEFAULT 0,
    platform_message_id bigint, sender_id bigint, sender_name text,
    role text NOT NULL, content text NOT NULL,
    sent_at timestamptz, legacy_timestamp text, received_at timestamptz NOT NULL DEFAULT now(),
    reply_to_id bigint, edited_at timestamptz,
    source text NOT NULL DEFAULT 'telegram', source_id text NOT NULL,
    source_hash text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}',
    UNIQUE(audience, source, source_id)
);
CREATE INDEX messages_history ON brodar_v2.messages(audience, topic_id, received_at, id);
CREATE INDEX messages_platform ON brodar_v2.messages(audience, platform_message_id);
CREATE INDEX messages_words ON brodar_v2.messages USING gin(to_tsvector('simple', content));
CREATE TABLE brodar_v2.inbox (
    id uuid PRIMARY KEY, audience text NOT NULL, topic_id bigint NOT NULL DEFAULT 0,
    bot_id bigint NOT NULL, update_id bigint NOT NULL, message_id uuid REFERENCES brodar_v2.messages(id),
    payload jsonb NOT NULL, status text NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','running','done','failed')),
    attempts integer NOT NULL DEFAULT 0, lease_until timestamptz, error_code text,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(bot_id,update_id)
);
CREATE INDEX inbox_pending ON brodar_v2.inbox(status,lease_until,created_at);
CREATE TABLE brodar_v2.memory_documents (
    id uuid PRIMARY KEY, audience text NOT NULL, path text NOT NULL,
    content text NOT NULL, revision integer NOT NULL DEFAULT 1,
    subject_ids bigint[] NOT NULL DEFAULT '{}', confidence text NOT NULL DEFAULT 'observed',
    deleted boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(audience,path)
);
CREATE TABLE brodar_v2.memory_revisions (
    id uuid PRIMARY KEY, audience text NOT NULL, document_id uuid NOT NULL
        REFERENCES brodar_v2.memory_documents(id), revision integer NOT NULL,
    content text NOT NULL, deleted boolean NOT NULL, reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(document_id,revision)
);
CREATE TABLE brodar_v2.memory_sources (
    audience text NOT NULL, document_id uuid NOT NULL REFERENCES brodar_v2.memory_documents(id),
    message_id uuid NOT NULL REFERENCES brodar_v2.messages(id) ON DELETE CASCADE,
    PRIMARY KEY(document_id,message_id)
);
CREATE TABLE brodar_v2.embeddings (
    audience text NOT NULL, kind text NOT NULL CHECK(kind IN ('message','memory')),
    source_id uuid NOT NULL, model text NOT NULL, dimensions integer NOT NULL CHECK(dimensions=768),
    content_hash text NOT NULL, embedding vector(768) NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(audience,kind,source_id,model)
);
CREATE INDEX embeddings_scope ON brodar_v2.embeddings(audience,kind,model);
CREATE TABLE brodar_v2.sessions (
    audience text NOT NULL, topic_id bigint NOT NULL DEFAULT 0,
    provider text NOT NULL, prompt_revision text NOT NULL, session_id text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(audience,topic_id,provider,prompt_revision)
);
CREATE TABLE brodar_v2.deliveries (
    id uuid PRIMARY KEY, audience text NOT NULL, topic_id bigint NOT NULL DEFAULT 0,
    turn_id uuid NOT NULL, tool_call_id text NOT NULL, method text NOT NULL,
    request jsonb NOT NULL, status text NOT NULL CHECK(status IN ('pending','sending','sent','failed','uncertain')),
    response jsonb, created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(turn_id,tool_call_id)
);
CREATE TABLE brodar_v2.jobs (
    id uuid PRIMARY KEY, audience text NOT NULL, topic_id bigint NOT NULL DEFAULT 0,
    kind text NOT NULL, payload jsonb NOT NULL, checkpoint jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'pending', due_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz, attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    dedup_key text NOT NULL, UNIQUE(audience,kind,dedup_key)
);
CREATE TABLE brodar_v2.poll_state (
    bot_id bigint PRIMARY KEY, next_update_id bigint NOT NULL
);
-- The model never receives this role, a connection string, or a SQL tool.
-- RLS is defense in depth underneath the scope-bound application methods.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['messages','inbox','memory_documents','memory_revisions',
      'memory_sources','embeddings','sessions','deliveries','jobs'] LOOP
    EXECUTE format('ALTER TABLE brodar_v2.%I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('ALTER TABLE brodar_v2.%I FORCE ROW LEVEL SECURITY',t);
    EXECUTE format('CREATE POLICY audience_policy ON brodar_v2.%I USING
      (audience = current_setting(''brodar.audience'',true)) WITH CHECK
      (audience = current_setting(''brodar.audience'',true))',t);
  END LOOP;
END $$;
