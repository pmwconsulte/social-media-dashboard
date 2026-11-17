
-- ============================================================
-- PMW SOCIAL IDENTITY V2
-- Manual, one-time migration.
-- Run the READ-ONLY preflight before this migration.
-- Never execute automatically during application startup.
--
-- SAFETY GUARANTEES:
-- 1. No social accounts or historical metrics are deleted.
-- 2. OAuth access tokens are never modified.
-- 3. Direct Instagram OAuth identities (connected) are protected.
-- 4. Facebook external IDs are never inferred from usernames.
-- 5. Only validated linked Instagram identities are backfilled.
-- 6. All changes are transactional.
--
-- Execute:
-- psql -v ON_ERROR_STOP=1 -f migrations/20261115_social_external_identity.sql
-- ============================================================

\set ON_ERROR_STOP on

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '2min';

-- ============================================================
-- 1. Validate required tables
-- ============================================================

DO $$
BEGIN
    IF to_regclass('public.social_accounts') IS NULL THEN
        RAISE EXCEPTION
            'Migration blocked: public.social_accounts does not exist';
    END IF;

    IF to_regclass('public.facebook_instagram_links') IS NULL THEN
        RAISE EXCEPTION
            'Migration blocked: public.facebook_instagram_links does not exist';
    END IF;
END
$$;

-- Prevent concurrent modifications while validating and
-- applying the identity backfill and unique index.

LOCK TABLE public.social_accounts,
           public.facebook_instagram_links
IN SHARE ROW EXCLUSIVE MODE;

-- ============================================================
-- 2. Validate required columns
-- ============================================================

DO $$
DECLARE
    v_table TEXT;
    v_column TEXT;
BEGIN
    FOR v_table, v_column IN
        SELECT *
        FROM (VALUES
            ('social_accounts', 'id'),
            ('social_accounts', 'workspace_id'),
            ('social_accounts', 'platform'),
            ('social_accounts', 'connection_status'),
            ('facebook_instagram_links', 'social_account_id'),
            ('facebook_instagram_links', 'page_id'),
            ('facebook_instagram_links', 'instagram_account_id'),
            ('facebook_instagram_links', 'instagram_social_account_id')
        ) AS required_columns(table_name, column_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = v_table
              AND column_name = v_column
        ) THEN
            RAISE EXCEPTION
                'Migration blocked: required column %.% does not exist',
                v_table, v_column;
        END IF;
    END LOOP;
END
$$;

-- ============================================================
-- 3. Ensure external_account_id exists with correct type
-- ============================================================

ALTER TABLE public.social_accounts
    ADD COLUMN IF NOT EXISTS external_account_id VARCHAR(255);

DO $$
DECLARE
    v_data_type TEXT;
    v_max_length INTEGER;
BEGIN
    SELECT data_type, character_maximum_length
    INTO v_data_type, v_max_length
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'social_accounts'
      AND column_name = 'external_account_id';

    IF v_data_type IS DISTINCT FROM 'character varying'
       OR v_max_length IS DISTINCT FROM 255 THEN
        RAISE EXCEPTION
            'Migration blocked: external_account_id must be VARCHAR(255). Found type %, length %',
            v_data_type, v_max_length;
    END IF;
END
$$;

-- ============================================================
-- 4. Validate every Facebook-to-Instagram mapping
-- ============================================================

