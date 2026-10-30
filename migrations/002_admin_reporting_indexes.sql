-- Optional reporting performance indexes; no data changes.
-- Review the intended database/search_path before applying. Not run automatically.
BEGIN;
CREATE INDEX IF NOT EXISTS ix_saved_searches_lead_id ON saved_searches (lead_id);
CREATE INDEX IF NOT EXISTS ix_email_logs_search_id ON email_logs (search_id);
COMMIT;
