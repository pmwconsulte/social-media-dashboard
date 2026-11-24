from unittest.mock import patch

from flask import Flask

import auth
from security_rbac import issue_csrf_token


class FakeCursor:
    def __init__(self, is_active):
        self.is_active = is_active
        self.calls = []
        self.result = None

    def execute(self, query, params=None):
        normalized = " ".join(query.split()).lower()
        self.calls.append((normalized, params))

        if normalized.startswith("select id, subscription_plan"):
            self.result = ("workspace-1", "trial")
        elif normalized.startswith("select platform, username"):
            self.result = (
                "Instagram",
                "brand",
                "external-123",
                self.is_active,
                "connected",
            )

    def fetchone(self):
        result = self.result
        self.result = None
        return result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeConnection:
    def __init__(self, is_active):
        self.fake_cursor = FakeCursor(is_active)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def make_client(monkeypatch, connection):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")

    app = Flask(__name__)
    app.config.update(TESTING=True)

    monkeypatch.setattr(
        "security_rbac.refresh_session_identity",
        lambda current_session: True,
    )
    monkeypatch.setattr(
        auth,
        "refresh_session_identity",
        lambda current_session: True,
    )

    with (
        patch("auth.register_facebook_posts_route"),
        patch("payments.register_paypal_routes"),
    ):
        auth.setup_auth(app)

    monkeypatch.setattr(auth, "get_connection", lambda: connection)

    client = app.test_client()

    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["role"] = "owner"
        sess["workspace_slug"] = "test-workspace"
        csrf_token = issue_csrf_token(sess)

    return client, csrf_token


def test_active_social_account_can_be_deactivated(monkeypatch):
    connection = FakeConnection(is_active=True)
    client, csrf_token = make_client(monkeypatch, connection)

    response = client.post(
        "/social-accounts/account-1/toggle",
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == 302
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed is True

    updates = [
        query for query, _ in connection.fake_cursor.calls
        if query.startswith("update social_accounts")
    ]
    assert len(updates) == 1
    assert "set is_active=false" in updates[0]


def test_inactive_social_account_cannot_be_reactivated(monkeypatch):
    connection = FakeConnection(is_active=False)
    client, csrf_token = make_client(monkeypatch, connection)

    response = client.post(
        "/social-accounts/account-1/toggle",
        data={"csrf_token": csrf_token},
    )

    assert response.status_code == 302
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True

    writes = [
        query for query, _ in connection.fake_cursor.calls
        if query.startswith((
            "update social_accounts",
            "insert into social_accounts",
            "delete from social_accounts",
        ))
    ]
    assert writes == []
