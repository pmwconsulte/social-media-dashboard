import re

import pytest
from flask import Flask

import auth
import facebook_messenger
import instagram_webhook
from security_rbac import issue_csrf_token, valid_csrf_token, has_permission
from waitlist import register_waitlist
from security_rbac import require_permission, refresh_session_identity, credential_fingerprint


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.query = query
        self.params = params

    def fetchall(self):
        return [("account-1", "pmw_test", "encrypted-token")]


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.fake_cursor = FakeCursor()

    def cursor(self):
        return self.fake_cursor

    def close(self):
        self.closed = True


class FakeCipher:
    def decrypt(self, value):
        assert value == b"encrypted-token"
        return b"mock-access-token"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self.payload

    def raise_for_status(self):
        if not self.ok:
            import requests
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)


@pytest.fixture
def inbox_client(monkeypatch):
    import database

    class AdminWorkspaceCursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.query = query
            self.params = params
        def fetchone(self):
            return ("workspace-test-id",)

    class AdminWorkspaceConnection:
        def cursor(self):
            return AdminWorkspaceCursor()
        def close(self):
            pass

    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ADMIN_USERNAME", "test-admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "admin-hash-test")
    monkeypatch.setattr(auth, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(database, "get_connection", lambda: AdminWorkspaceConnection())
    monkeypatch.setattr(auth, "_token_cipher", lambda: FakeCipher())

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test-secret-key")
    auth.setup_auth(app)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "test-admin"
        sess["user_id"] = None
        sess["workspace_id"] = "workspace-test-id"
        sess["workspace_slug"] = "workspace-test"
        sess["role"] = "platform_admin"
        sess["credential_fingerprint"] = credential_fingerprint("admin-hash-test", "test-secret-key")
        sess["instagram_inbox_csrf"] = "csrf-test"
    return client


def conversations_response():
    return FakeResponse({
        "data": [{
            "id": "conversation-1",
            "participants": {"data": [
                {"username": "pmw_test", "id": "ig-owner"},
                {"username": "customer", "id": "ig-user-123"},
            ]},
        }]
    })


def post_data(**overrides):
    data = {
        "csrf_token": "csrf-test",
        "conversation": "conversation-1",
        "message": "Olá! Obrigado pelo contacto.",
    }
    data.update(overrides)
    return data




def test_protected_route_refreshes_role_and_blocks_revoked_permission(monkeypatch):
    import database
    from flask import session
    from werkzeug.exceptions import Forbidden

    class IdentityCursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.params = params
        def fetchone(self):
            return ("viewer", "workspace-id", True, "workspace-test", "test-password-hash")

    class IdentityConnection:
        def cursor(self):
            return IdentityCursor()
        def close(self):
            self.closed = True

    monkeypatch.setattr(database, "get_connection", lambda: IdentityConnection())
    app = Flask("rbac-refresh-test")
    app.secret_key = "rbac-test-secret"

    @require_permission("messaging.reply")
    def protected_action():
        return "allowed"

    with app.test_request_context("/protected"):
        session["authenticated"] = True
        session["user_id"] = "user-id"
        session["workspace_id"] = "workspace-id"
        session["workspace_slug"] = "workspace-test"
        session["role"] = "owner"
        session["credential_fingerprint"] = credential_fingerprint("test-password-hash", "rbac-test-secret")
        with pytest.raises(Forbidden):
            protected_action()
        assert session["role"] == "viewer"


def test_dashboard_and_dash_callbacks_reject_deactivated_database_user(monkeypatch):
    import database

    class InactiveCursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.params = params
        def fetchone(self):
            return ("owner", "workspace-id", False, "workspace-test", "test-password-hash")

    class InactiveConnection:
        def cursor(self):
            return InactiveCursor()
        def close(self):
            self.closed = True

    monkeypatch.setenv("SECRET_KEY", "dashboard-auth-test")
    monkeypatch.setattr(database, "get_connection", lambda: InactiveConnection())
    app = Flask("dashboard-auth-test")
    app.secret_key = "dashboard-auth-test"
    auth.setup_auth(app)

    @app.get("/")
    def dashboard():
        return "dashboard data"

    @app.post("/_dash-update-component")
    def dash_callback():
        return "callback data"

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user_id"] = "inactive-user"
        sess["workspace_id"] = "workspace-id"
        sess["workspace_slug"] = "workspace-test"
        sess["role"] = "owner"
        sess["credential_fingerprint"] = credential_fingerprint("test-password-hash", "dashboard-auth-test")

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 302
    assert dashboard_response.headers["Location"].endswith("/login")
    callback_response = client.post("/_dash-update-component")
    assert callback_response.status_code == 401


def test_password_rotation_invalidates_existing_database_sessions(monkeypatch):
    import database

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.query = query
            self.params = params
        def fetchone(self):
            return ("owner", "workspace-a-id", True, "workspace-a", "new-password-hash")

    class Connection:
        def cursor(self):
            return Cursor()
        def close(self):
            pass

    monkeypatch.setattr(database, "get_connection", lambda: Connection())
    app = Flask("credential-rotation-test")
    app.secret_key = "credential-rotation-secret"
    identity = {
        "authenticated": True,
        "user_id": "user-a",
        "workspace_id": "workspace-a-id",
        "workspace_slug": "workspace-a",
        "role": "owner",
        "credential_fingerprint": credential_fingerprint("old-password-hash", "credential-rotation-secret"),
    }
    with app.app_context():
        assert refresh_session_identity(identity) is False
    assert identity == {}


def test_database_user_reassignment_to_another_workspace_invalidates_session(monkeypatch):
    import database

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.query = query
            self.params = params
        def fetchone(self):
            return ("owner", "workspace-b-id", True, "workspace-b", "test-password-hash")

    class Connection:
        def cursor(self):
            return Cursor()
        def close(self):
            pass

    monkeypatch.setattr(database, "get_connection", lambda: Connection())
    identity = {
        "authenticated": True,
        "user_id": "user-a",
        "workspace_id": "workspace-a-id",
        "workspace_slug": "workspace-a",
        "role": "owner",
        "credential_fingerprint": credential_fingerprint("test-password-hash", "test-secret"),
    }
    app = Flask("workspace-reassignment-test")
    app.secret_key = "test-secret"
    with app.app_context():
        assert refresh_session_identity(identity) is False
    assert identity == {}


def test_database_user_cannot_escalate_to_platform_admin(monkeypatch):
    import database

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, query, params): pass
        def fetchone(self):
            return ("platform_admin", "workspace-a-id", True, "workspace-a", "test-password-hash")

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    monkeypatch.setattr(database, "get_connection", lambda: Connection())
    app = Flask("database-platform-admin-deny")
    app.secret_key = "platform-admin-deny-secret"
    identity = {
        "authenticated": True, "user_id": "database-user",
        "workspace_id": "workspace-a-id", "workspace_slug": "workspace-a",
        "role": "owner",
        "credential_fingerprint": credential_fingerprint(
            "test-password-hash", "platform-admin-deny-secret"
        ),
    }
    with app.app_context():
        assert refresh_session_identity(identity) is False
    assert identity == {}


def test_environment_admin_session_requires_matching_admin_username(monkeypatch):
    import database

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, query, params): pass
        def fetchone(self): return ("workspace-a-id",)

    class Connection:
        def cursor(self): return Cursor()
        def close(self): pass

    monkeypatch.setattr(database, "get_connection", lambda: Connection())
    monkeypatch.setenv("ADMIN_USERNAME", "platform-operator")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "admin-hash-test")
    identity = {
        "authenticated": True, "username": "someone-else", "user_id": None,
        "workspace_id": "workspace-a-id", "workspace_slug": "workspace-a",
        "role": "platform_admin",
        "credential_fingerprint": credential_fingerprint(
            "admin-hash-test", "environment-admin-secret"
        ),
    }
    app = Flask("environment-admin-user-binding")
    app.secret_key = "environment-admin-secret"
    with app.app_context():
        assert refresh_session_identity(identity) is False
    assert identity == {}


def test_environment_admin_session_is_bound_to_existing_workspace_id(monkeypatch):
    import database

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.query = query
            self.params = params
        def fetchone(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()
        def close(self):
            pass

    monkeypatch.setattr(database, "get_connection", lambda: Connection())
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "admin-hash-test")
    identity = {
        "authenticated": True,
        "user_id": None,
        "workspace_id": "deleted-workspace-id",
        "workspace_slug": "pmw-default",
        "role": "platform_admin",
        "credential_fingerprint": credential_fingerprint("admin-hash-test", "environment-admin-secret"),
    }
    app = Flask("environment-admin-binding-test")
    app.secret_key = "environment-admin-secret"
    with app.app_context():
        assert refresh_session_identity(identity) is False
    assert identity == {}


