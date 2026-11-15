"""Central RBAC primitives for PMW Dashboard.

Permissions are additive and workspace-scoped. This module has no Flask or
database side effects at import time.
"""
from functools import wraps

ROLE_PERMISSIONS = {
    "platform_admin": frozenset({
        "workspace.read", "workspace.manage", "users.read", "users.manage",
        "billing.read", "billing.manage", "accounts.read", "accounts.manage",
        "analytics.read", "reports.read", "messaging.read", "messaging.reply",
        "publishing.create", "integrations.manage", "security.audit",
    }),
    "owner": frozenset({
        "workspace.read", "workspace.manage", "users.read", "users.manage",
        "billing.read", "billing.manage", "accounts.read", "accounts.manage",
        "analytics.read", "reports.read", "messaging.read", "messaging.reply",
        "publishing.create", "integrations.manage", "security.audit",
    }),
    "admin": frozenset({
        "workspace.read", "users.read", "users.manage", "billing.read",
        "accounts.read", "accounts.manage", "analytics.read", "reports.read",
        "messaging.read", "messaging.reply", "publishing.create",
        "integrations.manage", "security.audit",
    }),
    "editor": frozenset({
        "workspace.read", "accounts.read", "analytics.read", "reports.read",
        "messaging.read", "messaging.reply", "publishing.create",
    }),
    "analyst": frozenset({
        "workspace.read", "accounts.read", "analytics.read", "reports.read",
    }),
    "viewer": frozenset({
        "workspace.read", "accounts.read", "analytics.read", "reports.read",
        "messaging.read",
    }),
}

VALID_ROLES = frozenset(ROLE_PERMISSIONS)


def normalize_role(role):
    value = str(role or "viewer").strip().lower()
    return value if value in VALID_ROLES else "viewer"


def has_permission(role, permission):
    return permission in ROLE_PERMISSIONS[normalize_role(role)]


def require_permission(permission):
    """Flask view decorator; authentication remains centrally enforced too."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            from flask import abort, session

            if not session.get("authenticated"):
                abort(401)

            # Revalidate database-backed identities on every protected request.
            # This makes role changes, workspace reassignment and deactivation
            # effective without waiting for the browser session to expire.
            user_id = session.get("user_id")
            workspace_id = session.get("workspace_id")
            workspace_slug = session.get("workspace_slug")

            if user_id:
                conn = None
                try:
                    from database import get_connection
                    conn = get_connection()
                    with conn.cursor() as cur:
                        cur.execute(
                            """SELECT u.role, u.workspace_id, COALESCE(u.is_active, TRUE),
                                      w.slug
                               FROM users u
                               JOIN workspaces w ON w.id = u.workspace_id
                               WHERE u.id = %s
                               LIMIT 1""",
                            (user_id,),
                        )
                        identity = cur.fetchone()
                    if not identity:
                        session.clear()
                        abort(401)
                    db_role, db_workspace_id, is_active, db_workspace_slug = identity
                    if (
                        not is_active
                        or str(db_workspace_id) != str(workspace_id)
                        or str(db_workspace_slug) != str(workspace_slug)
                    ):
                        session.clear()
                        abort(401)
                    session["role"] = normalize_role(db_role)
                except Exception:
                    # Fail closed when identity cannot be revalidated.
                    session.clear()
                    abort(503)
                finally:
                    if conn:
                        conn.close()
            elif session.get("role") != "platform_admin" or not workspace_id or not workspace_slug:
                session.clear()
                abort(401)

            if not has_permission(session.get("role"), permission):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate
