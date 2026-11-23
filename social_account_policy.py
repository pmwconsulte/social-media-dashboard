def has_social_account_slot(
    cur,
    *,
    workspace_id,
    platform,
    external_account_id,
    username,
    account_limit,
):
    """Check whether a social identity may connect without exceeding the workspace plan.

    Active identities already occupying a slot may reconnect.
    Passive linked identities and inactive identities do not bypass the limit.
    The limit is global to the workspace, not platform-specific.
    """
    external_id = str(external_account_id or "").strip()
    normalized_username = str(username or "").strip()

    cur.execute(
        """SELECT COUNT(*)
           FROM social_accounts
           WHERE workspace_id = %s
             AND COALESCE(is_active, TRUE) = TRUE
             AND LOWER(BTRIM(COALESCE(connection_status, ''))) <> 'linked'""",
        (workspace_id,),
    )
    active_accounts = cur.fetchone()[0]

    cur.execute(
        """SELECT COUNT(*)
           FROM social_accounts
           WHERE workspace_id = %s
             AND LOWER(BTRIM(platform)) = LOWER(BTRIM(%s))
             AND COALESCE(is_active, TRUE) = TRUE
             AND LOWER(BTRIM(COALESCE(connection_status, ''))) <> 'linked'
             AND (
                 (%s <> '' AND BTRIM(COALESCE(external_account_id, '')) = %s)
                 OR (
                     %s <> ''
                     AND LOWER(BTRIM(COALESCE(username, ''))) = LOWER(BTRIM(%s))
                     AND BTRIM(COALESCE(external_account_id, '')) = ''
                 )
             )""",
        (
            workspace_id,
            platform,
            external_id,
            external_id,
            normalized_username,
            normalized_username,
        ),
    )
    existing_active_identity_count = cur.fetchone()[0]

    if existing_active_identity_count > 0:
        return True

    if isinstance(account_limit, int) and active_accounts >= account_limit:
        return False

    return True
