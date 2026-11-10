-- Review and apply once in the intended database/schema before DB comparison/import.
-- This stores source identities and alert counts, not alerts or search criteria.
BEGIN;
CREATE TABLE realscout_contact_imports (
    contact_id VARCHAR(255) PRIMARY KEY,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    source_deleted BOOLEAN NOT NULL,
    listing_alert_count INTEGER NOT NULL DEFAULT 0,
    market_alert_count INTEGER NOT NULL DEFAULT 0,
    home_value_alert_count INTEGER NOT NULL DEFAULT 0,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_realscout_contact_imports_lead_id ON realscout_contact_imports (lead_id);
COMMIT;
