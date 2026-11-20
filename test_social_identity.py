import unittest

from social_identity import (
    SocialIdentityConflict,
    link_discovered_social_account,
    upsert_social_account,
)


class FakeCursor:
    def __init__(
        self,
        external_rows=None,
        username_rows=None,
        inserted_id="new-id",
        update_rowcount=1,
    ):
        self.external_rows = list(external_rows or [])
        self.username_rows = list(username_rows or [])
        self.inserted_id = inserted_id
        self.update_rowcount = update_rowcount
        self.calls = []
        self.rowcount = -1
        self._one = None
        self._all = []

    def execute(self, query, params=None):
        sql = " ".join(query.split()).lower()
        self.calls.append((sql, params))
        self._one = None
        self._all = []
        self.rowcount = -1

        if sql.startswith("select pg_advisory_xact_lock"):
            return
        if sql.startswith("select id, username"):
            self._all = list(self.external_rows)
            return
        if sql.startswith("select id, external_account_id"):
            self._all = list(self.username_rows)
            return
        if sql.startswith("insert into social_accounts"):
            self._one = (self.inserted_id,)
            self.rowcount = 1
            return
        if sql.startswith("update social_accounts"):
            self.rowcount = self.update_rowcount
            return
        raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchall(self):
        return list(self._all)

    def fetchone(self):
        return self._one


