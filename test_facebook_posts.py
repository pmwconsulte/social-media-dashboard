from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from flask import Flask

import facebook_posts


class FakeCursor:
    def __init__(self, pages):
        self.pages = pages
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params
        self.result = [page[:4] for page in self.pages if page[4] == params[0]]

    def fetchall(self):
        return self.result


class FakeConnection:
    def __init__(self, pages):
        self.fake_cursor = FakeCursor(pages)
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def close(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch):
    key = Fernet.generate_key()
    cipher = Fernet(key)
    encrypted = cipher.encrypt(b"page-token").decode("utf-8")
    pages = [
        ("page-row-1", "123456", "PMW Consult", encrypted, "workspace-test"),
        ("page-row-2", "789012", "Ayane Doces", encrypted, "workspace-test"),
        ("page-row-foreign", "999999", "Página de outro workspace", encrypted, "workspace-alheio"),
    ]
    connection = FakeConnection(pages)
    app = Flask(__name__)
    app.secret_key = "test-only-secret"
    app.add_url_rule("/login", "login", lambda: "login")
    facebook_posts.register_facebook_posts_route(
        app, lambda: connection, lambda: cipher
    )
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client, connection, pages


def login(client):
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["workspace_slug"] = "workspace-test"


def api_response(payload, status=200):
    response = Mock()
    response.ok = 200 <= status < 400
    response.status_code = status
    response.json.return_value = payload
    return response


def test_unauthenticated_user_is_redirected_to_login(client):
    test_client, _, _ = client
    response = test_client.get("/facebook/posts")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_only_pages_from_current_workspace_query_are_rendered(client, monkeypatch):
    test_client, connection, _ = client
    login(test_client)
    monkeypatch.setattr(facebook_posts.requests, "get", lambda *a, **k: api_response({"data": []}))
    response = test_client.get("/facebook/posts")
    assert response.status_code == 200
    assert "PMW Consult" in response.get_data(as_text=True)
    assert "Ayane Doces" in response.get_data(as_text=True)
    assert "Página de outro workspace" not in response.get_data(as_text=True)
    assert connection.fake_cursor.params == ("workspace-test",)
    assert "JOIN workspaces w ON w.id=sa.workspace_id" in connection.fake_cursor.query
    assert "WHERE w.slug=%s" in connection.fake_cursor.query
    assert connection.closed is True


def test_page_selection_uses_page_id_and_decrypted_page_token(client, monkeypatch):
    test_client, _, pages = client
    login(test_client)
    calls = {}

    def fake_get(url, params, headers, timeout):
        calls.update(url=url, params=params, headers=headers, timeout=timeout)
        return api_response({"data": []})

    monkeypatch.setattr(facebook_posts.requests, "get", fake_get)
    response = test_client.get("/facebook/posts?page=page-row-2")
    assert response.status_code == 200
    assert calls["url"].endswith("/789012/published_posts")
    assert "access_token" not in calls["params"]
    assert calls["headers"]["Authorization"] == "Bearer page-token"
    assert calls["params"]["limit"] == 25
    assert "selected" in response.get_data(as_text=True)


def test_page_selection_cannot_select_an_unlisted_row(client, monkeypatch):
    test_client, _, _ = client
    login(test_client)
    calls = []
    monkeypatch.setattr(
        facebook_posts.requests, "get",
        lambda url, **kwargs: (calls.append(url) or api_response({"data": []})),
    )
    response = test_client.get("/facebook/posts?page=foreign-page-row")
    assert response.status_code == 200
    assert calls[0].endswith("/123456/published_posts")


def test_empty_posts_are_reported_without_inventing_metrics(client, monkeypatch):
    test_client, _, _ = client
    login(test_client)
    monkeypatch.setattr(facebook_posts.requests, "get", lambda *a, **k: api_response({"data": []}))
    response = test_client.get("/facebook/posts")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "A API não devolveu publicações" in page
    assert "Reações:" not in page


@pytest.mark.parametrize("status,code", [(400, 100), (403, 200), (500, 1)])
def test_meta_http_errors_are_handled_without_exposing_token(client, monkeypatch, capsys, status, code):
    test_client, _, _ = client
    login(test_client)
    monkeypatch.setattr(
        facebook_posts.requests, "get",
        lambda *a, **k: api_response({"error": {"message": "private detail", "code": code}}, status),
    )
    response = test_client.get("/facebook/posts")
    captured = capsys.readouterr()
    assert response.status_code == 200
    assert "não disponibilizou as publicações" in response.get_data(as_text=True)
    assert "page-token" not in captured.out
    assert "private detail" in captured.out
    assert f"code={code}" in captured.out


def test_only_meta_fields_requested_by_dashboard_are_rendered(client, monkeypatch):
    test_client, _, _ = client
    login(test_client)
    payload = {
        "data": [{
            "id": "post-1",
            "message": "Publicação de teste",
            "created_time": "2026-11-01T10:00:00+0000",
            "permalink_url": "https://www.facebook.com/example/posts/1",
            "reactions": {"summary": {"total_count": 4}},
        }]
    }
    monkeypatch.setattr(facebook_posts.requests, "get", lambda *a, **k: api_response(payload))
    response = test_client.get("/facebook/posts")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Publicação de teste" in page
    # The dashboard requests only the fields validated against Meta's API.
    # Extra fields in a mocked payload must not be presented as dashboard metrics.
    assert "Reações: 4" not in page
    assert "Comentários:" not in page
    assert "Partilhas:" not in page
    assert "Interações não disponibilizadas pela API" not in page


def test_request_exception_does_not_expose_credentials(client, monkeypatch, capsys):
    test_client, _, _ = client
    login(test_client)

    def fail_request(*args, **kwargs):
        raise facebook_posts.requests.RequestException("page-token must not appear")

    monkeypatch.setattr(facebook_posts.requests, "get", fail_request)
    response = test_client.get("/facebook/posts")
    captured = capsys.readouterr()
    assert response.status_code == 200
    assert "Não foi possível consultar as publicações" in response.get_data(as_text=True)
    assert "page-token" not in captured.out
