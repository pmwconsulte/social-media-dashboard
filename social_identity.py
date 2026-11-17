"""Workspace-scoped identity resolution for connected social accounts.

Canonical identity: workspace + platform + username.

Direct OAuth updates credentials. Facebook discovery only links an existing
Instagram identity and never overwrites its OAuth credentials.
Historical account rows are preserved.
"""

from __future__ import annotations


class SocialIdentityConflict(RuntimeError):
    """Raised when an external identity conflicts with another username."""


def _normalize(value):
    return str(value or "").strip()


def _lock_identity(cur, workspace_id, platform, username, external_account_id):
    """Serialize concurrent operations using a consistent lock order."""
    username_key = (
        f"social-username:{workspace_id}:"
        f"{platform.casefold()}:{username.casefold()}"
    )
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (username_key,),
    )

    identity_key = (
        f"social-account:{workspace_id}:"
        f"{platform.casefold()}:{external_account_id}"
    )
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (identity_key,),
    )


def _find_username_rows(cur, workspace_id, platform, username):
    cur.execute(
        """SELECT id, external_account_id, is_active, connection_status
           FROM social_accounts
           WHERE workspace_id = %s
             AND LOWER(platform) = LOWER(%s)
             AND LOWER(BTRIM(COALESCE(username, ''))) = LOWER(%s)
             ORDER BY
             CASE WHEN LOWER(COALESCE(connection_status, '')) = 'connected'
                  THEN 0 ELSE 1 END,
             COALESCE(is_active, FALSE) DESC,
             created_at ASC,
             id ASC
           FOR UPDATE""",
        (workspace_id, platform, username),
    )
    return cur.fetchall()


def _find_external_rows(cur, workspace_id, platform, external_account_id):
    cur.execute(
        """SELECT id, username
           FROM social_accounts
           WHERE workspace_id = %s
             AND LOWER(platform) = LOWER(%s)
             AND BTRIM(COALESCE(external_account_id, '')) = %s
           ORDER BY created_at ASC, id ASC
           FOR UPDATE""",
        (workspace_id, platform, external_account_id),
    )
    return cur.fetchall()


def _validate_external_owner(rows, username):
    """Reject an external ID already owned by another username."""
    for row in rows:
        existing_username = _normalize(row[1])
        if (
            existing_username
            and existing_username.casefold() != username.casefold()
        ):
            raise SocialIdentityConflict(
                "The external ID belongs to a different username."
            )

def _deactivate_duplicates(cur, workspace_id, platform, username, account_id):
    """Preserve duplicate rows, but deactivate them."""
    cur.execute(
        """UPDATE social_accounts
           SET is_active = FALSE
           WHERE workspace_id = %s
             AND LOWER(platform) = LOWER(%s)
             AND LOWER(BTRIM(COALESCE(username, ''))) = LOWER(%s)
             AND id <> %s
             AND COALESCE(is_active, FALSE) = TRUE""",
        (workspace_id, platform, username, account_id),
    )

