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
            if not has_permission(session.get("role"), permission):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate
