"""Workspace-scoped social-account identity resolution.

The immutable identity is (workspace_id, normalized platform, external_account_id).
Username is a mutable label and is never sufficient to merge accounts.

Direct OAuth may refresh credentials only on the row resolved by external ID,
or adopt a legacy row only when exactly one username match exists and that
row has no external ID. Ambiguity fails closed.

Facebook discovery resolves by external ID only. If that ID is not yet stored,
it creates a passive linked row rather than guessing from username.
"""

from __future__ import annotations


class SocialIdentityConflict(RuntimeError):
    """Raised when identity data is ambiguous or conflicts with another row."""


def _normalize(value):
    return str(value or "").strip()


def _lock_identity(cur, workspace_id, platform, username, external_account_id):
    """Serialize same-username and same-external-ID operations."""
    username_key = (
        f"social-username:{workspace_id}:{platform.casefold()}:{username.casefold()}"
    )
    external_key = (
        f"social-account:{workspace_id}:{platform.casefold()}:{external_account_id}"
    )

    # All callers acquire locks in this order to avoid lock-order deadlocks.
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (username_key,),
    )
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (external_key,),
    )


def _find_external_rows(cur, workspace_id, platform, external_account_id):
    cur.execute(
        """SELECT id, username
           FROM social_accounts
           WHERE workspace_id = %s
             AND LOWER(BTRIM(platform)) = LOWER(BTRIM(%s))
             AND BTRIM(COALESCE(external_account_id, '')) = %s
           ORDER BY created_at ASC, id ASC
           FOR UPDATE""",
        (workspace_id, platform, external_account_id),
    )
    return cur.fetchall()


def _find_username_rows(cur, workspace_id, platform, username):
    cur.execute(
        """SELECT id, external_account_id
           FROM social_accounts
           WHERE workspace_id = %s
             AND LOWER(BTRIM(platform)) = LOWER(BTRIM(%s))
             AND LOWER(BTRIM(COALESCE(username, ''))) = LOWER(BTRIM(%s))
           ORDER BY created_at ASC, id ASC
           FOR UPDATE""",
        (workspace_id, platform, username),
    )
    return cur.fetchall()


def _validate_required(workspace_id, platform, external_account_id, username):
    if not all((_normalize(workspace_id), _normalize(platform),
                _normalize(external_account_id), _normalize(username))):
        raise ValueError(
            "Workspace, platform, external ID and username are required."
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
    """Create or refresh a direct-OAuth account using external ID as identity."""
    workspace_id = _normalize(workspace_id)
    platform = _normalize(platform)
    external_account_id = _normalize(external_account_id)
    username = _normalize(username)
    display_name = _normalize(display_name) or username
    _validate_required(workspace_id, platform, external_account_id, username)

    _lock_identity(cur, workspace_id, platform, username, external_account_id)

    # Immutable external identity has priority over mutable username.
    external_rows = _find_external_rows(
        cur, workspace_id, platform, external_account_id
    )
    if len(external_rows) > 1:
        raise SocialIdentityConflict(
            "Duplicate external identity; reconciliation is required."
        )
    if external_rows:
        # The external ID is immutable identity; usernames may legitimately change.
        account_id = external_rows[0][0]
    else:
        username_rows = _find_username_rows(
            cur, workspace_id, platform, username
        )
        if len(username_rows) > 1:
            raise SocialIdentityConflict(
                "Username matches multiple historical accounts and no exact "
                "external-ID match exists; reconciliation is required."
            )
        if username_rows:
            legacy_external_id = _normalize(username_rows[0][1])
            if legacy_external_id:
                raise SocialIdentityConflict(
                    "Username belongs to a different external identity; "
                    "automatic reassignment is blocked."
                )
            # Safe legacy adoption: exactly one row and no stored external ID.
            account_id = username_rows[0][0]
        else:
            cur.execute(
                """INSERT INTO social_accounts
                   (workspace_id, platform, username, display_name,
                    external_account_id, access_token, connection_status,
                    is_active, last_sync_at)
                   VALUES (%s, %s, %s, %s, %s, %s,
                           'connected', TRUE, NULL)
                   RETURNING id""",
                (
                    workspace_id, platform, username, display_name,
                    external_account_id, encrypted_token,
                ),
            )
            inserted = cur.fetchone()
            if not inserted:
                raise SocialIdentityConflict(
                    "The database did not return the newly created account ID."
                )
            return inserted[0]

    # Only the resolved row is refreshed. Historical same-username rows remain
    # untouched: no deactivation, deletion, token overwrite or metric movement.
    cur.execute(
        """UPDATE social_accounts
           SET username = %s,
               display_name = %s,
               external_account_id = %s,
               access_token = %s,
               connection_status = 'connected',
               is_active = TRUE
           WHERE id = %s
             AND workspace_id = %s
             AND LOWER(BTRIM(platform)) = LOWER(BTRIM(%s))""",
        (
            username, display_name, external_account_id, encrypted_token,
            account_id, workspace_id, platform,
        ),
    )
    if cur.rowcount != 1:
        raise SocialIdentityConflict(
            "Could not refresh the resolved account in the requested workspace."
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
    """Link an account discovered through another platform by external ID.

    This function never changes OAuth credentials or account state. It does
    not infer identity from username. If the external ID is not yet present,
    it creates a passive linked row with no token and is_active=FALSE.
    """
    workspace_id = _normalize(workspace_id)
    platform = _normalize(platform)
    external_account_id = _normalize(external_account_id)
    username = _normalize(username)
    display_name = _normalize(display_name) or username
    _validate_required(workspace_id, platform, external_account_id, username)

    _lock_identity(cur, workspace_id, platform, username, external_account_id)
    external_rows = _find_external_rows(
        cur, workspace_id, platform, external_account_id
    )
    if len(external_rows) > 1:
        raise SocialIdentityConflict(
            "Duplicate external identity; reconciliation is required."
        )
    if external_rows:
        # The external ID is immutable identity; usernames may legitimately change.
        account_id = external_rows[0][0]

        # Fill only blank labels; never change token, external ID or state.
        cur.execute(
            """UPDATE social_accounts
               SET username = CASE
                       WHEN NULLIF(BTRIM(COALESCE(username, '')), '') IS NULL
                       THEN %s ELSE username END,
                   display_name = CASE
                       WHEN NULLIF(BTRIM(COALESCE(display_name, '')), '') IS NULL
                       THEN %s ELSE display_name END
               WHERE id = %s
                 AND workspace_id = %s
                 AND LOWER(BTRIM(platform)) = LOWER(BTRIM(%s))""",
            (username, display_name, account_id, workspace_id, platform),
        )
        if cur.rowcount != 1:
            raise SocialIdentityConflict(
                "Could not update missing labels on the discovered account."
            )
        return account_id

    # Username is deliberately not used as a fallback identity.
    cur.execute(
        """INSERT INTO social_accounts
           (workspace_id, platform, username, display_name,
            external_account_id, access_token, connection_status,
            is_active, last_sync_at)
           VALUES (%s, %s, %s, %s, %s, NULL, 'linked', FALSE, NULL)
           RETURNING id""",
        (workspace_id, platform, username, display_name, external_account_id),
    )
    inserted = cur.fetchone()
    if not inserted:
        raise SocialIdentityConflict(
            "The database did not return the discovered account ID."
        )
    return inserted[0]
