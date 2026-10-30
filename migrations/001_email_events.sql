-- Apply once after review, before deploying the new webhook handler.
-- Assumes the existing email_logs table is in the intended search_path.
BEGIN;
CREATE INDEX IF NOT EXISTS ix_email_logs_message_id ON email_logs (message_id);
CREATE TABLE email_events (
    id SERIAL PRIMARY KEY,
    provider_event_id VARCHAR(255) NOT NULL UNIQUE,
    message_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details JSON NOT NULL,
    processing_status VARCHAR(20) NOT NULL,
    email_log_id INTEGER REFERENCES email_logs(id) ON DELETE SET NULL,
    CONSTRAINT ck_email_events_status CHECK (processing_status IN ('pending', 'processed'))
);
CREATE INDEX ix_email_events_message_id ON email_events (message_id);
CREATE INDEX ix_email_events_pending ON email_events (processing_status, message_id);
CREATE INDEX ix_email_events_log_time ON email_events (email_log_id, occurred_at);
COMMIT;
