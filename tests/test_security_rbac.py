import unittest
from security_rbac import (VALID_ROLES, has_permission, normalize_role,\n                           issue_csrf_token, valid_csrf_token)


class RolePermissionTests(unittest.TestCase):
    def test_unknown_roles_fail_closed_to_viewer(self):
        self.assertEqual(normalize_role("unexpected"), "viewer")
        self.assertEqual(normalize_role(None), "viewer")

    def test_valid_role_set_is_explicit(self):
        self.assertEqual(
            VALID_ROLES,
            {"platform_admin", "owner", "admin", "editor", "analyst", "viewer"},
        )

    def test_viewer_cannot_send_or_manage_integrations(self):
        self.assertFalse(has_permission("viewer", "messaging.reply"))
        self.assertFalse(has_permission("viewer", "integrations.manage"))
        self.assertFalse(has_permission("viewer", "accounts.manage"))

    def test_editor_can_reply_but_cannot_manage_integrations(self):
        self.assertTrue(has_permission("editor", "messaging.reply"))
        self.assertTrue(has_permission("editor", "publishing.create"))
        self.assertFalse(has_permission("editor", "integrations.manage"))

    def test_analyst_is_read_only(self):
        self.assertTrue(has_permission("analyst", "analytics.read"))
        self.assertTrue(has_permission("analyst", "reports.read"))
        self.assertFalse(has_permission("analyst", "messaging.reply"))
        self.assertFalse(has_permission("analyst", "publishing.create"))

    def test_owner_can_manage_own_workspace_capabilities(self):
        self.assertTrue(has_permission("owner", "users.manage"))
        self.assertTrue(has_permission("owner", "billing.manage"))
        self.assertTrue(has_permission("owner", "integrations.manage"))

    def test_platform_admin_has_explicit_audit_permission(self):
        self.assertTrue(has_permission("platform_admin", "security.audit"))

    def test_csrf_token_is_stable_for_session(self):
        session = {}
        first = issue_csrf_token(session)
        second = issue_csrf_token(session)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 32)

    def test_csrf_rejects_missing_and_wrong_token(self):
        session = {"csrf_token": "a" * 43}
        self.assertFalse(valid_csrf_token(session, ""))
        self.assertFalse(valid_csrf_token(session, "b" * 43))

    def test_csrf_accepts_existing_integration_tokens(self):
        session = {
            "whatsapp_csrf": "w" * 43,
            "facebook_messenger_csrf": "f" * 64,
            "instagram_inbox_csrf": "i" * 43,
        }
        self.assertTrue(valid_csrf_token(session, "w" * 43))
        self.assertTrue(valid_csrf_token(session, "f" * 64))
        self.assertTrue(valid_csrf_token(session, "i" * 43))

    def test_viewer_can_read_facebook_posts_but_not_publish(self):
        self.assertTrue(has_permission("viewer", "publishing.read"))
        self.assertFalse(has_permission("viewer", "publishing.create"))

    def test_analyst_can_read_facebook_posts_but_not_publish(self):
        self.assertTrue(has_permission("analyst", "publishing.read"))
        self.assertFalse(has_permission("analyst", "publishing.create"))


if __name__ == "__main__":
    unittest.main()