DO $$
BEGIN
    -- Every mapping must contain both internal identities
    -- and official external identifiers.

    IF EXISTS (
        SELECT 1
        FROM public.facebook_instagram_links
        WHERE NULLIF(BTRIM(social_account_id), '') IS NULL
           OR NULLIF(BTRIM(page_id), '') IS NULL
           OR instagram_social_account_id IS NULL
           OR NULLIF(BTRIM(instagram_account_id), '') IS NULL
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: Facebook/Instagram mappings contain missing identifiers';
    END IF;

    -- One internal Instagram account cannot map to
    -- multiple distinct official Instagram IDs.

    IF EXISTS (
        SELECT instagram_social_account_id
        FROM public.facebook_instagram_links
        GROUP BY instagram_social_account_id
        HAVING COUNT(DISTINCT BTRIM(instagram_account_id)) <> 1
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: an Instagram internal account maps to multiple external IDs';
    END IF;

    -- Both internal accounts must exist, have the correct
    -- platforms and belong to the same workspace.

    IF EXISTS (
        SELECT 1
        FROM public.facebook_instagram_links fil
        LEFT JOIN public.social_accounts fb
               ON fb.id::text = fil.social_account_id
        LEFT JOIN public.social_accounts ig
               ON ig.id = fil.instagram_social_account_id
        WHERE fb.id IS NULL
           OR ig.id IS NULL
           OR LOWER(COALESCE(fb.platform, '')) <> 'facebook'
           OR LOWER(COALESCE(ig.platform, '')) <> 'instagram'
           OR fb.workspace_id IS NULL
           OR ig.workspace_id IS NULL
           OR fb.workspace_id IS DISTINCT FROM ig.workspace_id
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: invalid Facebook/Instagram account relationship or workspace mismatch';
    END IF;
END
$$;

-- ============================================================
-- 5. Protect direct Instagram OAuth identities
-- ============================================================
--
-- Connected Instagram accounts may have been authenticated
-- directly through Instagram OAuth.
--
-- Facebook discovery must never overwrite their identity.
--
-- Existing non-connected Instagram identities must agree
-- with their official Facebook mapping.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.facebook_instagram_links fil
        JOIN public.social_accounts ig
          ON ig.id = fil.instagram_social_account_id
        WHERE LOWER(COALESCE(ig.connection_status, ''))
                  <> 'connected'
          AND NULLIF(BTRIM(ig.external_account_id), '') IS NOT NULL
          AND BTRIM(ig.external_account_id)
                  <> BTRIM(fil.instagram_account_id)
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: a non-connected Instagram identity conflicts with its Facebook mapping';
    END IF;
END
$$;

-- ============================================================
-- 6. Backfill linked Instagram identities only
-- ============================================================
--
-- Only validated Facebook-discovered Instagram mappings
-- are eligible.
--
-- Connected Instagram OAuth identities are excluded.
-- Existing non-empty external IDs are preserved.
-- Facebook accounts are never modified here.

WITH instagram_mapping AS (
    SELECT
        instagram_social_account_id,
        MIN(BTRIM(instagram_account_id)) AS external_account_id
    FROM public.facebook_instagram_links
    GROUP BY instagram_social_account_id
    HAVING COUNT(DISTINCT BTRIM(instagram_account_id)) = 1
       AND COUNT(*) = COUNT(
           NULLIF(BTRIM(instagram_account_id), '')
       )
)
UPDATE public.social_accounts sa
SET external_account_id = mapping.external_account_id
FROM instagram_mapping mapping
WHERE sa.id = mapping.instagram_social_account_id
  AND LOWER(COALESCE(sa.platform, '')) = 'instagram'
  AND LOWER(COALESCE(sa.connection_status, '')) = 'linked'
  AND NULLIF(BTRIM(sa.external_account_id), '') IS NULL
  AND sa.workspace_id IS NOT NULL;

-- ============================================================
-- 7. Validate workspace ownership and identity uniqueness
-- ============================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.social_accounts
        WHERE NULLIF(BTRIM(external_account_id), '') IS NOT NULL
          AND workspace_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: populated external identities exist without workspace_id';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.social_accounts
        WHERE NULLIF(BTRIM(external_account_id), '') IS NOT NULL
        GROUP BY
            workspace_id,
            LOWER(platform),
            BTRIM(external_account_id)
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: duplicate normalized external identities exist within a workspace';
    END IF;
END
$$;

-- ============================================================
-- 8. Validate existing unique index or create it
-- ============================================================
--
-- Required keys:
-- workspace_id
-- LOWER(platform)
-- BTRIM(external_account_id)
--
-- Required partial predicate:
-- NULLIF(BTRIM(external_account_id), '') IS NOT NULL

DO $$
DECLARE
    v_index_oid OID;
    v_is_unique BOOLEAN;
    v_is_valid BOOLEAN;
    v_is_ready BOOLEAN;
    v_key_count INTEGER;
    v_total_count INTEGER;
    v_access_method TEXT;
    v_key_1 TEXT;
    v_key_2 TEXT;
    v_key_3 TEXT;
    v_predicate TEXT;
BEGIN
    SELECT
        idx.oid,
        i.indisunique,
        i.indisvalid,
        i.indisready,
        i.indnkeyatts,
        i.indnatts,
        am.amname,
        pg_get_indexdef(i.indexrelid, 1, TRUE),
        pg_get_indexdef(i.indexrelid, 2, TRUE),
        pg_get_indexdef(i.indexrelid, 3, TRUE),
        pg_get_expr(i.indpred, i.indrelid)
    INTO
        v_index_oid,
        v_is_unique,
        v_is_valid,
        v_is_ready,
        v_key_count,
        v_total_count,
        v_access_method,
        v_key_1,
        v_key_2,
        v_key_3,
        v_predicate
    FROM pg_class idx
    JOIN pg_namespace ns
      ON ns.oid = idx.relnamespace
    JOIN pg_index i
      ON i.indexrelid = idx.oid
    JOIN pg_class tbl
      ON tbl.oid = i.indrelid
    JOIN pg_am am
      ON am.oid = idx.relam
    WHERE ns.nspname = 'public'
      AND idx.relname = 'uq_social_accounts_external_identity';

    IF v_index_oid IS NOT NULL THEN

        IF NOT EXISTS (
            SELECT 1
            FROM pg_index i
            JOIN pg_class tbl
              ON tbl.oid = i.indrelid
            WHERE i.indexrelid = v_index_oid
              AND tbl.oid = 'public.social_accounts'::regclass
        ) THEN
            RAISE EXCEPTION
                'Migration blocked: reserved index name is used by another table';
        END IF;

        IF v_is_unique IS DISTINCT FROM TRUE
           OR v_is_valid IS DISTINCT FROM TRUE
           OR v_is_ready IS DISTINCT FROM TRUE
           OR v_key_count IS DISTINCT FROM 3
           OR v_total_count IS DISTINCT FROM 3
           OR v_access_method IS DISTINCT FROM 'btree'
           OR REGEXP_REPLACE(
                LOWER(COALESCE(v_key_1, '')),
                '\s+', '', 'g'
              ) <> 'workspace_id'
           OR REGEXP_REPLACE(
                LOWER(COALESCE(v_key_2, '')),
                '\s+', '', 'g'
              ) <> 'lower(platform::text)'
           OR REGEXP_REPLACE(
                LOWER(COALESCE(v_key_3, '')),
                '\s+', '', 'g'
              ) <> 'btrim(external_account_id::text)'
           OR REGEXP_REPLACE(
                LOWER(COALESCE(v_predicate, '')),
                '\s+', '', 'g'
              ) <> '(nullif(btrim((external_account_id)::text),''::text)isnotnull)'
        THEN
            RAISE EXCEPTION
                'Migration blocked: existing unique identity index does not match required definition';
        END IF;

        RAISE NOTICE
            'Existing normalized unique identity index validated successfully.';

    ELSE

        CREATE UNIQUE INDEX uq_social_accounts_external_identity
            ON public.social_accounts (
                workspace_id,
                LOWER(platform),
                BTRIM(external_account_id)
            )
            WHERE NULLIF(BTRIM(external_account_id), '') IS NOT NULL;

        RAISE NOTICE
            'Normalized unique identity index created successfully.';

    END IF;
END
$$;

-- ============================================================
-- 9. Final verification
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class idx
        JOIN pg_namespace ns
          ON ns.oid = idx.relnamespace
        JOIN pg_index i
          ON i.indexrelid = idx.oid
        WHERE ns.nspname = 'public'
          AND idx.relname = 'uq_social_accounts_external_identity'
          AND i.indisunique
          AND i.indisvalid
          AND i.indisready
          AND i.indnkeyatts = 3
          AND i.indnatts = 3
          AND i.indrelid = 'public.social_accounts'::regclass
    ) THEN
        RAISE EXCEPTION
            'Migration blocked: final unique identity index verification failed';
    END IF;
END
$$;

COMMIT;

\echo 'PMW Social Identity v2 migration completed successfully.'
