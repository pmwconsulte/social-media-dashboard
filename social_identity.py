"""Workspace-scoped external identity resolution for connected social accounts.

All callers must execute this helper inside the transaction that owns cur.
The advisory transaction lock serializes concurrent OAuth callbacks for the same
workspace/platform/external ID; the partial unique index is the final DB guard.
"""

from __future__ import annotations


class SocialIdentityConflict(RuntimeError):
    """Raised when legacy rows make an external identity ambiguous."""


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
    """Resolve one external identity and persist its current OAuth credential.

    Exact external ID always wins over mutable usernames. A legacy username
    fallback is accepted only when it identifies exactly one row and that row
    has no conflicting external ID. Ambiguity fails closed instead of creating
    another duplicate account.
    """
    workspace_id = str(workspace_id or "").strip()
    platform = str(platform or "").strip()
    external_account_id = str(external_account_id or "").strip()
    username = str(username or "").strip()
    display_name = str(display_name or username).strip()

    if not workspace_id or not platform or not external_account_id or not username:
        raise ValueError("Workspace, platform, external ID and username are required.")

    identity_key = f"social-account:{workspace_id}:{platform.casefold()}:{external_account_id}"
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (identity_key,))

    cur.execute(
        """SELECT id FROM social_accounts
           WHERE workspace_id=%s
             AND LOWER(platform)=LOWER(%s)
             AND external_account_id=%s
           LIMIT 2 FOR UPDATE""",
        (workspace_id, platform, external_account_id),
    )
    exact_rows = cur.fetchall()
    if len(exact_rows) > 1:
        raise SocialIdentityConflict("External identity is duplicated in this workspace.")

    if exact_rows:
        account_id = exact_rows[0][0]
    else:
        cur.execute(
            """SELECT id, external_account_id FROM social_accounts
               WHERE workspace_id=%s
                 AND LOWER(platform)=LOWER(%s)
                 AND LOWER(COALESCE(username, ''))=LOWER(%s)
               ORDER BY created_at, id
               LIMIT 2 FOR UPDATE""",
            (workspace_id, platform, username),
        )
        legacy_rows = cur.fetchall()
        if len(legacy_rows) > 1:
            raise SocialIdentityConflict(
                "Multiple legacy accounts share this username; manual reconciliation is required."
            )
        if legacy_rows:
            account_id, legacy_external_id = legacy_rows[0]
            if legacy_external_id and str(legacy_external_id) != external_account_id:
                raise SocialIdentityConflict(
                    "The username belongs to a different external account."
                )
            cur.execute(
                """UPDATE social_accounts
                   SET external_account_id=%s
                   WHERE id=%s AND workspace_id=%s
                     AND (external_account_id IS NULL OR external_account_id=%s)""",
                (external_account_id, account_id, workspace_id, external_account_id),
            )
            if cur.rowcount != 1:
                raise SocialIdentityConflict("The legacy identity changed during OAuth.")
        else:
            cur.execute(
                """INSERT INTO social_accounts
                   (workspace_id, platform, username, display_name, external_account_id,
                    access_token, connection_status, is_active, last_sync_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'connected', TRUE, NOW())
                   RETURNING id""",
                (
                    workspace_id, platform, username, display_name,
                    external_account_id, encrypted_token,
                ),
            )
            account_id = cur.fetchone()[0]

    cur.execute(
        """UPDATE social_accounts
           SET username=%s, display_name=%s, external_account_id=%s,
               access_token=%s, connection_status='connected',
               is_active=TRUE, last_sync_at=NOW()
           WHERE id=%s AND workspace_id=%s""",
        (
            username, display_name, external_account_id, encrypted_token,
            account_id, workspace_id,
        ),
    )
    if cur.rowcount != 1:
        raise SocialIdentityConflict("Resolved account is outside the active workspace.")
    return account_id
