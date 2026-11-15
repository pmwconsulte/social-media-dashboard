-- PMW Social Identity v1
-- Apply only after reviewing the preflight checks below. This file is not
-- executed automatically by application startup.
BEGIN;

ALTER TABLE social_accounts
    ADD COLUMN IF NOT EXISTS external_account_id VARCHAR(255);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM facebook_instagram_links
        WHERE instagram_social_account_id IS NOT NULL
        GROUP BY instagram_social_account_id
        HAVING COUNT(DISTINCT instagram_account_id) > 1
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: one linked social account maps to multiple Instagram external IDs';
    END IF;
END $;

-- Refuse to attach an external ID to a missing, wrong-platform, wrong-workspace,
-- or already-conflicting internal identity.
DO $
BEGIN
    IF EXISTS (
        SELECT 1
        FROM facebook_instagram_links fil
        LEFT JOIN social_accounts fb ON fb.id::text = fil.social_account_id
        LEFT JOIN social_accounts ig ON ig.id = fil.instagram_social_account_id
        WHERE fil.instagram_social_account_id IS NOT NULL
          AND (
              fb.id IS NULL
              OR ig.id IS NULL
              OR LOWER(COALESCE(fb.platform, '')) <> 'facebook'
              OR LOWER(COALESCE(ig.platform, '')) <> 'instagram'
              OR fb.workspace_id IS DISTINCT FROM ig.workspace_id
              OR (
                  ig.external_account_id IS NOT NULL
                  AND ig.external_account_id <> fil.instagram_account_id
              )
          )
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: Facebook/Instagram link has an invalid workspace, platform, or external identity';
    END IF;
END $;

-- Facebook page IDs are already stored in username by the existing OAuth
-- contract. Preserve that official identifier during the schema transition.
UPDATE social_accounts
SET external_account_id = BTRIM(username)
WHERE LOWER(platform) = 'facebook'
  AND external_account_id IS NULL
  AND NULLIF(BTRIM(username), '') IS NOT NULL;

-- Backfill only the explicit Facebook-discovered linked Instagram identity.
-- Do not infer IDs for direct Instagram OAuth rows from usernames or copy history.
UPDATE social_accounts sa
SET external_account_id = mapping.instagram_account_id
FROM (
    SELECT instagram_social_account_id, MIN(instagram_account_id) AS instagram_account_id
    FROM facebook_instagram_links
    WHERE instagram_social_account_id IS NOT NULL
    GROUP BY instagram_social_account_id
) AS mapping
WHERE sa.id = mapping.instagram_social_account_id
  AND LOWER(sa.platform) = 'instagram'
  AND LOWER(COALESCE(sa.connection_status, '')) = 'linked'
  AND sa.external_account_id IS NULL;

DO $
BEGIN
    IF EXISTS (
        SELECT 1
        FROM social_accounts
        WHERE external_account_id IS NOT NULL
          AND workspace_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: external identities without workspace ownership require manual review';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM social_accounts
        WHERE external_account_id IS NOT NULL
        GROUP BY workspace_id, LOWER(platform), external_account_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: duplicate external identities need manual reconciliation';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_social_accounts_external_identity
    ON social_accounts (workspace_id, LOWER(platform), external_account_id)
    WHERE external_account_id IS NOT NULL;

COMMIT;
