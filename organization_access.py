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


def ensure_system_organization_roles(conn, organization_id):
    """Create protected preset roles and grant their baseline permissions.

    This function is explicit and caller-invoked; it is never run at import or
    application startup. The caller owns commit/rollback and connection scope.
    """
    role_ids = {}
    with conn.cursor() as cur:
        for role_key in sorted(SYSTEM_ORGANIZATION_ROLES):
            cur.execute(
                """INSERT INTO organization_roles
                       (organization_id, role_key, display_name, is_system)
                   VALUES (%s, %s, %s, TRUE)
                   ON CONFLICT (organization_id, role_key) DO NOTHING
                   RETURNING id, is_system""",
                (str(organization_id), role_key, role_key.replace("_", " ").title()),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """SELECT id, is_system
                       FROM organization_roles
                       WHERE organization_id = %s AND role_key = %s
                       LIMIT 1""",
                    (str(organization_id), role_key),
                )
                row = cur.fetchone()
            if not row or row[1] is not True:
                raise RuntimeError(
                    "Protected organization role is missing or marked as custom."
                )
            role_id = row[0]
            role_ids[role_key] = role_id
            for permission in sorted(ORGANIZATION_ROLE_PERMISSIONS[role_key]):
                cur.execute(
                    """INSERT INTO organization_role_permissions
                           (role_id, permission_key)
                       VALUES (%s, %s)
                       ON CONFLICT (role_id, permission_key) DO NOTHING""",
                    (role_id, permission),
                )
    return role_ids


def user_has_organization_permission(conn, user_id, organization_id, permission):
    """Check an active organization membership using a caller-owned connection.

    The caller owns transaction and connection lifecycle. Unknown roles and
    missing memberships fail closed. Custom role permissions are read from DB.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.role_key,
                      COALESCE(
                          ARRAY_AGG(p.permission_key)
                              FILTER (WHERE p.permission_key IS NOT NULL),
                          ARRAY[]::VARCHAR[]
                      )
               FROM organization_memberships m
               JOIN organization_roles r
                 ON r.id = m.role_id
                AND r.organization_id = m.organization_id
               LEFT JOIN organization_role_permissions p
                 ON p.role_id = r.id
               WHERE m.user_id = %s
                 AND m.organization_id = %s
                 AND m.is_active = TRUE
               GROUP BY r.role_key
               LIMIT 1""",
            (str(user_id), str(organization_id)),
        )
        row = cur.fetchone()

    if not row:
        return False
    role_key, permission_rows = row
    custom_permissions = frozenset(permission_rows or ())
    return has_organization_permission(
        role_key, permission, custom_permissions=custom_permissions
    )


def user_has_workspace_permission(conn, user_id, workspace_id, permission):
    """Require active org + workspace memberships before checking legacy RBAC.

    This is an opt-in authorization primitive. Existing routes are not switched
    to it automatically; route-by-route integration requires a separate review.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT w.organization_id,
                      om.is_active,
                      wm.is_active,
                      wm.role_key,
                      r.role_key,
                      COALESCE(
                          ARRAY_AGG(p.permission_key)
                              FILTER (WHERE p.permission_key IS NOT NULL),
                          ARRAY[]::VARCHAR[]
                      )
               FROM workspaces w
               JOIN organization_memberships om
                 ON om.organization_id = w.organization_id
                AND om.user_id = %s
               JOIN organization_roles r
                 ON r.id = om.role_id
                AND r.organization_id = om.organization_id
               LEFT JOIN organization_role_permissions p
                 ON p.role_id = r.id
               JOIN workspace_memberships wm
                 ON wm.workspace_id = w.id
                AND wm.organization_id = w.organization_id
                AND wm.user_id = om.user_id
               WHERE w.id = %s
               GROUP BY w.organization_id, om.is_active, wm.is_active,
                        wm.role_key, r.role_key
               LIMIT 1""",
            (str(user_id), str(workspace_id)),
        )
        row = cur.fetchone()

    if not row:
        return False
    (
        _organization_id,
        organization_active,
        workspace_active,
        workspace_role,
        organization_role,
        organization_permissions,
    ) = row
    if not has_organization_permission(
        organization_role,
        "workspace.list",
        custom_permissions=frozenset(organization_permissions or ()),
    ):
        return False
    return can_access_workspace_membership(
        organization_membership_active=organization_active,
        workspace_membership_active=workspace_active,
        workspace_role=workspace_role,
        permission=permission,
    )