class SocialIdentityTests(unittest.TestCase):
    def oauth_args(self, **overrides):
        args = {
            "workspace_id": "workspace-1",
            "platform": "Instagram",
            "external_account_id": "17841400000000000",
            "username": "brand",
            "display_name": "Brand",
            "encrypted_token": "encrypted-token",
        }
        args.update(overrides)
        return args

    def discovery_args(self, **overrides):
        args = {
            "workspace_id": "workspace-1",
            "platform": "Instagram",
            "external_account_id": "17841401520086875",
            "username": "brand",
            "display_name": "Brand",
        }
        args.update(overrides)
        return args

    def sql_calls(self, cur, prefix):
        return [sql for sql, _ in cur.calls if sql.startswith(prefix)]

    def test_oauth_inserts_when_no_external_or_legacy_match(self):
        cur = FakeCursor(inserted_id="created")
        self.assertEqual(upsert_social_account(cur, **self.oauth_args()), "created")
        insert_sql = self.sql_calls(cur, "insert into social_accounts")[0]
        self.assertEqual(len(self.sql_calls(cur, "insert into social_accounts")), 1)
        self.assertIn("connected', true, null)", insert_sql)
        self.assertIn("last_sync_at", insert_sql)

    def test_oauth_reconnect_preserves_previous_sync_timestamp(self):
        cur = FakeCursor(external_rows=[("existing-id", "brand")])
        upsert_social_account(cur, **self.oauth_args())
        update_sql = self.sql_calls(cur, "update social_accounts")[0]
        self.assertNotIn("last_sync_at =", update_sql)

    def test_oauth_prefers_exact_external_id_even_with_duplicate_usernames(self):
        cur = FakeCursor(
            external_rows=[("exact-id", "brand")],
            username_rows=[
                ("old-account", None),
                ("other-history", "different-external"),
            ],
        )
        result = upsert_social_account(cur, **self.oauth_args())
        self.assertEqual(result, "exact-id")
        update_params = next(
            params for sql, params in cur.calls
            if sql.startswith("update social_accounts")
        )
        self.assertEqual(update_params[4], "exact-id")

    def test_oauth_adopts_one_legacy_username_row_without_external_id(self):
        cur = FakeCursor(username_rows=[("legacy-id", None)])
        self.assertEqual(upsert_social_account(cur, **self.oauth_args()), "legacy-id")

    def test_oauth_rejects_multiple_username_matches_without_exact_external_id(self):
        cur = FakeCursor(
            username_rows=[("row-1", None), ("row-2", None)]
        )
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.oauth_args())

    def test_oauth_rejects_username_row_with_different_external_id(self):
        cur = FakeCursor(username_rows=[("row-1", "old-external-id")])
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.oauth_args())

    def test_oauth_rejects_duplicate_external_id_rows(self):
        cur = FakeCursor(
            external_rows=[("row-1", "brand"), ("row-2", "brand")]
        )
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.oauth_args())

    def test_oauth_allows_username_change_when_external_id_matches(self):
        cur = FakeCursor(external_rows=[("row-1", "old-brand")])
        self.assertEqual(upsert_social_account(cur, **self.oauth_args()), "row-1")
        update_params = next(
            params for sql, params in cur.calls
            if sql.startswith("update social_accounts")
        )
        self.assertEqual(update_params[0], "brand")
        self.assertEqual(update_params[4], "row-1")

    def test_oauth_does_not_deactivate_or_delete_historical_rows(self):
        cur = FakeCursor(external_rows=[("exact-id", "brand")])
        upsert_social_account(cur, **self.oauth_args())
        self.assertEqual(self.sql_calls(cur, "delete from social_accounts"), [])
        updates = self.sql_calls(cur, "update social_accounts")
        self.assertEqual(len(updates), 1)
        self.assertNotIn("set is_active = false", updates[0])

    def test_oauth_fails_if_resolved_row_update_did_not_apply(self):
        cur = FakeCursor(
            external_rows=[("exact-id", "brand")],
            update_rowcount=0,
        )
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.oauth_args())

    def test_discovery_reuses_exact_external_id_despite_duplicate_usernames(self):
        cur = FakeCursor(
            external_rows=[("exact-id", "brand")],
            username_rows=[("old-1", None), ("old-2", None)],
        )
        self.assertEqual(
            link_discovered_social_account(cur, **self.discovery_args()),
            "exact-id",
        )
        self.assertEqual(len(self.sql_calls(cur, "insert into social_accounts")), 0)

    def test_discovery_creates_passive_row_instead_of_guessing_by_username(self):
        cur = FakeCursor(
            username_rows=[("legacy-1", None), ("legacy-2", None)],
            inserted_id="passive-id",
        )
        self.assertEqual(
            link_discovered_social_account(cur, **self.discovery_args()),
            "passive-id",
        )
        insert_sql, insert_params = next(
            call for call in cur.calls if call[0].startswith("insert into social_accounts")
        )
        self.assertIn("null, 'linked', false, null", insert_sql)
        self.assertIn("last_sync_at", insert_sql)
        self.assertEqual(insert_params[-1], "17841401520086875")

    def test_discovery_rejects_duplicate_external_ids(self):
        cur = FakeCursor(
            external_rows=[("row-1", "brand"), ("row-2", "brand")]
        )
        with self.assertRaises(SocialIdentityConflict):
            link_discovered_social_account(cur, **self.discovery_args())

    def test_discovery_does_not_conflict_on_username_change_for_same_external_id(self):
        cur = FakeCursor(external_rows=[("row-1", "old-brand")])
        self.assertEqual(
            link_discovered_social_account(cur, **self.discovery_args()), "row-1"
        )
        # Discovery only fills blank labels; it preserves existing labels and state.
        update_params = next(
            params for sql, params in cur.calls
            if sql.startswith("update social_accounts")
        )
        self.assertEqual(update_params[0], "brand")
        self.assertEqual(update_params[2], "row-1")
        discovery_update = next(
            sql for sql in self.sql_calls(cur, "update social_accounts")
        )
        self.assertIn("else username end", discovery_update)

    def test_discovery_preserves_oauth_credentials_and_state(self):
        cur = FakeCursor(external_rows=[("oauth-id", "brand")])
        link_discovered_social_account(
            cur, **self.discovery_args(external_account_id="existing-external")
        )
        update = next(
            sql for sql in self.sql_calls(cur, "update social_accounts")
        )
        self.assertNotIn("access_token", update)
        self.assertNotIn("connection_status =", update)
        self.assertNotIn("is_active =", update)
        self.assertNotIn("external_account_id =", update)

    def test_missing_required_identity_is_rejected(self):
        for field in ("workspace_id", "platform", "external_account_id", "username"):
            with self.subTest(field=field):
                args = self.oauth_args(**{field: ""})
                with self.assertRaises(ValueError):
                    upsert_social_account(FakeCursor(), **args)

    def test_advisory_locks_are_taken_in_consistent_order(self):
        cur = FakeCursor()
        upsert_social_account(cur, **self.oauth_args())
        lock_params = [
            params[0] for sql, params in cur.calls
            if sql.startswith("select pg_advisory_xact_lock")
        ]
        self.assertEqual(len(lock_params), 2)
        self.assertTrue(lock_params[0].startswith("social-username:"))
        self.assertTrue(lock_params[1].startswith("social-account:"))


if __name__ == "__main__":
    unittest.main()
