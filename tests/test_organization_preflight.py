"""Contract checks for the read-only organization migration preflight."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = (
    ROOT / "migrations" / "preflight"
    / "20261126_organization_migration_preflight.sql"
)
PRIMARY_OWNER_MIGRATION = (
    ROOT / "migrations" / "20261126_003_primary_organization_owner.sql"
)


class OrganizationPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = PREFLIGHT.read_text(encoding="utf-8").lower()
        cls.primary_owner_sql = PRIMARY_OWNER_MIGRATION.read_text(encoding="utf-8").lower()

    def test_preflight_is_read_only_and_rolls_back(self):
        self.assertIn("begin transaction read only;", self.sql)
        self.assertIn("rollback;", self.sql)
        for forbidden in ("insert into ", "update ", "delete from ", "alter table ", "create table "):
            self.assertNotIn(forbidden, self.sql)

    def test_preflight_does_not_select_credentials_or_message_bodies(self):
        self.assertNotIn("access_token", self.sql)
        self.assertNotIn("password_hash", self.sql)
        self.assertNotIn("message_body", self.sql)
        self.assertNotIn("message_text", self.sql)

    def test_preflight_checks_all_three_legacy_workspaces(self):
        for workspace_id in (
            "be43b2c9-4bbc-40a8-8f46-c67b53e2ace7",
            "746181b5-a798-4f0e-805e-b379d713ca9c",
            "9e28c16c-63af-458b-b403-4f09d34df8d9",
        ):
            self.assertIn(workspace_id, self.sql)

    def test_preflight_checks_both_existing_owner_ids(self):
        self.assertIn("96a8116f-2ebc-4aa9-b253-cc5a1fb49428", self.sql)
        self.assertIn("531620c9-05ea-4546-83bd-f174420ea871", self.sql)

    def test_primary_owner_marker_is_unique_per_organization(self):
        self.assertIn("is_primary_owner boolean not null default false", self.primary_owner_sql)
        self.assertIn("where is_primary_owner = true", self.primary_owner_sql)
        self.assertIn("create unique index", self.primary_owner_sql)


if __name__ == "__main__":
    unittest.main()
