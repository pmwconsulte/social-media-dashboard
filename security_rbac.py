"""Central RBAC primitives for PMW Dashboard.

Permissions are additive and workspace-scoped. This module has no Flask or
database side effects at import time.
"""
from functools import wraps
import secrets

ROLE_PERMISSIONS = {
    "platform_admin": frozenset({
        "workspace.read", "workspace.manage", "users.read", "users.manage",
        "billing.read", "billing.manage", "accounts.read", "accounts.manage",
        "analytics.read", "reports.read", "messaging.read", "messaging.reply",
        "publishing.read", "publishing.create", "integrations.manage", "security.audit",
    }),
    "owner": frozenset({
        "workspace.read", "workspace.manage", "users.read", "users.manage",
        "billing.read", "billing.manage", "accounts.read", "accounts.manage",
        "analytics.read", "reports.read", "messaging.read", "messaging.reply",
        "publishing.read", "publishing.create", "integrations.manage", "security.audit",
    }),
    "admin": frozenset({
        "workspace.read", "users.read", "users.manage", "billing.read",
        "accounts.read", "accounts.manage", "analytics.read", "reports.read",
        "messaging.read", "messaging.reply", "publishing.read", "publishing.create",
        "integrations.manage", "security.audit",
    }),
    "editor": frozenset({
        "workspace.read", "accounts.read", "analytics.read", "reports.read",
        "messaging.read", "messaging.reply", "publishing.read", "publishing.create",
    }),
    "analyst": frozenset({
        "workspace.read", "accounts.read", "analytics.read", "reports.read", "publishing.read",
    }),
    "viewer": frozenset({
        "workspace.read", "accounts.read", "analytics.read", "reports.read", "publishing.read",
        "messaging.read",
    }),
}

VALID_ROLES = frozenset(ROLE_PERMISSIONS)


CSRF_SESSION_KEYS = (
    "csrf_token",
    "whatsapp_csrf",
    "facebook_messenger_csrf",
    "instagram_inbox_csrf",
)


def issue_csrf_token(session, key="csrf_token"):
    """Return a stable, unpredictable per-session CSRF token."""
    token = session.get(key)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session[key] = token
    return token


def valid_csrf_token(session, supplied):
    """Accept only a token issued into this same session."""
    if not isinstance(supplied, str) or not supplied:
        return False
    return any(
        isinstance(session.get(key), str)
        and secrets.compare_digest(session[key], supplied)
        for key in CSRF_SESSION_KEYS
    )


def normalize_role(role):
    value = str(role or "viewer").strip().lower()
    return value if value in VALID_ROLES else "viewer"


def has_permission(role, permission):
    return permission in ROLE_PERMISSIONS[normalize_role(role)]


def refresh_session_identity(session):
    """Refresh a logged-in identity and fail closed on stale tenant membership."""
    if not session.get("authenticated"):
        return False

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
                return False
            db_role, db_workspace_id, is_active, db_workspace_slug = identity
            if (
                not is_active
                or str(db_workspace_id) != str(workspace_id)
                or str(db_workspace_slug) != str(workspace_slug)
            ):
                session.clear()
                return False
            session["role"] = normalize_role(db_role)
            return True
        finally:
            if conn:
                conn.close()

    if session.get("role") == "platform_admin" and workspace_id and workspace_slug:
        return True

    session.clear()
    return False


def require_permission(permission):
    """Flask view decorator; refreshes identity and enforces the requested permission."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            from flask import abort, redirect, session, url_for
            from werkzeug.exceptions import HTTPException

            if not session.get("authenticated"):
                return redirect(url_for("login"))

            try:
                if not refresh_session_identity(session):
                    abort(401)
            except HTTPException:
                raise
            except Exception:
                session.clear()
                abort(503)

            if not has_permission(session.get("role"), permission):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate
