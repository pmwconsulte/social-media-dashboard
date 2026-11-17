import unittest

from social_identity import (
    SocialIdentityConflict,
    upsert_social_account,
    link_discovered_social_account,
)


class FakeCursor:
    def __init__(self, username_rows=None, external_rows=None, inserted_id="new-id"):
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
        return dict(
            workspace_id="workspace-1",
            platform="Instagram",
            external_account_id="17841400000000000",
            username="brand",
            display_name="Brand",
            encrypted_token="encrypted-token",
        )

    def discovery_kwargs(self):
        return dict(
            workspace_id="workspace-1",
            platform="Instagram",
            external_account_id="17841401520086875",
            username="brand",
            display_name="Brand",
        )

    def test_direct_oauth_inserts_new_identity(self):
        cur = FakeCursor(inserted_id="created-id")
        result = upsert_social_account(cur, **self.kwargs())
        self.assertEqual(result, "created-id")

    def test_direct_oauth_rejects_duplicate_external_ids(self):
        cur = FakeCursor(
            external_rows=[
                ("id-1", "brand"),
                ("id-2", "brand"),
            ]
        )
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())

    def test_direct_oauth_rejects_external_id_owned_by_other_username(self):
        cur = FakeCursor(
            external_rows=[("other-id", "another-brand")]
        )
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())

    def test_direct_oauth_reuses_username_identity(self):
        cur = FakeCursor(
            username_rows=[
                ("direct-id", "old-external-id", True, "connected")
            ]
        )
        result = upsert_social_account(cur, **self.kwargs())
        self.assertEqual(result, "direct-id")

    def test_direct_oauth_rejects_ambiguous_username(self):
        cur = FakeCursor(
            username_rows=[
                ("old-1", None, True, "connected"),
                ("old-2", None, True, "connected"),
            ]
        )
        with self.assertRaises(SocialIdentityConflict):
            upsert_social_account(cur, **self.kwargs())

    def test_direct_oauth_reuses_legacy_username_without_external_id(self):
        cur = FakeCursor(
            username_rows=[
                ("legacy-id", None, True, "connected")
            ]
        )
        result = upsert_social_account(cur, **self.kwargs())
        self.assertEqual(result, "legacy-id")


    def test_discovery_creates_passive_identity_when_missing(self):
        cur = FakeCursor(inserted_id="passive-id")
        result = link_discovered_social_account(
            cur, **self.discovery_kwargs()
        )
        self.assertEqual(result, "passive-id")

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

    def test_discovery_rejects_external_id_owned_by_another_username(self):
        cur = FakeCursor(
            external_rows=[("other-id", "another-brand")]
        )
        with self.assertRaises(SocialIdentityConflict):
            link_discovered_social_account(
                cur, **self.discovery_kwargs()
            )

    def test_discovery_reuses_direct_oauth_identity(self):
        cur = FakeCursor(
            username_rows=[
                ("direct-id", "28666758149640792", True, "connected")
            ]
        )
        result = link_discovered_social_account(
            cur, **self.discovery_kwargs()
        )
        self.assertEqual(result, "direct-id")

    def test_missing_external_identity_is_rejected(self):
        cur = FakeCursor()
        values = self.kwargs()
        values["external_account_id"] = ""
        with self.assertRaises(ValueError):
            upsert_social_account(cur, **values)

    def test_discovery_rejects_username_external_id_row_mismatch(self):
        cur = FakeCursor(
            username_rows=[
                ("username-id", None, True, "connected")
            ],
            external_rows=[
                ("external-id", "brand")
            ],
        )
        with self.assertRaises(SocialIdentityConflict):
            link_discovered_social_account(
                cur, **self.discovery_kwargs()
            )


if __name__ == "__main__":
    unittest.main()
