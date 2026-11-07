-- NULL means historical metadata was not recorded. Do not fabricate a backfill.
-- Restoring such a revision preserves the current document metadata.
ALTER TABLE brodar_v2.memory_revisions ADD COLUMN metadata jsonb;
