import unittest
from security_rbac import VALID_ROLES, has_permission, normalize_role


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


if __name__ == "__main__":
    unittest.main()
