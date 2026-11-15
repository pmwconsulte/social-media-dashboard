"""Central role-based access control for PMW Dashboard routes.

Roles are intentionally tenant-scoped. platform_admin is reserved for the
explicit environment-backed platform administrator account.
"""
from functools import wraps

from flask import jsonify, redirect, request, session, url_for


ROLE_PERMISSIONS = {
    "platform_admin": frozenset({"*"}),
    "owner": frozenset({
        "dashboard:view", "analytics:view", "analytics:sync",
        "messaging:view", "messaging:reply", "publishing:create",
        "integrations:manage", "workspace:manage", "users:manage",
        "billing:manage", "profile:manage",
    }),
    "admin": frozenset({
        "dashboard:view", "analytics:view", "analytics:sync",
        "messaging:view", "messaging:reply", "publishing:create",
        "integrations:manage", "workspace:manage", "users:manage",
        "profile:manage",
    }),
    "editor": frozenset({
        "dashboard:view", "analytics:view", "messaging:view",
        "messaging:reply", "publishing:create", "profile:manage",
    }),
    "analyst": frozenset({
        "dashboard:view", "analytics:view", "messaging:view",
        "profile:manage",
    }),
    "viewer": frozenset({
        "dashboard:view", "analytics:view", "messaging:view",
        "profile:manage",
    }),
}

VALID_ROLES = frozenset(ROLE_PERMISSIONS)


def normalize_role(role):
    value = str(role or "viewer").strip().lower()
    return value if value in VALID_ROLES else "viewer"


def has_permission(role, permission):
    permissions = ROLE_PERMISSIONS.get(normalize_role(role), frozenset())
    return "*" in permissions or permission in permissions


def current_role():
    return normalize_role(session.get("role"))


def permission_denied(permission):
    response = jsonify({
        "error": "permission_denied",
        "required_permission": permission,
    })
    response.status_code = 403
    response.headers["Cache-Control"] = "no-store"
    return response


def role_required(permission):
    """Require authentication and one permission, or map permissions by HTTP method."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("authenticated"):
                if request.method == "GET":
                    return redirect(url_for("login"))
                return jsonify({"error": "authentication_required"}), 401

            required = (
                permission.get(request.method)
                if isinstance(permission, dict)
                else permission
            )
            if not required:
                return permission_denied("route_not_configured")
            if not has_permission(current_role(), required):
                return permission_denied(required)
            return view(*args, **kwargs)
        return wrapped
    return decorator
