-- Rollback for 20261126_001_organization_foundation.sql
-- Refuses to remove the foundation if any organization or workspace assignment exists.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM organizations)
       OR EXISTS (
           SELECT 1
           FROM workspaces
           WHERE organization_id IS NOT NULL
       ) THEN
        RAISE EXCEPTION
            'Rollback blocked: organizations or workspace assignments exist. Export/reconcile data and use a dedicated rollback plan.';
    END IF;
END
$$;

DROP INDEX IF EXISTS uq_organizations_payment_subscription;
DROP INDEX IF EXISTS idx_workspaces_organization_id;

ALTER TABLE workspaces
    DROP CONSTRAINT IF EXISTS workspaces_organization_id_fkey;

ALTER TABLE workspaces
    DROP COLUMN IF EXISTS organization_id;

DROP TABLE IF EXISTS organizations;

COMMIT;
