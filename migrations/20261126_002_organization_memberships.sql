-- PMW organization memberships and organization-scoped RBAC
-- Version: 20261126_002
-- Structural only: no users/workspaces are assigned and no existing roles are changed.
-- Apply manually only after migration 20261126_001 has been reviewed and applied.

BEGIN;

CREATE TABLE IF NOT EXISTS organization_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    role_key VARCHAR(80) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    is_system BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_organization_roles_org_key
        UNIQUE (organization_id, role_key),
    CONSTRAINT uq_organization_roles_id_org
        UNIQUE (id, organization_id),
    CONSTRAINT ck_organization_roles_key
        CHECK (role_key ~ '^[a-z][a-z0-9_:-]{1,79}$')
);

CREATE TABLE IF NOT EXISTS organization_role_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id UUID NOT NULL
        REFERENCES organization_roles(id) ON DELETE CASCADE,
    permission_key VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_organization_role_permission
        UNIQUE (role_id, permission_key)
);

CREATE TABLE IF NOT EXISTS organization_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    role_id UUID NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_organization_membership_user
        UNIQUE (organization_id, user_id),
    CONSTRAINT fk_organization_membership_role_scope
        FOREIGN KEY (role_id, organization_id)
        REFERENCES organization_roles(id, organization_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_organization_memberships_user_active
    ON organization_memberships(user_id, is_active);

CREATE INDEX IF NOT EXISTS idx_organization_memberships_org_active
    ON organization_memberships(organization_id, is_active);

-- A user can belong to multiple workspaces in the same organization.
-- This is separate from organization membership and does not grant access by itself.
CREATE TABLE IF NOT EXISTS workspace_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL
        REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    role_key VARCHAR(40) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_workspace_membership_user
        UNIQUE (workspace_id, user_id),
    CONSTRAINT ck_workspace_membership_role
        CHECK (role_key IN ('owner', 'admin', 'editor', 'analyst', 'viewer', 'platform_admin'))
);

CREATE INDEX IF NOT EXISTS idx_workspace_memberships_user_active
    ON workspace_memberships(user_id, is_active);

CREATE INDEX IF NOT EXISTS idx_workspace_memberships_workspace_active
    ON workspace_memberships(workspace_id, is_active);

COMMIT;
