-- Apply before deploying digest-aware workers or reporting. No backfill required.
BEGIN;
CREATE TABLE email_log_listings (
    email_log_id INTEGER NOT NULL REFERENCES email_logs(id) ON DELETE CASCADE,
    listing_id VARCHAR NOT NULL,
    PRIMARY KEY (email_log_id, listing_id)
);
CREATE INDEX ix_email_log_listings_listing_id ON email_log_listings (listing_id);
COMMIT;
