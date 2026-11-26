"""Contract tests for organization membership and RBAC schema."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261126_002_organization_memberships.sql"
ROLLBACK = (
    ROOT / "migrations" / "rollback"
    / "20261126_002_organization_memberships.rollback.sql"
)


class OrganizationMembershipMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()
        cls.rollback_sql = ROLLBACK.read_text(encoding="utf-8").lower()

    def test_migration_is_transactional_and_does_not_assign_existing_users(self):
        self.assertIn("begin;", self.sql)
        self.assertIn("commit;", self.sql)
        self.assertNotIn("insert into organization_memberships", self.sql)
        self.assertNotIn("update users", self.sql)
        self.assertNotIn("update workspaces", self.sql)

    def test_roles_and_permissions_are_organization_scoped(self):
        self.assertIn("create table if not exists organization_roles", self.sql)
        self.assertIn("unique (organization_id, role_key)", self.sql)
        self.assertIn("create table if not exists organization_role_permissions", self.sql)
        self.assertIn("unique (role_id, permission_key)", self.sql)

    def test_organization_membership_is_unique_per_user_and_org(self):
        self.assertIn("create table if not exists organization_memberships", self.sql)
        self.assertIn("unique (organization_id, user_id)", self.sql)
        self.assertIn("foreign key (role_id, organization_id)", self.sql)

    def test_workspace_membership_requires_same_organization(self):
        self.assertIn("create table if not exists workspace_memberships", self.sql)
        self.assertIn("foreign key (workspace_id, organization_id)", self.sql)
        self.assertIn("references workspaces(id, organization_id)", self.sql)
        self.assertIn("foreign key (organization_id, user_id)", self.sql)
        self.assertIn("references organization_memberships(organization_id, user_id)", self.sql)

    def test_legacy_user_roles_are_not_modified(self):
        self.assertNotIn("alter table users", self.sql)
        self.assertNotIn("update users", self.sql)

    def test_rollback_refuses_to_remove_populated_membership_schema(self):
        self.assertIn("if exists (select 1 from workspace_memberships)", self.rollback_sql)
        self.assertIn("rollback blocked", self.rollback_sql)
        self.assertIn("drop index if exists uq_workspaces_id_organization", self.rollback_sql)


if __name__ == "__main__":
    unittest.main()
