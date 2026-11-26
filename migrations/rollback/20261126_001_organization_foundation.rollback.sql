-- Rollback for 20261126_001_organization_foundation.sql
-- Only run after confirming no application code or data depends on organization_id.

BEGIN;

DROP INDEX IF EXISTS uq_organizations_payment_subscription;
DROP INDEX IF EXISTS idx_workspaces_organization_id;

ALTER TABLE workspaces
    DROP CONSTRAINT IF EXISTS workspaces_organization_id_fkey;

ALTER TABLE workspaces
    DROP COLUMN IF EXISTS organization_id;

DROP TABLE IF EXISTS organizations;

COMMIT;
