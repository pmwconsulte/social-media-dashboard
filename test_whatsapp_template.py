"""Regression tests for guarded WhatsApp template conversations.

All Graph API calls are mocked; these tests never send real WhatsApp messages.
"""
import importlib

import pytest
from flask import Flask


@pytest.fixture
def app_module(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "whatsapp-tests-secret")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_STATUS", "IN_REVIEW")
    import app as module
    module.server.secret_key = "whatsapp-tests-secret"
    return module


class FakeCursor:
    def __init__(self, workspace=("workspace-id",)):
        self.workspace = workspace
        self.executed = []
        self.last_query = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.last_query = query
        self.executed.append((query, params))

    def fetchone(self):
        return self.workspace


class FakeConnection:
    def __init__(self, workspace=("workspace-id",)):
        self.fake_cursor = FakeCursor(workspace)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def whatsapp_client(app_module):
    client = app_module.server.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["workspace_slug"] = "workspace-test"
        session["whatsapp_csrf"] = "csrf-test"
    return client


def post_template(client, **overrides):
    data = {
        "csrf_token": "csrf-test",
        "recipient": "258823002555",
    }
    data.update(overrides)
    return client.post("/whatsapp/send-template", data=data)


def test_template_route_requires_login(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "send_whatsapp_template",
                        lambda *args: calls.append(args))
    response = app_module.server.test_client().post(
        "/whatsapp/send-template",
        data={"csrf_token": "csrf-test", "recipient": "258823002555"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert calls == []


def test_template_route_rejects_invalid_csrf(whatsapp_client, app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "send_whatsapp_template",
                        lambda *args: calls.append(args))
    response = post_template(whatsapp_client, csrf_token="wrong")
    assert response.status_code == 400
    assert calls == []


def test_template_route_fails_closed_while_meta_review_pending(
    whatsapp_client, app_module, monkeypatch
):
    monkeypatch.setenv("WHATSAPP_TEMPLATE_STATUS", "IN_REVIEW")
    db_calls = []
    api_calls = []
    monkeypatch.setattr(app_module, "get_connection", lambda: db_calls.append(True))
    monkeypatch.setattr(app_module, "send_whatsapp_template",
                        lambda *args: api_calls.append(args))

    response = post_template(whatsapp_client)

    assert response.status_code == 409
    assert "bloqueado" in response.get_data(as_text=True)
    assert db_calls == []
    assert api_calls == []


@pytest.mark.parametrize("recipient", ["", "12345", "abc-123", "1234567890123456"])
def test_template_route_rejects_invalid_recipient(
    whatsapp_client, app_module, monkeypatch, recipient
):
    monkeypatch.setenv("WHATSAPP_TEMPLATE_STATUS", "APPROVED")
    db_calls = []
    api_calls = []
    monkeypatch.setattr(app_module, "get_connection", lambda: db_calls.append(True))
    monkeypatch.setattr(app_module, "send_whatsapp_template",
                        lambda *args: api_calls.append(args))

    response = post_template(whatsapp_client, recipient=recipient)

    assert response.status_code == 400
    assert db_calls == []
    assert api_calls == []


def test_template_route_sends_only_after_approval_and_persists_outbound(
    whatsapp_client, app_module, monkeypatch
):
    monkeypatch.setenv("WHATSAPP_TEMPLATE_STATUS", "APPROVED")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-id-test")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "pmw_primeiro_contacto")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_LANGUAGE", "pt_PT")
    connection = FakeConnection()
    api_calls = []

    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(
        app_module, "send_whatsapp_template",
        lambda *args: (api_calls.append(args) or "wamid.mock-message"),
    )

    response = post_template(whatsapp_client, recipient="+258 82 300 2555")

    assert response.status_code == 303
    assert response.headers["Location"] == "/whatsapp/inbox?q=258823002555"
    assert api_calls == [
        ("phone-id-test", "258823002555", "pmw_primeiro_contacto", "pt_PT")
    ]
    insert = [
        (query, params) for query, params in connection.fake_cursor.executed
        if "INSERT INTO whatsapp_messages" in query
    ]
    assert len(insert) == 1
    assert insert[0][1][0:5] == (
        "workspace-id", "wamid.mock-message", "phone-id-test",
        "258823002555", "PMW Dashboard"
    )
    assert connection.committed is True
    assert connection.closed is True
    assert connection.rolled_back is False


def test_template_route_does_not_send_if_workspace_missing(
    whatsapp_client, app_module, monkeypatch
):
    monkeypatch.setenv("WHATSAPP_TEMPLATE_STATUS", "APPROVED")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "phone-id-test")
    connection = FakeConnection(workspace=None)
    api_calls = []
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "send_whatsapp_template",
                        lambda *args: api_calls.append(args))

    response = post_template(whatsapp_client)

    assert response.status_code == 404
    assert api_calls == []
    assert connection.closed is True


class FakeMetaResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self.payload


def test_template_helper_posts_expected_payload(monkeypatch):
    import whatsapp_webhook

    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "mock-token")
    calls = []
    monkeypatch.setattr(
        whatsapp_webhook.requests, "post",
        lambda url, **kwargs: (
            calls.append((url, kwargs))
            or FakeMetaResponse({"messages": [{"id": "wamid.mock"}]})
        ),
    )

    result = whatsapp_webhook.send_whatsapp_template(
        "phone-id", "+258 82 300 2555", "pmw_primeiro_contacto", "pt_PT"
    )

    assert result == "wamid.mock"
    url, kwargs = calls[0]
    assert url.endswith("/v25.0/phone-id/messages")
    assert kwargs["headers"]["Authorization"] == "Bearer mock-token"
    assert kwargs["json"] == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "258823002555",
        "type": "template",
        "template": {
            "name": "pmw_primeiro_contacto",
            "language": {"code": "pt_PT"},
        },
    }


def test_template_helper_rejects_invalid_recipient_before_api_call(monkeypatch):
    import whatsapp_webhook

    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "mock-token")
    calls = []
    monkeypatch.setattr(whatsapp_webhook.requests, "post",
                        lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(ValueError, match="Número de destino inválido"):
        whatsapp_webhook.send_whatsapp_template(
            "phone-id", "123", "pmw_primeiro_contacto", "pt_PT"
        )
    assert calls == []


def test_template_helper_requires_access_token(monkeypatch):
    import whatsapp_webhook

    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="WHATSAPP_ACCESS_TOKEN"):
        whatsapp_webhook.send_whatsapp_template(
            "phone-id", "258823002555", "pmw_primeiro_contacto", "pt_PT"
        )
