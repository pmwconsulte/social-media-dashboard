
-- PMW Social Identity v2 - READ-ONLY PREFLIGHT
-- Run before 20261115_social_external_identity.sql.
-- This file performs SELECT statements only.
-- No records, tokens, indexes, or schemas are modified.
-- Any anomaly must be reviewed before running the migration.

\set ON_ERROR_STOP on

\echo '1) Required tables and columns'
SELECT table_name, column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'public'
  AND (
      (table_name = 'social_accounts'
       AND column_name IN (
           'id', 'workspace_id', 'platform', 'username',
           'connection_status', 'external_account_id'
       ))
      OR
      (table_name = 'facebook_instagram_links'
       AND column_name IN (
           'social_account_id', 'page_id', 'instagram_account_id',
           'instagram_username', 'instagram_social_account_id'
       ))
  )
ORDER BY table_name, ordinal_position;

\echo '2) Existing external identity column - expected VARCHAR(255)'

SELECT
    table_schema,
    table_name,
    column_name,
    data_type,
    character_maximum_length,
    CASE
        WHEN data_type = 'character varying'
         AND character_maximum_length = 255
        THEN 'OK'
        ELSE 'ANOMALY - expected VARCHAR(255)'
    END AS validation
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'social_accounts'
  AND column_name = 'external_account_id';
\echo '3) Existing reserved index'
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname = 'uq_social_accounts_external_identity';

\echo '4) Mapping rows with missing IDs or ambiguous Instagram identities'
SELECT instagram_social_account_id,
       COUNT(*) AS link_rows,
       COUNT(DISTINCT NULLIF(BTRIM(instagram_account_id), ''))
           AS distinct_nonblank_ids,
       COUNT(*) FILTER (
           WHERE NULLIF(BTRIM(instagram_account_id), '') IS NULL
       ) AS blank_id_rows
FROM public.facebook_instagram_links
GROUP BY instagram_social_account_id
HAVING instagram_social_account_id IS NULL
    OR COUNT(DISTINCT NULLIF(BTRIM(instagram_account_id), '')) <> 1
    OR COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(instagram_account_id), '') IS NULL
    ) > 0;

\echo '5) Invalid Facebook and Instagram relationships'
SELECT fil.social_account_id,
       fil.page_id,
       fil.instagram_social_account_id,
       fb.platform AS facebook_platform,
       ig.platform AS instagram_platform,
       fb.workspace_id AS facebook_workspace,
       ig.workspace_id AS instagram_workspace,
       fb.id IS NOT NULL AS facebook_exists,
       ig.id IS NOT NULL AS instagram_exists
FROM public.facebook_instagram_links fil
LEFT JOIN public.social_accounts fb
       ON fb.id::text = fil.social_account_id
LEFT JOIN public.social_accounts ig
       ON ig.id = fil.instagram_social_account_id
WHERE fil.social_account_id IS NULL
   OR fil.instagram_social_account_id IS NULL
   OR fb.id IS NULL
   OR ig.id IS NULL
   OR LOWER(COALESCE(fb.platform, '')) <> 'facebook'
   OR LOWER(COALESCE(ig.platform, '')) <> 'instagram'
   OR fb.workspace_id IS NULL
   OR ig.workspace_id IS NULL
   OR fb.workspace_id IS DISTINCT FROM ig.workspace_id
   OR NULLIF(BTRIM(fil.page_id), '') IS NULL
   OR NULLIF(BTRIM(fil.instagram_account_id), '') IS NULL
   OR (
       LOWER(COALESCE(ig.connection_status, '')) <> 'connected'
       AND NULLIF(BTRIM(ig.external_account_id), '') IS NOT NULL
       AND BTRIM(ig.external_account_id)
           <> BTRIM(fil.instagram_account_id)
   );

\echo '6) Instagram mappings with conflicting official external IDs'
SELECT fil.instagram_social_account_id,
       COUNT(DISTINCT BTRIM(fil.instagram_account_id))
           AS distinct_external_ids,
       ARRAY_AGG(DISTINCT BTRIM(fil.instagram_account_id))
           AS external_ids
FROM public.facebook_instagram_links fil
WHERE NULLIF(BTRIM(fil.instagram_account_id), '') IS NOT NULL
GROUP BY fil.instagram_social_account_id
HAVING COUNT(DISTINCT BTRIM(fil.instagram_account_id)) > 1;

\echo '7) Projected identity collisions after the safe Instagram backfill'

