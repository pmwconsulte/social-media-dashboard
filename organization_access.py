"""Organization-scoped access policy for PMW Dashboard.

This module is deliberately side-effect free: it performs no database writes,
does not alter legacy users.role values, and never grants workspace access
solely because a user belongs to an organization.
"""
import re


SYSTEM_ORGANIZATION_ROLES = frozenset({
    "owner",
    "admin",
    "billing_manager",
    "manager",
    "member",
})

ORGANIZATION_ROLE_PERMISSIONS = {
    "owner": frozenset({
        "organization.read", "organization.manage",
        "organization.members.read", "organization.members.manage",
        "organization.roles.read", "organization.roles.manage",
        "organization.billing.read", "organization.billing.manage",
        "workspace.list", "workspace.create", "workspace.manage",
        "security.audit",
    }),
    "admin": frozenset({
        "organization.read",
        "organization.members.read", "organization.members.manage",
        "organization.roles.read", "organization.roles.manage",
        "workspace.list", "workspace.create", "workspace.manage",
        "security.audit",
    }),
    "billing_manager": frozenset({
        "organization.read",
        "organization.billing.read", "organization.billing.manage",
        "workspace.list",
    }),
    "manager": frozenset({
        "organization.read", "organization.members.read",
        "organization.roles.read", "workspace.list",
        "workspace.create", "workspace.manage",
    }),
    "member": frozenset({
        "organization.read", "workspace.list",
    }),
}

RESERVED_ORGANIZATION_ROLE_KEYS = SYSTEM_ORGANIZATION_ROLES
_CUSTOM_ROLE_KEY = re.compile(r"^[a-z][a-z0-9_:-]{1,79}$")


def normalize_organization_role(role):
    """Normalize a protected organization role; unknown values fail closed."""
    value = str(role or "").strip().lower()
    return value if value in SYSTEM_ORGANIZATION_ROLES else None


def permissions_for_system_role(role):
    """Return immutable permissions for a protected role, or an empty set."""
    normalized = normalize_organization_role(role)
    if normalized is None:
        return frozenset()
    return ORGANIZATION_ROLE_PERMISSIONS[normalized]


def has_organization_permission(role, permission, custom_permissions=None):
    """Check a permission for a system role or an explicitly loaded custom role."""
    normalized = normalize_organization_role(role)
    if normalized is not None:
        return permission in ORGANIZATION_ROLE_PERMISSIONS[normalized]
    if not isinstance(custom_permissions, (set, frozenset, tuple, list)):
        return False
    return permission in custom_permissions


def validate_custom_role_key(role_key):
    """Validate a custom role key while reserving all protected role names."""
    if not isinstance(role_key, str):
        return False
    value = role_key.strip().lower()
    return bool(
        _CUSTOM_ROLE_KEY.fullmatch(value)
        and value not in RESERVED_ORGANIZATION_ROLE_KEYS
    )


def workspace_role_allows(role, permission):
    """Delegate workspace permissions to existing RBAC without changing it."""
    from security_rbac import has_permission
    return has_permission(role, permission)


def can_access_workspace_membership(
    *,
    organization_membership_active,
    workspace_membership_active,
    workspace_role,
    permission,
):
    """Require both active memberships; org membership alone never grants access."""
    if organization_membership_active is not True:
        return False
    if workspace_membership_active is not True:
        return False
    return workspace_role_allows(workspace_role, permission)
