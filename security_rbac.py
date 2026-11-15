"""Central RBAC primitives for PMW Dashboard.

Permissions are additive and workspace-scoped. This module has no Flask or
database side effects at import time.
"""
from functools import wraps
import hashlib
import hmac
import os
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


def credential_fingerprint(password_hash, secret_key):
    """Bind a browser session to the current credential without exposing its hash."""
    if not password_hash or not secret_key:
        return None
    key = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
    return hmac.new(
        key,
        str(password_hash).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()




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
                              w.slug, u.password_hash
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
            db_role, db_workspace_id, is_active, db_workspace_slug, db_password_hash = identity
            from flask import current_app
            expected_fingerprint = credential_fingerprint(
                db_password_hash, current_app.secret_key
            )
            supplied_fingerprint = session.get("credential_fingerprint", "")
            if (
                not is_active
                or str(db_workspace_id) != str(workspace_id)
                or str(db_workspace_slug) != str(workspace_slug)
                or not expected_fingerprint
                or not isinstance(supplied_fingerprint, str)
                or not hmac.compare_digest(expected_fingerprint, supplied_fingerprint)
            ):
                session.clear()
                return False
            # platform_admin is reserved exclusively for the environment
            # administrator authenticated through ADMIN_USERNAME/ADMIN_PASSWORD_HASH.
            # A database user must never gain platform-wide privileges by a role row.
            normalized_db_role = normalize_role(db_role)
            if normalized_db_role == "platform_admin":
                session.clear()
                return False
            session["role"] = normalized_db_role
            return True
        finally:
            if conn:
                conn.close()

    if session.get("role") == "platform_admin" and workspace_id and workspace_slug:
        # Environment-admin sessions are bound to both the exact workspace and
        # the current ADMIN_PASSWORD_HASH, so credential rotation revokes them.
        admin_hash = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
        admin_username = os.getenv("ADMIN_USERNAME", "").strip()
        from flask import current_app
        expected_fingerprint = credential_fingerprint(admin_hash, current_app.secret_key)
        supplied_fingerprint = session.get("credential_fingerprint", "")
        if (
            not admin_username
            or session.get("username") != admin_username
            or not expected_fingerprint
            or not isinstance(supplied_fingerprint, str)
            or not hmac.compare_digest(expected_fingerprint, supplied_fingerprint)
        ):
            session.clear()
            return False
        conn = None
        try:
            from database import get_connection
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM workspaces WHERE id=%s AND slug=%s LIMIT 1",
                    (workspace_id, workspace_slug),
                )
                workspace = cur.fetchone()
            if workspace:
                return True
        finally:
            if conn:
                conn.close()

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