def test_csrf_helpers_accept_only_current_session_tokens():
    session = {}
    token = issue_csrf_token(session)
    assert issue_csrf_token(session) == token
    assert valid_csrf_token(session, token)
    assert not valid_csrf_token({}, token)
    assert not valid_csrf_token(session, "wrong-token")
    session["whatsapp_csrf"] = "w" * 43
    assert valid_csrf_token(session, "w" * 43)


def test_instagram_webhook_validates_hmac_with_instagram_app_secret(monkeypatch):
    import hashlib
    import hmac

    secret = "instagram-webhook-test-secret"
    raw = b'{"object":"instagram","entry":[]}'
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", secret)
    app = Flask("instagram-signature-test")
    with app.test_request_context(
        "/webhooks/instagram",
        headers={"X-Hub-Signature-256": signature},
    ):
        assert instagram_webhook._signature_is_valid(raw)


def test_instagram_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", "instagram-webhook-test-secret")
    app = Flask("instagram-invalid-signature-test")
    with app.test_request_context(
        "/webhooks/instagram",
        headers={"X-Hub-Signature-256": "sha256=" + ("0" * 64)},
    ):
        assert not instagram_webhook._signature_is_valid(b"payload")


def test_messenger_webhook_accepts_only_facebook_app_hmac(monkeypatch):
    import hashlib
    import hmac

    secret = "facebook-webhook-test-secret"
    raw = b'{"object":"page","entry":[]}'
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    monkeypatch.setenv("FACEBOOK_APP_SECRET", secret)
    app = Flask("messenger-signature-test")
    with app.test_request_context(
        "/webhooks/messenger",
        headers={"X-Hub-Signature-256": signature},
    ):
        assert facebook_messenger._verify_signature(raw)


def test_messenger_webhook_rejects_missing_or_wrong_signature(monkeypatch):
    monkeypatch.setenv("FACEBOOK_APP_SECRET", "facebook-webhook-test-secret")
    app = Flask("messenger-invalid-signature-test")
    with app.test_request_context("/webhooks/messenger"):
        assert not facebook_messenger._verify_signature(b"payload")
    with app.test_request_context(
        "/webhooks/messenger",
        headers={"X-Hub-Signature-256": "sha256=" + ("0" * 64)},
    ):
        assert not facebook_messenger._verify_signature(b"payload")


def test_facebook_page_selector_is_bound_to_initiating_session():
    import auth

    session_a = {"facebook_oauth_selector": "selector-session-a"}
    assert auth._facebook_oauth_selector_matches("selector-session-a", session_a)
    assert not auth._facebook_oauth_selector_matches("selector-session-b", session_a)
    assert not auth._facebook_oauth_selector_matches("", session_a)
    session_b = {"facebook_oauth_selector": "selector-session-b"}
    assert not auth._facebook_oauth_selector_matches("selector-session-a", session_b)


def test_facebook_oauth_state_requires_same_session_nonce_and_workspace():
    import auth

    session = {
        "facebook_oauth_nonce": "nonce-session-a",
        "workspace_slug": "workspace-a",
    }
    assert auth._consume_facebook_oauth_state(
        {"nonce": "nonce-session-a", "workspace_slug": "workspace-a"},
        session,
    ) is True
    assert "facebook_oauth_nonce" not in session

    replay_session = {
        "facebook_oauth_nonce": "nonce-session-b",
        "workspace_slug": "workspace-a",
    }
    assert auth._consume_facebook_oauth_state(
        {"nonce": "nonce-session-a", "workspace_slug": "workspace-a"},
        replay_session,
    ) is False
    assert "facebook_oauth_nonce" not in replay_session

    foreign_workspace_session = {
        "facebook_oauth_nonce": "nonce-session-c",
        "workspace_slug": "workspace-b",
    }
    assert auth._consume_facebook_oauth_state(
        {"nonce": "nonce-session-c", "workspace_slug": "workspace-a"},
        foreign_workspace_session,
    ) is False