def upsert_social_account(
    cur,
    *,
    workspace_id,
    platform,
    external_account_id,
    username,
    display_name,
    encrypted_token,
):
    """Create or update an account through its direct OAuth flow.

    The canonical account is selected by workspace, platform and username.
    Direct OAuth is allowed to refresh its external ID and access token.
    """
    workspace_id = _normalize(workspace_id)
    platform = _normalize(platform)
    external_account_id = _normalize(external_account_id)
    username = _normalize(username)
    display_name = _normalize(display_name) or username

    if not all((workspace_id, platform, external_account_id, username)):
        raise ValueError(
            "Workspace, platform, external ID and username are required."
        )

    _lock_identity(
        cur, workspace_id, platform, username, external_account_id
    )

    username_rows = _find_username_rows(
        cur, workspace_id, platform, username
    )
    external_rows = _find_external_rows(
        cur, workspace_id, platform, external_account_id
    )

    _validate_external_owner(external_rows, username)

    if len(external_rows) > 1:
        raise SocialIdentityConflict(
            "Duplicate external identity; reconciliation required."
        )

    if len(username_rows) > 1:
        raise SocialIdentityConflict(
            "Multiple accounts share this username; reconciliation required."
        )

    if (
        username_rows
        and external_rows
        and username_rows[0][0] != external_rows[0][0]
    ):
        raise SocialIdentityConflict(
            "Username and external ID resolve to different accounts; "
            "reconciliation required."
        )

    if username_rows:
        account_id = username_rows[0][0]
    elif external_rows:
        account_id = external_rows[0][0]
    else:
        cur.execute(
            """INSERT INTO social_accounts
               (workspace_id, platform, username, display_name,
                external_account_id, access_token, connection_status,
                is_active, last_sync_at)
               VALUES (%s, %s, %s, %s, %s, %s,
                       'connected', TRUE, NOW())
               RETURNING id""",
            (
                workspace_id,
                platform,
                username,
                display_name,
                external_account_id,
                encrypted_token,
            ),
        )
        account_id = cur.fetchone()[0]

    _deactivate_duplicates(
        cur, workspace_id, platform, username, account_id
    )

    cur.execute(
        """UPDATE social_accounts
           SET username = %s,
               display_name = %s,
               external_account_id = %s,
               access_token = %s,
               connection_status = 'connected',
               is_active = TRUE,
               last_sync_at = NOW()
           WHERE id = %s
             AND workspace_id = %s
             AND LOWER(platform) = LOWER(%s)""",
        (
            username,
            display_name,
            external_account_id,
            encrypted_token,
            account_id,
            workspace_id,
            platform,
        ),
    )

    if cur.rowcount != 1:
        raise SocialIdentityConflict(
            "Could not update the canonical account in the active workspace."
        )

    return account_id


def link_discovered_social_account(
    cur,
    *,
    workspace_id,
    platform,
    external_account_id,
    username,
    display_name=None,
):
    """Link an account discovered through another platform.

    Reuses the existing username identity without changing its external ID,
    OAuth token, connection status or active state.
    Creates a passive linked row only when no identity exists.
    """
    workspace_id = _normalize(workspace_id)
    platform = _normalize(platform)
    external_account_id = _normalize(external_account_id)
    username = _normalize(username)
    display_name = _normalize(display_name) or username

    if not all((workspace_id, platform, external_account_id, username)):
        raise ValueError(
            "Workspace, platform, external ID and username are required."
        )

    _lock_identity(
        cur, workspace_id, platform, username, external_account_id
    )

    username_rows = _find_username_rows(
        cur, workspace_id, platform, username
    )
    external_rows = _find_external_rows(
        cur, workspace_id, platform, external_account_id
    )

    _validate_external_owner(external_rows, username)

    if len(external_rows) > 1:
        raise SocialIdentityConflict(
            "Duplicate external identity; reconciliation required."
        )
    if (
        username_rows
        and external_rows
        and username_rows[0][0] != external_rows[0][0]
    ):
        raise SocialIdentityConflict(
            "Username and external ID resolve to different accounts; "
            "reconciliation required."
        )
    if username_rows:
        # Preserve the existing direct OAuth account and its credentials.
        account_id = username_rows[0][0]

    elif external_rows:
        account_id = external_rows[0][0]

        # Only fill missing identity labels; never replace credentials or state.
        cur.execute(
            """UPDATE social_accounts
               SET username = CASE
                       WHEN NULLIF(BTRIM(COALESCE(username, '')), '') IS NULL
                       THEN %s ELSE username END,
                   display_name = CASE
                       WHEN NULLIF(BTRIM(COALESCE(display_name, '')), '') IS NULL
                       THEN %s ELSE display_name END
               WHERE id = %s AND workspace_id = %s
                 AND LOWER(platform) = LOWER(%s)""",
            (username, display_name, account_id, workspace_id, platform),
        )

        if cur.rowcount != 1:
            raise SocialIdentityConflict(
                "Could not resolve the discovered account."
            )

    else:
        # Passive discovery does not activate the Instagram integration.
        cur.execute(
            """INSERT INTO social_accounts
               (workspace_id, platform, username, display_name,
                external_account_id, access_token, connection_status,
                is_active, last_sync_at)
               VALUES (%s, %s, %s, %s, %s, NULL, 'linked', FALSE, NOW())
               RETURNING id""",
            (
                workspace_id,
                platform,
                username,
                display_name,
                external_account_id,
            ),
        )
        account_id = cur.fetchone()[0]


    return account_id
