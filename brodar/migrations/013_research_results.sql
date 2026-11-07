-- Approval pauses and the final report are separate, idempotent notifications.
ALTER TABLE brodar_v2.research_tasks
 ADD COLUMN result_version integer NOT NULL DEFAULT 0,
 ADD COLUMN notified_result_version integer NOT NULL DEFAULT -1;
UPDATE brodar_v2.research_tasks SET notified_result_version=0
 WHERE notified_generation>=generation;
DROP INDEX brodar_v2.research_due;
CREATE INDEX research_due ON brodar_v2.research_tasks(audience,next_poll)
 WHERE transport='hyperagent_mcp' AND status IN ('queued','submitting','running','waiting');
