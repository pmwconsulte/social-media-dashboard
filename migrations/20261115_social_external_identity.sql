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
END $$;

-- Backfill only the explicit Facebook-discovered linked identity. Do not infer
-- IDs for direct Instagram OAuth rows from usernames or copy historical rows.
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

DO $$
BEGIN
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
