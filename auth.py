import os
from functools import wraps

import bcrypt
from flask import (
    Flask,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)


LOGIN_PAGE = """
<!doctype html>
<html lang="pt">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Login — PMW Social Media Dashboard AI</title>
    <style>
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: Arial, sans-serif;
            background: #020617;
            color: #f8fafc;
        }
        .card {
            width: min(92%, 420px);
            padding: 34px;
            border-radius: 18px;
            background: #111827;
            border: 1px solid #1e293b;
            box-shadow: 0 20px 60px rgba(0,0,0,.35);
        }
        h1 { margin: 0 0 8px; text-align: center; }
        p { color: #94a3b8; text-align: center; margin-bottom: 28px; }
        label { display: block; margin: 16px 0 7px; font-weight: 600; }
        input {
            width: 100%;
            padding: 13px;
            border-radius: 10px;
            border: 1px solid #334155;
            background: #020617;
            color: #fff;
            outline: none;
        }
        input:focus { border-color: #38bdf8; }
        button {
            width: 100%;
            margin-top: 24px;
            padding: 13px;
            border: 0;
            border-radius: 10px;
            background: #38bdf8;
            color: #020617;
            font-weight: 700;
            cursor: pointer;
        }
        .error {
            margin-top: 16px;
            padding: 10px;
            border-radius: 8px;
            background: rgba(239,68,68,.12);
            color: #fca5a5;
            text-align: center;
        }
        .footer {
            margin-top: 22px;
            font-size: 12px;
            color: #64748b;
            text-align: center;
        }
    </style>
</head>
<body>
    <main class="card">
        <h1>🔐 Dashboard AI</h1>
        <p>Autenticação necessária para continuar</p>
        <form method="post" action="/login">
            <label for="username">Username</label>
            <input id="username" name="username" type="text"
                   autocomplete="username" required autofocus>

            <label for="password">Password</label>
            <input id="password" name="password" type="password"
                   autocomplete="current-password" required>

            <button type="submit">Entrar</button>
        </form>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <div class="footer">PMW Consultoria & Tecnologia</div>
    </main>
</body>
</html>
"""


def _env_bool(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def setup_auth(server: Flask):
    """Configure secure session authentication for the Dash server."""
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY não está configurada no ambiente.")

    server.secret_key = secret_key
    server.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=_env_bool("SESSION_COOKIE_SECURE", True),
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=3600,
    )

    @server.get("/health")
    def health():
        return "OK", 200

    @server.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("authenticated"):
            return redirect(url_for("index"))

        error = None

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            configured_username = os.getenv("ADMIN_USERNAME", "").strip()
            password_hash = os.getenv("ADMIN_PASSWORD_HASH", "").strip()

            valid = False
            if configured_username and password_hash:
                try:
                    valid = (
                        username == configured_username
                        and bcrypt.checkpw(
                            password.encode("utf-8"),
                            password_hash.encode("utf-8"),
                        )
                    )
                except (ValueError, TypeError):
                    valid = False

            if valid:
                session.clear()
                session["authenticated"] = True
                session["username"] = username
                session.permanent = True
                return redirect(url_for("index"))

            error = "Username ou password inválido."

        return render_template_string(LOGIN_PAGE, error=error)

    @server.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @server.before_request
    def require_authentication():
        endpoint = request.endpoint or ""
        if endpoint in {"login", "health", "static"}:
            return None
        if request.path.startswith("/_dash-component-suites/"):
            return None
        if session.get("authenticated"):
            return None
        if request.path.startswith("/_dash-"):
            return redirect(url_for("login"))
        return redirect(url_for("login"))

    return server
