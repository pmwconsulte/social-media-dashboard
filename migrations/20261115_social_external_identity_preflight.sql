-- PMW Social Identity v1 - READ-ONLY PREFLIGHT
-- Run this file before 20261115_social_external_identity.sql.
-- This file performs SELECTs only. It does not create, update, or delete data.
-- Review every result set. Any anomaly requires stopping and manual review.

\echo '1) Schema: external_account_id must be absent or VARCHAR(255)'
SELECT table_schema, table_name, column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'social_accounts'
  AND column_name = 'external_account_id';

\echo '2) Existing index with the reserved name: inspect; migration expects no existing index'
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname = 'uq_social_accounts_external_identity';

\echo '3) Linked Instagram mappings with missing IDs or multiple distinct official IDs (must return zero rows)'
SELECT instagram_social_account_id,
       COUNT(*) AS link_rows,
       COUNT(DISTINCT NULLIF(BTRIM(instagram_account_id), '')) AS distinct_nonblank_ids,
       COUNT(*) FILTER (WHERE NULLIF(BTRIM(instagram_account_id), '') IS NULL) AS blank_id_rows
FROM facebook_instagram_links
WHERE instagram_social_account_id IS NOT NULL
GROUP BY instagram_social_account_id
HAVING COUNT(DISTINCT NULLIF(BTRIM(instagram_account_id), '')) > 1
    OR COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(instagram_account_id), '') IS NULL
    ) > 0;

\echo '4) Invalid Facebook/Instagram relationships (must return zero rows)'
SELECT fil.social_account_id,
       fil.page_id,
       fil.instagram_social_account_id,
       fil.instagram_account_id,
       fb.platform AS facebook_platform,
       ig.platform AS instagram_platform,
       fb.workspace_id AS facebook_workspace,
       ig.workspace_id AS instagram_workspace
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
      OR NULLIF(BTRIM(fil.instagram_account_id), '') IS NULL
  );

\echo '5) Projected external identity collisions after backfill (must return zero rows)'
WITH instagram_mapping AS (
    SELECT instagram_social_account_id,
           MIN(BTRIM(instagram_account_id)) AS external_account_id
    FROM facebook_instagram_links
    WHERE instagram_social_account_id IS NOT NULL
      AND NULLIF(BTRIM(instagram_account_id), '') IS NOT NULL
    GROUP BY instagram_social_account_id
),
projected AS (
    SELECT sa.id,
           sa.workspace_id,
           LOWER(sa.platform) AS platform,
           CASE
               WHEN LOWER(sa.platform) = 'facebook'
                    AND NULLIF(BTRIM(sa.username), '') IS NOT NULL
                   THEN BTRIM(sa.username)
               WHEN LOWER(sa.platform) = 'instagram'
                    AND LOWER(COALESCE(sa.connection_status, '')) = 'linked'
                   THEN im.external_account_id
               ELSE NULL
           END AS external_account_id
    FROM social_accounts sa
    LEFT JOIN instagram_mapping im
      ON im.instagram_social_account_id = sa.id
)
SELECT workspace_id, platform, external_account_id, COUNT(*) AS account_rows,
       ARRAY_AGG(id::text ORDER BY id) AS account_ids
FROM projected
WHERE external_account_id IS NOT NULL
GROUP BY workspace_id, platform, external_account_id
HAVING workspace_id IS NULL OR COUNT(*) > 1;

\echo '6) Rows that the migration would backfill (review IDs and values; no tokens shown)'
WITH instagram_mapping AS (
    SELECT instagram_social_account_id,
           MIN(BTRIM(instagram_account_id)) AS external_account_id
    FROM facebook_instagram_links
    WHERE instagram_social_account_id IS NOT NULL
      AND NULLIF(BTRIM(instagram_account_id), '') IS NOT NULL
    GROUP BY instagram_social_account_id
)
SELECT sa.id,
       sa.workspace_id,
       sa.platform,
       sa.username,
       sa.connection_status,
       sa.is_active,
       CASE
           WHEN LOWER(sa.platform) = 'facebook'
               THEN NULLIF(BTRIM(sa.username), '')
           WHEN LOWER(sa.platform) = 'instagram'
                AND LOWER(COALESCE(sa.connection_status, '')) = 'linked'
               THEN im.external_account_id
       END AS proposed_external_account_id
FROM social_accounts sa
LEFT JOIN instagram_mapping im
  ON im.instagram_social_account_id = sa.id
WHERE (
        LOWER(sa.platform) = 'facebook'
        AND NULLIF(BTRIM(sa.username), '') IS NOT NULL
      )
   OR (
        LOWER(sa.platform) = 'instagram'
        AND LOWER(COALESCE(sa.connection_status, '')) = 'linked'
        AND im.external_account_id IS NOT NULL
      )
ORDER BY sa.workspace_id, LOWER(sa.platform), sa.created_at, sa.id;

\echo '7) Summary counts of candidate backfills'
SELECT
    COUNT(*) FILTER (
        WHERE LOWER(platform) = 'facebook'
          AND NULLIF(BTRIM(username), '') IS NOT NULL
    ) AS facebook_candidates,
    COUNT(*) FILTER (
        WHERE LOWER(platform) = 'instagram'
          AND LOWER(COALESCE(connection_status, '')) = 'linked'
          AND id IN (
              SELECT instagram_social_account_id
              FROM facebook_instagram_links
              WHERE instagram_social_account_id IS NOT NULL
                AND NULLIF(BTRIM(instagram_account_id), '') IS NOT NULL
          )
    ) AS linked_instagram_candidates
FROM social_accounts;