def test_facebook_instagram_link_lookup_requires_same_workspace_and_active_link():
    import auth

    class Cursor:
        def execute(self, query, params):
            self.query = query
            self.params = params
        def fetchone(self):
            return None

    cursor = Cursor()
    assert auth._linked_instagram_for_facebook(cursor, "facebook-account-a") is None
    assert cursor.params == ("facebook-account-a",)
    assert "fb.workspace_id=ig.workspace_id" in cursor.query
    assert "ig.id=fil.instagram_social_account_id" in cursor.query
    assert "COALESCE(fb.is_active, TRUE)=TRUE" in cursor.query
    assert "COALESCE(ig.is_active, TRUE)=TRUE" in cursor.query


def test_messenger_account_lookup_is_scoped_to_requested_workspace(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params=None):
            self.query = query
            self.params = params
        def fetchone(self):
            return None

    class Connection:
        def __init__(self):
            self.fake_cursor = Cursor()
        def cursor(self):
            return self.fake_cursor
        def close(self):
            pass

    conn = Connection()
    monkeypatch.setattr(facebook_messenger, "get_connection", lambda: conn)
    result = facebook_messenger._workspace_account("workspace-a", "foreign-account-id")
    assert result is None
    assert "JOIN workspaces w ON w.id=sa.workspace_id" in conn.fake_cursor.query
    assert "WHERE w.slug=%s" in conn.fake_cursor.query
    assert conn.fake_cursor.params == ("workspace-a", "foreign-account-id", "foreign-account-id")


def test_messenger_viewer_cannot_reply_to_inbound_message(monkeypatch):
    import database
    from datetime import datetime, timezone

    class IdentityCursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.query = query
        def fetchone(self):
            return ("viewer", "workspace-a-id", True, "workspace-a", "test-password-hash")

    class IdentityConnection:
        def cursor(self):
            return IdentityCursor()
        def close(self):
            pass

    class MessageCursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.query = query
            self.params = params
        def fetchone(self):
            return (datetime.now(timezone.utc),)

    class MessageConnection:
        def cursor(self):
            return MessageCursor()
        def close(self):
            pass

    monkeypatch.setattr(database, "get_connection", lambda: IdentityConnection())
    monkeypatch.setattr(facebook_messenger, "get_connection", lambda: MessageConnection())
    monkeypatch.setattr(
        facebook_messenger, "_workspace_account",
        lambda workspace_slug, account_id=None:
            ("account-a", "page-a", "Page A", "encrypted-token", "workspace-a-id"),
    )
    monkeypatch.setattr(facebook_messenger, "_ensure_table", lambda cursor: None)
    monkeypatch.setattr(
        facebook_messenger, "_send_reply",
        lambda *args: pytest.fail("Viewer não pode enviar resposta Messenger"),
    )

    app = Flask("messenger-rbac-test")
    app.secret_key = "messenger-rbac-secret"
    app.add_url_rule("/login", "login", lambda: "login")
    facebook_messenger.register_facebook_messenger(app)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user_id"] = "viewer-id"
        sess["workspace_id"] = "workspace-a-id"
        sess["workspace_slug"] = "workspace-a"
        sess["role"] = "owner"
        sess["credential_fingerprint"] = credential_fingerprint("test-password-hash", "messenger-rbac-secret")
        sess["facebook_messenger_csrf"] = "m" * 64

    response = client.post(
        "/facebook/messenger/inbox",
        data={
            "csrf_token": "m" * 64,
            "account": "account-a",
            "action": "reply",
            "recipient": "123456789",
            "message": "Resposta não autorizada",
        },
    )
    assert response.status_code == 403


