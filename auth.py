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

from billing import PLANS, evaluate_workspace_access
from database import get_connection


BILLING_PAGE = """
<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plano e Subscrição — PMW Dashboard AI</title>
<style>
body{margin:0;background:#020617;color:#f8fafc;font-family:Arial,sans-serif}
.wrap{max-width:1100px;margin:40px auto;padding:20px}
.card{background:#111827;border:1px solid #1e293b;border-radius:18px;padding:26px;margin-bottom:22px}
h1{margin-top:0} .muted{color:#94a3b8}.status{padding:12px;border-radius:10px;background:#172554;margin:18px 0}
.plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}
.plan{background:#0f172a;border:1px solid #334155;border-radius:14px;padding:20px}
.price{font-size:24px;font-weight:700;margin:10px 0 18px}
ul{padding-left:20px;line-height:1.8}
button,a.btn{display:inline-block;padding:11px 16px;border-radius:9px;text-decoration:none;font-weight:700;border:0;background:#38bdf8;color:#020617}
a.logout{background:#ef4444;color:white;margin-left:8px}
</style>
</head>
<body>
<div class="wrap">
<div class="card">
<h1>💳 Plano e Subscrição</h1>
<p class="muted">PMW Social Media Dashboard AI</p>
<div class="status">
<strong>Estado:</strong> {{ status }}<br>
<strong>Plano:</strong> {{ plan_name }}
{% if days_left > 0 %}<br><strong>Dias restantes do trial:</strong> {{ days_left }}{% endif %}
</div>
{% if expired %}
<p>O período gratuito de 30 dias terminou. Os seus dados permanecem guardados, mas o acesso ao dashboard está bloqueado até ativar uma subscrição.</p>
{% else %}
<p>O período de teste está ativo. Escolha um plano quando estiver pronto para continuar.</p>
{% endif %}
<a class="btn" href="/billing">Atualizar subscrição</a>
<a class="btn logout" href="/logout">Sair</a>
</div>
<div class="plans">
{% for key, item in plans.items() %}
<div class="plan">
<h2>{{ item.name }}</h2>
<div class="price">{{ item.price }}</div>
<ul>
<li>Contas sociais: {{ item.accounts }}</li>
<li>Utilizadores: {{ item.users }}</li>
<li>Analytics e IA</li>
</ul>
<button disabled>Pagamento em breve</button>
</div>
{% endfor %}
</div>
</div>
</body>
</html>
"""

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

    @server.get("/billing")
    def billing():
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        try:
            conn = get_connection()
            try:
                access = evaluate_workspace_access(conn)
            finally:
                conn.close()
        except Exception as erro:
            print(f"[BILLING ERROR] {erro}")
            return "Serviço de faturação temporariamente indisponível.", 503

        workspace = access.get("workspace") or {}
        plan_key = access.get("plan", "trial")
        plan_name = PLANS.get(plan_key, PLANS["trial"])["name"]

        return render_template_string(
            BILLING_PAGE,
            status=access.get("status", "unknown"),
            plan_name=plan_name,
            days_left=access.get("days_left", 0),
            expired=not access.get("allowed", False),
            plans=PLANS,
        )

    @server.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("authenticated"):
            try:
                conn = get_connection()
                try:
                    access = evaluate_workspace_access(conn)
                finally:
                    conn.close()
                if access["allowed"]:
                    return redirect(url_for("index"))
                return redirect(url_for("billing"))
            except Exception as erro:
                print(f"[BILLING LOGIN ERROR] {erro}")
                return redirect(url_for("billing"))

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
        if endpoint in {"login", "health", "billing", "static"}:
            return None
        if request.path.startswith("/_dash-component-suites/"):
            return None
        if session.get("authenticated"):
            # O acesso ao dashboard é condicionado ao estado da subscrição.
            if request.path == "/logout":
                return None
            try:
                conn = get_connection()
                try:
                    access = evaluate_workspace_access(conn)
                finally:
                    conn.close()
                if access["allowed"]:
                    return None
                return redirect(url_for("billing"))
            except Exception as erro:
                print(f"[BILLING ACCESS ERROR] {erro}")
                return redirect(url_for("billing"))
        if request.path.startswith("/_dash-"):
            return redirect(url_for("login"))
        return redirect(url_for("login"))

    return server
