-- PMW multi-organization migration preflight
-- READ-ONLY: run after migrations 001 and 002 are applied in a DEVELOPMENT
-- database. This script does not INSERT, UPDATE, DELETE, or execute DDL.
-- Review all result sets before preparing/applying the data migration.
--
-- Expected target:
-- Organization: PMW Consultoria (slug: pmw-consultoria)
-- Existing workspaces: all three retained as separate workspaces.
-- Primary organization owner candidate:
--   96a8116f-2ebc-4aa9-b253-cc5a1fb49428
-- Existing second Owner must retain access; no role downgrade is proposed.
--
-- Never run against production as part of this preparation step.

BEGIN TRANSACTION READ ONLY;

-- 1. Confirm required schema exists.
SELECT
    to_regclass('public.organizations') AS organizations_table,
    to_regclass('public.organization_roles') AS organization_roles_table,
    to_regclass('public.organization_role_permissions') AS role_permissions_table,
    to_regclass('public.organization_memberships') AS organization_memberships_table,
    to_regclass('public.workspace_memberships') AS workspace_memberships_table,
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'workspaces'
          AND column_name = 'organization_id'
    ) AS workspaces_has_organization_id;

-- 2. Inventory all workspaces without changing them.
SELECT
    id,
    name,
    slug,
    subscription_status,
    subscription_plan,
    payment_provider,
    CASE
        WHEN subscription_id IS NULL THEN FALSE
        ELSE TRUE
    END AS has_subscription_reference,
    organization_id
FROM workspaces
ORDER BY created_at, slug;

-- 3. Verify expected legacy workspace identities still exist.
WITH expected_workspaces(id, expected_slug) AS (
    VALUES
      ('be43b2c9-4bbc-40a8-8f46-c67b53e2ace7'::uuid, 'pmw-consultoria-98fdcf3f'),
      ('746181b5-a798-4f0e-805e-b379d713ca9c'::uuid, 'pmw-consult-02778e56'),
      ('9e28c16c-63af-458b-b403-4f09d34df8d9'::uuid, 'pmw-default')
)
SELECT
    e.expected_slug,
    e.id AS expected_workspace_id,
    w.slug AS actual_slug,
    w.id IS NOT NULL AS found,
    CASE WHEN w.id IS NULL THEN 'MISSING'
         WHEN w.slug <> e.expected_slug THEN 'SLUG_MISMATCH'
         ELSE 'OK'
    END AS result
FROM expected_workspaces e
LEFT JOIN workspaces w ON w.id = e.id
ORDER BY e.expected_slug;

-- 4. Verify the two existing Owner identities without exposing credentials.
WITH expected_owners(id, expected_email) AS (
    VALUES
      ('96a8116f-2ebc-4aa9-b253-cc5a1fb49428'::uuid, 'pmweng@gmail.com'),
      ('531620c9-05ea-4546-83bd-f174420ea871'::uuid, 'pmwconsulte@gmail.com')
)
SELECT
    e.id AS expected_user_id,
    u.id IS NOT NULL AS found,
    (lower(u.email) = lower(e.expected_email)) AS email_matches,
    u.role AS current_legacy_role,
    u.is_active,
    u.workspace_id,
    w.slug AS current_workspace_slug,
    CASE
        WHEN u.id IS NULL THEN 'MISSING'
        WHEN lower(u.email) <> lower(e.expected_email) THEN 'IDENTITY_MISMATCH'
        WHEN u.role <> 'owner' THEN 'ROLE_REVIEW_REQUIRED'
        WHEN u.is_active IS NOT TRUE THEN 'INACTIVE_REVIEW_REQUIRED'
        ELSE 'OK'
    END AS result
FROM expected_owners e
LEFT JOIN users u ON u.id = e.id
LEFT JOIN workspaces w ON w.id = u.workspace_id
ORDER BY e.id;

-- 5. Count records by workspace. No account tokens or message bodies are read.
SELECT
    w.id AS workspace_id,
    w.slug,
    (SELECT COUNT(*) FROM users u WHERE u.workspace_id = w.id) AS users_count,
    (SELECT COUNT(*) FROM social_accounts s WHERE s.workspace_id = w.id) AS social_accounts_count,
    (SELECT COUNT(*) FROM whatsapp_messages m WHERE m.workspace_id = w.id) AS whatsapp_messages_count,
    (SELECT COUNT(*) FROM facebook_messenger_messages f WHERE f.workspace_id = w.id) AS facebook_messenger_messages_count
FROM workspaces w
ORDER BY w.slug;

-- 6. Verify no existing workspace has already been assigned unexpectedly.
SELECT
    COUNT(*) AS total_workspaces,
    COUNT(*) FILTER (WHERE organization_id IS NULL) AS unassigned_workspaces,
    COUNT(*) FILTER (WHERE organization_id IS NOT NULL) AS already_assigned_workspaces
FROM workspaces;

-- 7. Confirm the existing PayPal Starter subscription remains attached to
-- exactly one workspace. The subscription reference itself is not displayed.
SELECT
    COUNT(*) FILTER (
        WHERE subscription_status = 'active'
          AND subscription_plan = 'starter'
          AND payment_provider = 'paypal'
          AND subscription_id IS NOT NULL
    ) AS active_paypal_starter_workspace_count,
    COUNT(DISTINCT subscription_id) FILTER (
        WHERE payment_provider = 'paypal'
          AND subscription_id IS NOT NULL
    ) AS distinct_paypal_subscription_count
FROM workspaces;

ROLLBACK;