def test_messenger_role_permissions_separate_reply_from_integration_management():
    assert has_permission("editor", "messaging.read")
    assert has_permission("editor", "messaging.reply")
    assert not has_permission("editor", "integrations.manage")
    assert has_permission("viewer", "messaging.read")
    assert not has_permission("viewer", "messaging.reply")


def test_read_only_roles_can_view_posts_but_not_publish():
    assert has_permission("viewer", "publishing.read")
    assert has_permission("analyst", "publishing.read")
    assert not has_permission("viewer", "publishing.create")
    assert not has_permission("analyst", "publishing.create")


def test_waitlist_requires_session_csrf_token():
    app = Flask("waitlist-csrf-test")
    app.secret_key = "waitlist-test-secret"
    app.config.update(TESTING=True)
    register_waitlist(app)
    client = app.test_client()
    page = client.get("/waitlist").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    rejected = client.post("/waitlist", data={"email": "x@example.com", "consent": "yes"})
    assert rejected.status_code == 400
    accepted_csrf = client.post(
        "/waitlist",
        data={"csrf_token": token, "email": "invalid-email", "consent": "yes"},
    )
    assert accepted_csrf.status_code == 400
    assert "endereço de email válido" in accepted_csrf.get_data(as_text=True)

def test_dashboard_logout_link_requires_confirmation_and_post_csrf(inbox_client):
    response = inbox_client.get("/logout")
    assert response.status_code == 200
    assert 'method="post" action="/logout"' in response.get_data(as_text=True)
    with inbox_client.session_transaction() as sess:
        assert sess.get("authenticated") is True
        token = sess.get("csrf_token")
    rejected = inbox_client.post("/logout", data={"csrf_token": "wrong-token"})
    assert rejected.status_code == 400
    with inbox_client.session_transaction() as sess:
        assert sess.get("authenticated") is True
    accepted = inbox_client.post("/logout", data={"csrf_token": token})
    assert accepted.status_code == 302
    with inbox_client.session_transaction() as sess:
        assert not sess.get("authenticated")


def test_login_fails_closed_when_case_insensitive_email_is_ambiguous(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "ambiguous-login-secret")
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.query = query
            self.params = params
        def fetchall(self):
            return [
                ("user-a", "Pedro@example.com", "hash-a", "workspace-a-id", "owner"),
                ("user-b", "pedro@example.com", "hash-b", "workspace-b-id", "owner"),
            ]

    class Connection:
        def cursor(self):
            return Cursor()
        def close(self):
            pass

    monkeypatch.setattr(auth, "get_connection", lambda: Connection())
    app = Flask("ambiguous-login-test")
    app.secret_key = "ambiguous-login-secret"
    auth.setup_auth(app)
    client = app.test_client()
    page = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post(
        "/login",
        data={
            "csrf_token": token,
            "username": "PEDRO@example.com",
            "password": "any-password",
        },
    )
    assert response.status_code == 200
    assert "Email/username ou password inválido." in response.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert not sess.get("authenticated")


def test_login_post_requires_global_csrf_token(inbox_client):
    response = inbox_client.post(
        "/login",
        data={"username": "someone@example.com", "password": "not-a-real-password"},
    )
    assert response.status_code == 400
    assert "Pedido inválido" in response.get_data(as_text=True)


def test_viewer_cannot_send_instagram_direct_even_with_valid_csrf(inbox_client, monkeypatch):
    import database

    class ViewerCursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, query, params):
            self.params = params
        def fetchone(self):
            return ("viewer", "workspace-test-id", True, "workspace-test", "test-password-hash")

    class ViewerConnection:
        def cursor(self):
            return ViewerCursor()
        def close(self):
            pass

    monkeypatch.setattr(database, "get_connection", lambda: ViewerConnection())
    with inbox_client.session_transaction() as sess:
        sess["user_id"] = "user-viewer"
        sess["workspace_id"] = "workspace-test-id"
        sess["workspace_slug"] = "workspace-test"
        sess["role"] = "owner"
        sess["credential_fingerprint"] = credential_fingerprint("test-password-hash", "test-secret-key")

    calls = []
    def fake_get(*args, **kwargs):
        calls.append(("get", args, kwargs))
        return conversations_response()
    def fake_post(*args, **kwargs):
        calls.append(("post", args, kwargs))
        pytest.fail("Um utilizador viewer não pode enviar mensagens Instagram")
    monkeypatch.setattr(auth.requests, "get", fake_get)
    monkeypatch.setattr(auth.requests, "post", fake_post)
    response = inbox_client.post("/instagram/inbox", data=post_data())

    assert response.status_code == 403
    assert [call[0] for call in calls] == ["get"]


