-- PMW organization memberships and organization-scoped RBAC
-- Version: 20261126_002
-- Structural only: no users/workspaces are assigned and no existing roles are changed.
-- Apply manually only after migration 20261126_001 has been reviewed and applied.

BEGIN;

-- Composite key allows PostgreSQL to enforce that a workspace membership and
-- its organization membership refer to the same organization.
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspaces_id_organization
    ON workspaces(id, organization_id);

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
    CONSTRAINT uq_organization_membership_org_user
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

-- Both foreign keys enforce that workspace membership belongs to the same
-- organization membership. Legacy workspace roles remain unchanged.
CREATE TABLE IF NOT EXISTS workspace_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    workspace_id UUID NOT NULL,
    user_id UUID NOT NULL,
    role_key VARCHAR(40) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_workspace_membership_user
        UNIQUE (workspace_id, user_id),
    CONSTRAINT ck_workspace_membership_role
        CHECK (role_key IN ('owner', 'admin', 'editor', 'analyst', 'viewer')),
    CONSTRAINT fk_workspace_membership_workspace_org
        FOREIGN KEY (workspace_id, organization_id)
        REFERENCES workspaces(id, organization_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_workspace_membership_org_user
        FOREIGN KEY (organization_id, user_id)
        REFERENCES organization_memberships(organization_id, user_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_memberships_user_active
    ON workspace_memberships(user_id, is_active);

CREATE INDEX IF NOT EXISTS idx_workspace_memberships_workspace_active
    ON workspace_memberships(workspace_id, is_active);

COMMIT;
