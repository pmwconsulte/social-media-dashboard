import unittest

from social_identity import SocialIdentityConflict, upsert_social_account


class FakeCursor:
    def __init__(self, exact_rows=None, legacy_rows=None, inserted_id="new-id"):
        self.exact_rows = exact_rows or []
        self.legacy_rows = legacy_rows or []
        self.inserted_id = inserted_id
        self.calls = []
        self.rowcount = 1
        self._one = None
        self._all = []

    def execute(self, query, params=None):
        self.calls.append((" ".join(query.split()), params))
        normalized = " ".join(query.split()).lower()
        if "pg_advisory_xact_lock" in normalized:
            self._all = []
        elif "external_account_id=%s" in normalized and normalized.startswith("select id"):
            self._all = list(self.exact_rows)
        elif "lower(coalesce(username" in normalized:
            self._all = list(self.legacy_rows)
        elif normalized.startswith("insert into social_accounts"):
            self._one = (self.inserted_id,)
        else:
            self.rowcount = 1

    def fetchall(self):
        return self._all

    def fetchone(self):
        return self._one


class SocialIdentityTests(unittest.TestCase):
    def kwargs(self):
        return dict(
            workspace_id="workspace-1",
            platform="Instagram",
            external_account_id="17841400000000000",
            username="brand",
            display_name="brand",
            encrypted_token="encrypted-token",
        )

    def test_exact_external_id_reuses_existing_linked_identity(self):
        cur = FakeCursor(exact_rows=[("linked-id",)])
        result = upsert_social_account(cur, **self.kwargs())
        self.assertEqual(result, "linked-id")
        self.assertTrue(any("pg_advisory_xact_lock" in q for q, _ in cur.calls))
        self.assertTrue(any("connection_status='connected'" in q for q, _ in cur.calls))

    def test_unique_legacy_username_is_claimed_by_external_id(self):
        cur = FakeCursor(legacy_rows=[("legacy-id", None)])
        result = upsert_social_account(cur, **self.kwargs())
        self.assertEqual(result, "legacy-id")
        self.assertTrue(any("set external_account_id=%s" in q for q, _ in cur.calls))

    def test_ambiguous_legacy_username_fails_closed(self):
        cur = FakeCursor(legacy_rows=[("old-1", None), ("old-2", None)])
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())

    def test_conflicting_legacy_external_id_fails_closed(self):
        cur = FakeCursor(legacy_rows=[("old-1", "different-id")])
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())

    def test_new_identity_is_inserted_when_no_match_exists(self):
        cur = FakeCursor(inserted_id="created-id")
        result = upsert_social_account(cur, **self.kwargs())
        self.assertEqual(result, "created-id")
        self.assertTrue(any("insert into social_accounts" in q for q, _ in cur.calls))

    def test_missing_external_identity_is_rejected(self):
        cur = FakeCursor()
        values = self.kwargs()
        values["external_account_id"] = ""
        with self.assertRaises(ValueError):
            upsert_social_account(cur, **values)


if __name__ == "__main__":
    unittest.main()