def test_instagram_inbox_lists_accounts_only_from_session_workspace(inbox_client, monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(auth, "get_connection", lambda: connection)
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: conversations_response())

    response = inbox_client.get("/instagram/inbox")
    assert response.status_code == 200
    assert "JOIN workspaces w ON w.id = sa.workspace_id" in connection.fake_cursor.query
    assert "WHERE w.slug=%s" in connection.fake_cursor.query
    assert connection.fake_cursor.params == ("workspace-test",)
    assert connection.closed is True


def test_inbox_rejeita_csrf_invalido_sem_enviar(inbox_client, monkeypatch):
    calls = []
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: conversations_response())
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: calls.append((a, k)))

    response = inbox_client.post("/instagram/inbox", data=post_data(csrf_token="wrong"))

    assert response.status_code == 400
    assert calls == []


def test_inbox_rejeita_conversa_fora_da_conta(inbox_client, monkeypatch):
    calls = []
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: conversations_response())
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: calls.append((a, k)))

    response = inbox_client.post(
        "/instagram/inbox",
        data=post_data(conversation="conversation-of-another-account"),
    )

    assert response.status_code == 302
    assert calls == []


def test_inbox_nao_confirma_envio_quando_meta_rejeita(inbox_client, monkeypatch):
    get_calls = []

    def fake_get(url, **kwargs):
        get_calls.append(url)
        if url.endswith("/me/conversations"):
            return conversations_response()
        if url.endswith("/conversation-1"):
            return FakeResponse({"messages": {"data": [
                {"from": {"id": "ig-owner-456"}, "message": "Resposta anterior"},
                {"from": {"id": "ig-user-123"}, "message": "Olá"}
            ]}})
        if url.endswith("/me"):
            return FakeResponse({"id": "ig-owner-456"})
        pytest.fail(f"Pedido GET inesperado: {url}")

    sent = []
    monkeypatch.setattr(auth.requests, "get", fake_get)
    monkeypatch.setattr(
        auth.requests, "post",
        lambda url, **kwargs: (sent.append((url, kwargs)) or FakeResponse(
            {"error": {"message": "Permission denied"}}, 403
        )),
    )

    response = inbox_client.post("/instagram/inbox", data=post_data())

    assert response.status_code == 302
    assert len(sent) == 1
    assert "sent=" not in response.location
    assert "error=" in response.location


def test_inbox_envia_resposta_com_destinatario_validado(inbox_client, monkeypatch):
    def fake_get(url, **kwargs):
        if url.endswith("/me/conversations"):
            return conversations_response()
        if url.endswith("/conversation-1"):
            return FakeResponse({"messages": {"data": [
                {"from": {"username": "customer", "id": "ig-user-123"}, "message": "Olá"}
            ]}})
        if url.endswith("/me"):
            return FakeResponse({"id": "ig-owner-456"})
        pytest.fail(f"Pedido GET inesperado: {url}")

    sent = []
    monkeypatch.setattr(auth.requests, "get", fake_get)
    monkeypatch.setattr(
        auth.requests, "post",
        lambda url, **kwargs: (sent.append((url, kwargs)) or FakeResponse({"message_id": "msg-1"})),
    )

    response = inbox_client.post("/instagram/inbox", data=post_data())

    assert response.status_code == 302
    assert len(sent) == 1
    url, kwargs = sent[0]
    assert url.endswith("/ig-owner-456/messages")
    assert kwargs["json"] == {
        "recipient": {"id": "ig-user-123"},
        "message": {"text": "Olá! Obrigado pelo contacto."},
    }
    assert "sent=" in response.location
