import unittest

from organization_access import (
    ORGANIZATION_ROLE_PERMISSIONS,
    SYSTEM_ORGANIZATION_ROLES,
    can_access_workspace_membership,
    has_organization_permission,
    normalize_organization_role,
    permissions_for_system_role,
    validate_custom_role_key,
)


class OrganizationRoleTests(unittest.TestCase):
    def test_protected_organization_roles_are_explicit(self):
        self.assertEqual(
            SYSTEM_ORGANIZATION_ROLES,
            {"owner", "admin", "billing_manager", "manager", "member"},
        )

    def test_unknown_organization_roles_fail_closed(self):
        self.assertIsNone(normalize_organization_role("superuser"))
        self.assertIsNone(normalize_organization_role(None))
        self.assertEqual(permissions_for_system_role("superuser"), frozenset())

    def test_owner_can_manage_organization_and_billing(self):
        self.assertTrue(has_organization_permission("owner", "organization.manage"))
        self.assertTrue(has_organization_permission("owner", "organization.billing.manage"))

    def test_admin_cannot_manage_billing(self):
        self.assertTrue(has_organization_permission("admin", "organization.members.manage"))
        self.assertFalse(has_organization_permission("admin", "organization.billing.manage"))

    def test_billing_manager_is_limited_to_billing(self):
        self.assertTrue(has_organization_permission("billing_manager", "organization.billing.manage"))
        self.assertFalse(has_organization_permission("billing_manager", "organization.members.manage"))
        self.assertFalse(has_organization_permission("billing_manager", "workspace.manage"))

    def test_member_has_read_only_organization_scope(self):
        self.assertTrue(has_organization_permission("member", "organization.read"))
        self.assertTrue(has_organization_permission("member", "workspace.list"))
        self.assertFalse(has_organization_permission("member", "workspace.manage"))

    def test_custom_permissions_must_be_explicitly_supplied(self):
        self.assertFalse(has_organization_permission("custom_analyst", "organization.read"))
        self.assertTrue(
            has_organization_permission(
                "custom_analyst",
                "analytics.read",
                custom_permissions={"analytics.read"},
            )
        )

    def test_custom_role_keys_cannot_override_protected_roles(self):
        for role in SYSTEM_ORGANIZATION_ROLES:
            self.assertFalse(validate_custom_role_key(role))
        self.assertTrue(validate_custom_role_key("regional_auditor"))
        self.assertFalse(validate_custom_role_key("Bad Role"))
        self.assertFalse(validate_custom_role_key("x"))
        self.assertFalse(validate_custom_role_key(None))

    def test_workspace_access_requires_both_active_memberships(self):
        args = {
            "organization_membership_active": True,
            "workspace_membership_active": True,
            "workspace_role": "viewer",
            "permission": "analytics.read",
        }
        self.assertTrue(can_access_workspace_membership(**args))
        self.assertFalse(can_access_workspace_membership(
            **{**args, "organization_membership_active": False}
        ))
        self.assertFalse(can_access_workspace_membership(
            **{**args, "workspace_membership_active": False}
        ))

    def test_workspace_permissions_remain_separate_from_org_roles(self):
        self.assertIn("billing_manager", SYSTEM_ORGANIZATION_ROLES)
        self.assertNotIn("billing_manager", __import__("security_rbac").VALID_ROLES)


class _FakeCursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, row):
        self.fake_cursor = _FakeCursor(row)

    def cursor(self):
        return self.fake_cursor


class OrganizationAccessQueryTests(unittest.TestCase):
    def test_missing_organization_membership_denies_permission(self):
        from organization_access import user_has_organization_permission
        conn = _FakeConnection(None)
        self.assertFalse(
            user_has_organization_permission(conn, "user-id", "org-id", "organization.read")
        )

    def test_system_organization_role_uses_protected_permissions(self):
        from organization_access import user_has_organization_permission
        conn = _FakeConnection(("billing_manager", []))
        self.assertTrue(
            user_has_organization_permission(
                conn, "user-id", "org-id", "organization.billing.manage"
            )
        )
        self.assertFalse(
            user_has_organization_permission(
                conn, "user-id", "org-id", "organization.members.manage"
            )
        )

    def test_custom_role_permissions_are_loaded_from_database(self):
        from organization_access import user_has_organization_permission
        conn = _FakeConnection(("custom_auditor", ["security.audit"]))
        self.assertTrue(
            user_has_organization_permission(
                conn, "user-id", "org-id", "security.audit"
            )
        )
        self.assertFalse(
            user_has_organization_permission(
                conn, "user-id", "org-id", "organization.billing.manage"
            )
        )

    def test_workspace_access_requires_explicit_dual_membership(self):
        from organization_access import user_has_workspace_permission
        conn = _FakeConnection(None)
        self.assertFalse(
            user_has_workspace_permission(
                conn, "user-id", "workspace-id", "analytics.read"
            )
        )

    def test_workspace_access_checks_organization_workspace_and_legacy_roles(self):
        from organization_access import user_has_workspace_permission
        conn = _FakeConnection((
            "org-id", True, True, "viewer", "member", []
        ))
        self.assertTrue(
            user_has_workspace_permission(
                conn, "user-id", "workspace-id", "analytics.read"
            )
        )
        self.assertFalse(
            user_has_workspace_permission(
                conn, "user-id", "workspace-id", "accounts.manage"
            )
        )

    def test_workspace_access_requires_organization_workspace_list_permission(self):
        from organization_access import user_has_workspace_permission
        conn = _FakeConnection((
            "org-id", True, True, "owner", "custom_restricted", []
        ))
        self.assertFalse(
            user_has_workspace_permission(
                conn, "user-id", "workspace-id", "analytics.read"
            )
        )


if __name__ == "__main__":
    unittest.main()
