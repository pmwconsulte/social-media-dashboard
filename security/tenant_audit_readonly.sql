-- PMW Social Media Dashboard AI
-- READ-ONLY tenant ownership audit. Safe to run against production.
-- No INSERT, UPDATE, DELETE, DDL, token reads, or secret values are selected.

BEGIN TRANSACTION READ ONLY;

-- 1. Workspace inventory and billing ownership.
SELECT id, slug, name, subscription_status, subscription_plan,
       CASE WHEN subscription_id IS NULL THEN 'no_subscription' ELSE 'subscription_assigned' END AS subscription_binding
FROM workspaces
ORDER BY slug;

-- 2. Active/inactive users and role distribution by workspace.
SELECT w.slug, COALESCE(u.role, 'NO_USER') AS role,
       COALESCE(u.is_active, TRUE) AS is_active,
       COUNT(*) AS user_count
FROM workspaces w
LEFT JOIN users u ON u.workspace_id = w.id
GROUP BY w.slug, COALESCE(u.role, 'NO_USER'), COALESCE(u.is_active, TRUE)
ORDER BY w.slug, role, is_active;

-- 3. Social accounts by workspace and platform.
SELECT w.slug, LOWER(sa.platform) AS platform,
       COALESCE(sa.connection_status, 'unknown') AS connection_status,
       COALESCE(sa.is_active, TRUE) AS is_active,
       COUNT(*) AS account_count
FROM workspaces w
LEFT JOIN social_accounts sa ON sa.workspace_id = w.id
GROUP BY w.slug, LOWER(sa.platform),
         COALESCE(sa.connection_status, 'unknown'), COALESCE(sa.is_active, TRUE)
ORDER BY w.slug, platform, connection_status, is_active;

-- 4. Facebook Page IDs associated with more than one workspace.
-- Messenger intentionally refuses to route ambiguous Page ownership.
SELECT sa.username AS facebook_page_id,
       COUNT(DISTINCT sa.workspace_id) AS workspace_count,
       ARRAY_AGG(DISTINCT w.slug ORDER BY w.slug) AS workspaces
FROM social_accounts sa
JOIN workspaces w ON w.id = sa.workspace_id
WHERE LOWER(sa.platform) = 'facebook'
  AND COALESCE(sa.is_active, TRUE) = TRUE
GROUP BY sa.username
HAVING COUNT(DISTINCT sa.workspace_id) > 1
ORDER BY sa.username;

-- 5. WhatsApp phone-number ownership mapping.
SELECT p.phone_number_id, w.slug AS workspace_slug, w.name AS workspace_name
FROM whatsapp_phone_number_workspaces p
JOIN workspaces w ON w.id = p.workspace_id
ORDER BY w.slug, p.phone_number_id;

-- 6. Messenger message distribution. No message content or recipient IDs.
SELECT w.slug, m.direction, m.status, COUNT(*) AS message_count
FROM facebook_messenger_messages m
JOIN workspaces w ON w.id = m.workspace_id
GROUP BY w.slug, m.direction, m.status
ORDER BY w.slug, m.direction, m.status;

-- 7. WhatsApp message distribution. No message content or phone numbers.
SELECT w.slug, m.direction, m.status, COUNT(*) AS message_count
FROM whatsapp_messages m
JOIN workspaces w ON w.id = m.workspace_id
GROUP BY w.slug, m.direction, m.status
ORDER BY w.slug, m.direction, m.status;

-- 8. Duplicate PayPal subscription IDs across workspaces.
-- Expected result: zero rows.
SELECT subscription_id, COUNT(*) AS workspace_count,
       ARRAY_AGG(slug ORDER BY slug) AS workspaces
FROM workspaces
WHERE subscription_id IS NOT NULL
GROUP BY subscription_id
HAVING COUNT(*) > 1
ORDER BY subscription_id;

-- 9. Null workspace ownership still present.
-- Note: rows already backfilled to pmw-default cannot be distinguished here
-- from records that genuinely belonged to that workspace before backfill.
SELECT 'users' AS table_name, COUNT(*) AS rows_without_workspace
FROM users WHERE workspace_id IS NULL
UNION ALL
SELECT 'social_accounts', COUNT(*)
FROM social_accounts WHERE workspace_id IS NULL;

COMMIT;
