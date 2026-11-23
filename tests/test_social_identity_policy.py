
import unittest

from social_identity import (
    SocialIdentityConflict,
    upsert_social_account,
    link_discovered_social_account,
)


class FakeCursor:
    def __init__(
        self,
        username_rows=None,
        external_rows=None,
        inserted_id="new-id",
    ):
        self.username_rows = username_rows or []
        self.external_rows = external_rows or []
        self.inserted_id = inserted_id
        self.calls = []
        self.rowcount = 1
        self._one = None
        self._all = []

    def execute(self, query, params=None):
        normalized = " ".join(query.split()).lower()
        self.calls.append((normalized, params))

        if normalized.startswith("select id, external_account_id"):
            self._all = list(self.username_rows)

        elif normalized.startswith("select id, username"):
            self._all = list(self.external_rows)

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
        return {
            "workspace_id": "workspace-1",
            "platform": "Instagram",
            "external_account_id": "17841400000000000",
            "username": "brand",
            "display_name": "Brand",
            "encrypted_token": "encrypted-token",
        }

    def discovery_kwargs(self):
        return {
            "workspace_id": "workspace-1",
            "platform": "Instagram",
            "external_account_id": "17841401520086875",
            "username": "brand",
            "display_name": "Brand",
        }

    # DIRECT OAUTH

    def test_direct_oauth_inserts_new_identity(self):
        cur = FakeCursor(inserted_id="created-id")

        result = upsert_social_account(cur, **self.kwargs())

        self.assertEqual(result, "created-id")
        self.assertTrue(
            any(q.startswith("insert into social_accounts") for q, _ in cur.calls)
        )

    def test_direct_oauth_rejects_duplicate_external_ids(self):
        cur = FakeCursor(
            external_rows=[
                ("id-1", "brand"),
                ("id-2", "brand"),
            ]
        )

        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())

    def test_direct_oauth_reuses_exact_external_identity(self):
        cur = FakeCursor(
            external_rows=[("direct-id", "old-brand")]
        )
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())
        writes = [q.lower() for q, _ in cur.calls if q.lower().startswith((
            "insert into social_accounts", "update social_accounts",
            "delete from social_accounts"
        ))]
        self.assertEqual(writes, [])
    def test_direct_oauth_allows_username_change_for_same_external_id(self):
        cur = FakeCursor(
            external_rows=[("direct-id", "old-brand")]
        )
        values = self.kwargs()
        values["username"] = "new-brand"
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **values)
        writes = [q.lower() for q, _ in cur.calls if q.lower().startswith((
            "insert into social_accounts", "update social_accounts",
            "delete from social_accounts"
        ))]
        self.assertEqual(writes, [])
    def test_direct_oauth_rejects_username_owned_by_different_external_id(self):
        cur = FakeCursor(
            username_rows=[
                ("existing-id", "different-external-id")
            ]
        )

        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())

    def test_direct_oauth_rejects_ambiguous_legacy_username(self):
        cur = FakeCursor(
            username_rows=[
                ("old-1", None),
                ("old-2", None),
            ]
        )

        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())

    def test_direct_oauth_adopts_unique_legacy_without_external_id(self):
        cur = FakeCursor(username_rows=[('legacy-id', None)])
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())
        writes = [q.lower() for q, _ in cur.calls if q.lower().startswith((
            "insert into social_accounts", "update social_accounts",
            "delete from social_accounts"
        ))]
        self.assertEqual(writes, [])
    def test_direct_oauth_rejects_missing_external_identity(self):
        cur = FakeCursor()
        values = self.kwargs()
        values["external_account_id"] = ""

        with self.assertRaises(ValueError):
            upsert_social_account(cur, **values)

    def test_direct_oauth_does_not_deactivate_historical_accounts(self):
        cur = FakeCursor(
            external_rows=[("direct-id", "brand")]
        )
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())
        writes = [q.lower() for q, _ in cur.calls if q.lower().startswith((
            "insert into social_accounts", "update social_accounts",
            "delete from social_accounts"
        ))]
        self.assertEqual(writes, [])
    # DISCOVERY

    def test_discovery_creates_passive_identity_when_missing(self):
        cur = FakeCursor(inserted_id="passive-id")

        result = link_discovered_social_account(
            cur, **self.discovery_kwargs()
        )

        self.assertEqual(result, "passive-id")
        insert_calls = [
            (q, params) for q, params in cur.calls
            if q.startswith("insert into social_accounts")
        ]
        self.assertEqual(len(insert_calls), 1)
        self.assertIn("'linked', false", insert_calls[0][0])

    def test_discovery_rejects_duplicate_external_ids(self):
        cur = FakeCursor(
            external_rows=[
                ("id-1", "brand"),
                ("id-2", "brand"),
            ]
        )

        with self.assertRaises(SocialIdentityConflict):
            link_discovered_social_account(
                cur, **self.discovery_kwargs()
            )

    def test_discovery_reuses_exact_external_identity(self):
        cur = FakeCursor(
            external_rows=[
                ("direct-id", "brand")
            ]
        )

        result = link_discovered_social_account(
            cur, **self.discovery_kwargs()
        )

        self.assertEqual(result, "direct-id")

    def test_discovery_does_not_use_username_as_identity(self):
        cur = FakeCursor(
            username_rows=[
                ("existing-id", "different-external-id")
            ]
        )

        result = link_discovered_social_account(
            cur, **self.discovery_kwargs()
        )

        self.assertEqual(result, "new-id")

        username_queries = [
            q for q, _ in cur.calls
            if q.startswith("select id, external_account_id")
        ]
        self.assertEqual(username_queries, [])

    def test_discovery_preserves_direct_oauth_credentials_and_state(self):
        cur = FakeCursor(
            external_rows=[
                ("direct-id", "brand")
            ]
        )

        result = link_discovered_social_account(
            cur, **self.discovery_kwargs()
        )

        self.assertEqual(result, "direct-id")

        modifying_calls = [
            q for q, _ in cur.calls
            if q.startswith("update social_accounts")
            or q.startswith("insert into social_accounts")
        ]

        self.assertEqual(len(modifying_calls), 1)
        self.assertIn("set username = case", modifying_calls[0])
        self.assertNotIn("access_token =", modifying_calls[0])
        self.assertNotIn("connection_status =", modifying_calls[0])
        self.assertNotIn("is_active =", modifying_calls[0])


if __name__ == "__main__":
    unittest.main()
