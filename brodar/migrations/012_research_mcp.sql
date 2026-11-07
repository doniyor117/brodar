-- Preserve old Telegram task history; never silently re-submit it by MCP.
ALTER TABLE brodar_v2.poll_state ADD COLUMN internal_update_id bigint NOT NULL DEFAULT 0;
ALTER TABLE brodar_v2.research_tasks
 ADD COLUMN transport text NOT NULL DEFAULT 'telegram',
 ADD COLUMN remote_agent_id text,
 ADD COLUMN remote_thread_id text,
 ADD COLUMN interactions integer NOT NULL DEFAULT 0 CHECK(interactions BETWEEN 0 AND 10),
 ADD COLUMN generation integer NOT NULL DEFAULT 0,
 ADD COLUMN notified_generation integer NOT NULL DEFAULT -1,
 ADD COLUMN outbound_message text,
 ADD COLUMN operation_id text,
 ADD COLUMN operation_log jsonb NOT NULL DEFAULT '{}',
 ADD COLUMN result jsonb,
 ADD COLUMN last_result_id text,
 ADD COLUMN next_poll timestamptz NOT NULL DEFAULT now(),
 ADD COLUMN attempts integer NOT NULL DEFAULT 0,
 ADD COLUMN consumer_owner uuid,
 ADD COLUMN lease_until timestamptz,
 ADD COLUMN error_code text;
UPDATE brodar_v2.research_tasks SET status='cancelled',error_code='telegram_transport_replaced'
 WHERE transport='telegram' AND status='pending';
CREATE INDEX research_due ON brodar_v2.research_tasks(audience,next_poll)
 WHERE transport='hyperagent_mcp' AND status IN ('queued','submitting','running');
