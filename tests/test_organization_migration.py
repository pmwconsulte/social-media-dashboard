"""Contract tests for the first multi-organization SQL migration.

These tests inspect migration contents only. They do not connect to PostgreSQL
or execute DDL against any environment.
"""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261126_001_organization_foundation.sql"
ROLLBACK = (
    ROOT
    / "migrations"
    / "rollback"
    / "20261126_001_organization_foundation.rollback.sql"
)


class OrganizationMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()
        cls.rollback_sql = ROLLBACK.read_text(encoding="utf-8").lower()

    def test_migration_is_transactional(self):
        self.assertIn("begin;", self.sql)
        self.assertIn("commit;", self.sql)

    def test_organizations_table_has_uuid_identity_and_unique_slug(self):
        self.assertIn("create table if not exists organizations", self.sql)
        self.assertIn("id uuid primary key", self.sql)
        self.assertIn("slug varchar(255) not null unique", self.sql)

    def test_subscription_metadata_is_organization_scoped(self):
        for column in (
            "subscription_status",
            "subscription_plan",
            "payment_provider",
            "subscription_id",
            "trial_started_at",
            "trial_ends_at",
        ):
            self.assertIn(column, self.sql)

    def test_workspace_assignment_is_nullable_and_non_destructive(self):
        self.assertIn("add column if not exists organization_id uuid;", self.sql)
        self.assertNotIn("organization_id uuid not null", self.sql)
        self.assertNotIn("update workspaces", self.sql)
        self.assertNotIn("delete from workspaces", self.sql)
        self.assertNotIn("drop table workspaces", self.sql)

    def test_workspace_foreign_key_prevents_organization_deletion(self):
        self.assertIn("references organizations(id)", self.sql)
        self.assertIn("on delete restrict", self.sql)

    def test_migration_does_not_move_existing_billing_or_memberships(self):
        self.assertNotIn("update organizations", self.sql)
        self.assertNotIn("insert into organizations", self.sql)
        self.assertNotIn("update workspaces", self.sql)
        self.assertNotIn("update users", self.sql)

    def test_rollback_removes_only_the_new_foundation(self):
        self.assertIn("drop column if exists organization_id", self.rollback_sql)
        self.assertIn("drop table if exists organizations", self.rollback_sql)
        self.assertNotIn("drop table workspaces", self.rollback_sql)
        self.assertNotIn("delete from users", self.rollback_sql)
        self.assertNotIn("delete from social_accounts", self.rollback_sql)


if __name__ == "__main__":
    unittest.main()
