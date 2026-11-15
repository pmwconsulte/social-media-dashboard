import re

import pytest
from flask import Flask

import auth
from security_rbac import issue_csrf_token, valid_csrf_token, has_permission
from waitlist import register_waitlist
from security_rbac import require_permission


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

    def cursor(self):
        return FakeCursor()

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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setattr(auth, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(auth, "_token_cipher", lambda: FakeCipher())

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test-secret-key")
    auth.setup_auth(app)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user_id"] = None
        sess["workspace_id"] = "workspace-test-id"
        sess["workspace_slug"] = "workspace-test"
        sess["role"] = "platform_admin"
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
            return ("viewer", "workspace-id", True, "workspace-test")

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
        with pytest.raises(Forbidden):
            protected_action()
        assert session["role"] == "viewer"


def test_csrf_helpers_accept_only_current_session_tokens():
    session = {}
    token = issue_csrf_token(session)
    assert issue_csrf_token(session) == token
    assert valid_csrf_token(session, token)
    assert not valid_csrf_token({}, token)
    assert not valid_csrf_token(session, "wrong-token")
    session["whatsapp_csrf"] = "w" * 43
    assert valid_csrf_token(session, "w" * 43)


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

def test_login_post_requires_global_csrf_token(inbox_client):
    response = inbox_client.post(
        "/login",
        data={"username": "someone@example.com", "password": "not-a-real-password"},
    )
    assert response.status_code == 400
    assert "Pedido inválido" in response.get_data(as_text=True)


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