WITH instagram_mapping AS (
    SELECT
        instagram_social_account_id,
        MIN(BTRIM(instagram_account_id)) AS external_account_id
    FROM public.facebook_instagram_links
    WHERE instagram_social_account_id IS NOT NULL
      AND NULLIF(BTRIM(instagram_account_id), '') IS NOT NULL
    GROUP BY instagram_social_account_id
    HAVING COUNT(DISTINCT BTRIM(instagram_account_id)) = 1
       AND COUNT(*) = COUNT(
           NULLIF(BTRIM(instagram_account_id), '')
       )
),
projected AS (
    SELECT
        sa.id,
        sa.workspace_id,
        LOWER(sa.platform) AS platform,
        COALESCE(
            NULLIF(BTRIM(sa.external_account_id), ''),
            CASE
                WHEN LOWER(BTRIM(sa.platform)) = 'instagram'
                 AND LOWER(COALESCE(sa.connection_status, '')) = 'linked'
                 AND im.external_account_id IS NOT NULL
                THEN im.external_account_id
                ELSE NULL
            END
        ) AS external_account_id
    FROM public.social_accounts sa
    LEFT JOIN instagram_mapping im
           ON im.instagram_social_account_id = sa.id
)
SELECT
    workspace_id,
    platform,
    BTRIM(external_account_id) AS external_account_id,
    COUNT(*) AS account_rows,
    ARRAY_AGG(id::text ORDER BY id) AS account_ids
FROM projected
WHERE NULLIF(BTRIM(external_account_id), '') IS NOT NULL
GROUP BY workspace_id, platform, BTRIM(external_account_id)
HAVING workspace_id IS NULL OR COUNT(*) > 1;


\echo '8) Instagram rows eligible for safe backfill'

SELECT
    sa.id,
    sa.workspace_id,
    sa.platform,
    sa.username,
    sa.connection_status,
    sa.is_active,
    im.external_account_id AS proposed_external_account_id
FROM public.social_accounts sa
JOIN (
    SELECT
        instagram_social_account_id,
        MIN(BTRIM(instagram_account_id)) AS external_account_id
    FROM public.facebook_instagram_links
    GROUP BY instagram_social_account_id
    HAVING instagram_social_account_id IS NOT NULL
       AND COUNT(DISTINCT BTRIM(instagram_account_id)) = 1
       AND COUNT(*) = COUNT(
           NULLIF(BTRIM(instagram_account_id), '')
       )
) im ON im.instagram_social_account_id = sa.id
WHERE LOWER(COALESCE(sa.platform, '')) = 'instagram'
  AND LOWER(COALESCE(sa.connection_status, '')) = 'linked'
  AND NULLIF(BTRIM(sa.external_account_id), '') IS NULL
  AND sa.workspace_id IS NOT NULL
ORDER BY sa.workspace_id, sa.created_at, sa.id;


\echo '9) Existing populated identities without workspace'

SELECT id, platform, username
FROM public.social_accounts
WHERE NULLIF(BTRIM(external_account_id), '') IS NOT NULL
  AND workspace_id IS NULL;


\echo '10) Existing normalized identity duplicates'

SELECT
    workspace_id,
    LOWER(platform) AS platform,
    BTRIM(external_account_id) AS external_account_id,
    COUNT(*) AS account_rows,
    ARRAY_AGG(id::text ORDER BY id) AS account_ids
FROM public.social_accounts
WHERE NULLIF(BTRIM(external_account_id), '') IS NOT NULL
GROUP BY
    workspace_id,
    LOWER(platform),
    BTRIM(external_account_id)
HAVING workspace_id IS NULL OR COUNT(*) > 1;


\echo '11) Facebook rows without official external IDs (informational only)'

SELECT id, workspace_id, username, connection_status
FROM public.social_accounts
WHERE LOWER(COALESCE(platform, '')) = 'facebook'
  AND NULLIF(BTRIM(external_account_id), '') IS NULL
ORDER BY workspace_id, id;


\echo '12) Candidate counts for safe Instagram backfill'

SELECT COUNT(*) AS linked_instagram_candidates
FROM public.social_accounts sa
JOIN (
    SELECT
        instagram_social_account_id,
        MIN(BTRIM(instagram_account_id)) AS external_account_id
    FROM public.facebook_instagram_links
    WHERE instagram_social_account_id IS NOT NULL
    GROUP BY instagram_social_account_id
    HAVING COUNT(DISTINCT BTRIM(instagram_account_id)) = 1
       AND COUNT(*) = COUNT(
           NULLIF(BTRIM(instagram_account_id), '')
       )
) im ON im.instagram_social_account_id = sa.id
WHERE LOWER(COALESCE(sa.platform, '')) = 'instagram'
  AND LOWER(COALESCE(sa.connection_status, '')) = 'linked'
  AND NULLIF(BTRIM(sa.external_account_id), '') IS NULL
  AND sa.workspace_id IS NOT NULL;

\echo 'READ-ONLY PREFLIGHT COMPLETED. Review all result sets before migration.'