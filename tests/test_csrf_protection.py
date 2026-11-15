import re

from flask import Flask

from waitlist import register_waitlist


def make_client():
    app = Flask(__name__)
    app.secret_key = "test-csrf-secret"
    app.config.update(TESTING=True)
    register_waitlist(app)
    return app.test_client()


def test_public_waitlist_renders_session_bound_csrf_token():
    client = make_client()
    response = client.get("/waitlist")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match
    assert len(match.group(1)) >= 32


def test_public_waitlist_rejects_missing_csrf_token():
    client = make_client()
    response = client.post(
        "/waitlist",
        data={"email": "visitor@example.com", "consent": "yes"},
    )
    assert response.status_code == 400
    assert "Pedido inválido" in response.get_data(as_text=True)


def test_public_waitlist_accepts_session_csrf_before_validating_email():
    client = make_client()
    page = client.get("/waitlist").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post(
        "/waitlist",
        data={"csrf_token": token, "email": "invalid-email", "consent": "yes"},
    )
    assert response.status_code == 400
    assert "endereço de email válido" in response.get_data(as_text=True)
