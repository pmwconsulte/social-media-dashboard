-- PMW primary organization owner marker
-- Version: 20261126_003
-- Adds a unique primary-owner marker. No memberships or users are created/changed.
-- Apply manually after migrations 001 and 002.

BEGIN;

ALTER TABLE organization_memberships
    ADD COLUMN IF NOT EXISTS is_primary_owner BOOLEAN NOT NULL DEFAULT FALSE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_organization_single_primary_owner
    ON organization_memberships(organization_id)
    WHERE is_primary_owner = TRUE;

COMMIT;
