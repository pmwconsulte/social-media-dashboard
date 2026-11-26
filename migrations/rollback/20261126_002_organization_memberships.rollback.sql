-- Rollback for 20261126_002_organization_memberships.sql
-- Refuses rollback if membership/role data has been created.

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM workspace_memberships)
       OR EXISTS (SELECT 1 FROM organization_memberships)
       OR EXISTS (SELECT 1 FROM organization_role_permissions)
       OR EXISTS (SELECT 1 FROM organization_roles) THEN
        RAISE EXCEPTION
            'Rollback blocked: membership or role data exists. Export/reconcile data and use a dedicated rollback plan.';
    END IF;
END
$$;

DROP INDEX IF EXISTS idx_workspace_memberships_workspace_active;
DROP INDEX IF EXISTS idx_workspace_memberships_user_active;
DROP INDEX IF EXISTS idx_organization_memberships_org_active;
DROP INDEX IF EXISTS idx_organization_memberships_user_active;

DROP TABLE IF EXISTS workspace_memberships;
DROP TABLE IF EXISTS organization_memberships;
DROP TABLE IF EXISTS organization_role_permissions;
DROP TABLE IF EXISTS organization_roles;

DROP INDEX IF EXISTS uq_workspaces_id_organization;

COMMIT;
