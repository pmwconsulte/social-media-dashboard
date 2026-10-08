import os
import re
import uuid
from functools import wraps

import bcrypt
import base64
import hashlib
import secrets
import requests
from cryptography.fernet import Fernet, InvalidToken
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


def _oauth_configured():
    required = (
        "INSTAGRAM_APP_ID",
        "INSTAGRAM_APP_SECRET",
        "INSTAGRAM_OAUTH_REDIRECT_URI",
        "OAUTH_TOKEN_ENCRYPTION_KEY",
    )
    return all(os.getenv(name, "").strip() for name in required)


def _token_cipher():
    raw = os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY", "")
    if not raw:
        raise RuntimeError("OAUTH_TOKEN_ENCRYPTION_KEY não está definida.")
    try:
        key = raw.encode("utf-8")
        Fernet(key)
        return Fernet(key)
    except Exception:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_token(token):
    return _token_cipher().encrypt(token.encode("utf-8")).decode("utf-8")


def _instagram_authorize_url(state):
    from urllib.parse import urlencode

    client_id = os.getenv("INSTAGRAM_APP_ID", "").strip()
    redirect_uri = os.getenv("INSTAGRAM_OAUTH_REDIRECT_URI", "").strip()

    if not client_id or not redirect_uri:
        raise RuntimeError("INSTAGRAM_APP_ID ou INSTAGRAM_OAUTH_REDIRECT_URI não configurado.")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "instagram_business_basic",
        "state": state,
    }

    return "https://www.instagram.com/oauth/authorize?" + urlencode(params)



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

SIGNUP_PAGE = """
<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Criar conta — PMW Social Media Dashboard AI</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:Arial,sans-serif;background:#020617;color:#f8fafc}
.card{width:min(92%,460px);padding:34px;border-radius:18px;background:#111827;border:1px solid #1e293b;box-shadow:0 20px 60px rgba(0,0,0,.35)}
h1{margin:0 0 8px;text-align:center}p{color:#94a3b8;text-align:center;margin-bottom:24px}
label{display:block;margin:14px 0 7px;font-weight:600}input{width:100%;padding:13px;border-radius:10px;border:1px solid #334155;background:#020617;color:#fff;outline:none}
input:focus{border-color:#38bdf8}button{width:100%;margin-top:22px;padding:13px;border:0;border-radius:10px;background:#38bdf8;color:#020617;font-weight:700;cursor:pointer}
.error{margin-top:16px;padding:10px;border-radius:8px;background:rgba(239,68,68,.12);color:#fca5a5;text-align:center}
.link{display:block;text-align:center;margin-top:18px;color:#38bdf8;text-decoration:none}.footer{margin-top:22px;font-size:12px;color:#64748b;text-align:center}
</style></head><body><main class="card">
<h1>🚀 Criar conta</h1><p>Comece o seu trial gratuito de 30 dias</p>
<form method="post" action="/signup">
<label for="name">Nome / Empresa</label><input id="name" name="name" type="text" autocomplete="name" required>
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required>
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required>
<label for="confirm_password">Confirmar password</label><input id="confirm_password" name="confirm_password" type="password" autocomplete="new-password" minlength="8" required>
<button type="submit">Criar conta</button></form>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<a class="link" href="/login">Já tenho uma conta — Entrar</a><div class="footer">PMW Consultoria & Tecnologia</div>
</main></body></html>
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
        <p>Entre na sua conta para continuar</p>
        <form method="post" action="/login">
            <label for="username">Username</label>
            <input id="username" name="username" type="text"
                   autocomplete="username" required autofocus>

            <label for="password">Password</label>
            <input id="password" name="password" type="password"
                   autocomplete="current-password" required>

            <button type="submit">Entrar</button>
        </form>
        <a href="/signup" style="display:block;text-align:center;margin-top:18px;color:#38bdf8;text-decoration:none">Criar nova conta — Trial 30 dias</a>
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


ACCOUNT_PAGE = """<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Minha Conta — PMW Social Media Dashboard AI</title>
<style>body{margin:0;background:#020617;color:#f8fafc;font-family:Arial,sans-serif}.wrap{max-width:1050px;margin:35px auto;padding:20px}.card{background:#111827;border:1px solid #1e293b;border-radius:18px;padding:24px;margin-bottom:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}label{display:block;margin:12px 0 7px;font-weight:600}input,select{width:100%;padding:12px;border-radius:9px;border:1px solid #334155;background:#020617;color:#fff;box-sizing:border-box}button,.btn{display:inline-block;padding:11px 16px;border:0;border-radius:9px;background:#38bdf8;color:#020617;font-weight:700;text-decoration:none;cursor:pointer;margin-top:12px}.danger{background:#ef4444;color:#fff}.muted{color:#94a3b8}.ok{color:#86efac}.err{color:#fca5a5}.tag{display:inline-block;padding:5px 9px;border-radius:999px;background:#172554;color:#7dd3fc;font-size:12px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px;border-bottom:1px solid #1e293b}</style></head><body><div class="wrap">
<div class="card"><a class="btn" href="/">← Dashboard</a> <a class="btn" href="/account#social-connections" style="background:#a855f7;color:#fff">🔗 Conectar Redes Sociais</a> <a class="btn" href="/billing">💳 Plano</a> <a class="btn danger" href="/logout">Sair</a><h1>👤 Minha Conta</h1><p class="muted">Gerencie o seu perfil, password e contas sociais.</p>{% if message %}<p class="ok">{{ message }}</p>{% endif %}{% if error %}<p class="err">{{ error }}</p>{% endif %}</div><div id="social-connections" class="card" style="border:2px solid #38bdf8;background:linear-gradient(135deg,#0f172a,#082f49)"><h2 style="margin-top:0">🔗 Conectar Redes Sociais</h2><p class="muted">Ligue as suas redes sociais através da autenticação oficial. O cliente será redirecionado para cada plataforma, fará login e autorizará o acesso. Nunca introduza password, token, App ID ou App Secret neste dashboard.</p><div class="grid"><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>📷 Instagram</h3><p class="muted">Instagram Business / Creator</p><a class="btn" href="/oauth/instagram">📷 Conectar Instagram</a></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>📘 Facebook</h3><p class="muted">Facebook Pages</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>💬 WhatsApp</h3><p class="muted">WhatsApp Business Platform</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>🎵 TikTok</h3><p class="muted">TikTok for Business</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>▶️ YouTube</h3><p class="muted">YouTube / Google</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>💼 LinkedIn</h3><p class="muted">LinkedIn Pages</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>🐦 X</h3><p class="muted">X / Twitter</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div></div></div>
<div class="grid"><div class="card"><h2>Perfil</h2><form method="post" action="/account"><label>Nome</label><input name="name" value="{{ user_name }}" required><label>Email</label><input value="{{ email }}" disabled><label>Workspace</label><input value="{{ workspace_name }}" disabled><label>Plano</label><p><span class="tag">{{ plan_name }}</span> {% if days_left %}<span class="muted">{{ days_left }} dias de trial</span>{% endif %}</p><button type="submit">Guardar perfil</button></form></div>
<div class="card"><h2>Alterar password</h2><form method="post" action="/account/password"><label>Password atual</label><input name="current_password" type="password" required><label>Nova password</label><input name="new_password" type="password" minlength="8" required><label>Confirmar nova password</label><input name="confirm_password" type="password" minlength="8" required><button type="submit">Alterar password</button></form></div></div>
<div class="card" style="border:2px solid #a855f7;background:#111827">
<h2>➕ Adicionar Rede Social</h2>
<p class="muted">Selecione a plataforma. A autenticação será feita na plataforma oficial; não introduza passwords ou tokens neste dashboard.</p>
<form method="post" action="/social-accounts/add" style="display:grid;grid-template-columns:1fr auto;gap:12px;align-items:end">
<div><label>Rede Social</label>
<select name="platform" required>
<option value="">— Selecionar plataforma —</option>
<option value="Instagram">📷 Instagram</option>
<option value="Facebook">📘 Facebook</option>
<option value="WhatsApp">💬 WhatsApp</option>
<option value="TikTok">🎵 TikTok</option>
<option value="YouTube">▶️ YouTube</option>
<option value="LinkedIn">💼 LinkedIn</option>
<option value="X">🐦 X (Twitter)</option>
</select></div>
<div><button type="submit">🔗 Continuar</button></div>
</form>
</div>
<div class="card"><h2>🔗 Contas Sociais</h2><p class="muted">Ligue as suas redes sociais através de autenticação oficial. Não é necessário inserir tokens ou credenciais no dashboard.</p>
<div style="background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:18px;margin:16px 0"><h3 style="margin-top:0">📷 Instagram</h3><p class="muted">O cliente será redirecionado para o Instagram, fará login e autorizará o acesso. O token é armazenado cifrado no servidor.</p><a class="btn" href="/oauth/instagram">📷 Conectar Instagram</a></div>
{% if social_accounts %}<table><thead><tr><th>Plataforma</th><th>Conta</th><th>Estado</th><th>Ação</th></tr></thead><tbody>{% for item in social_accounts %}<tr><td>{{ item.platform }}</td><td>{{ item.display_name or item.username }}</td><td>{{ "🟢 Conectada" if item.connection_status == "connected" and item.is_active else ("🟡 Ativa / Pendente" if item.is_active else "⚪ Desativada") }}</td><td><form method="post" action="/social-accounts/{{ item.id }}/toggle"><button type="submit" class="{{ 'danger' if item.is_active else '' }}">{{ "Desativar" if item.is_active else "Ativar" }}</button></form></td></tr>{% endfor %}</tbody></table>{% else %}<p class="muted">Ainda não existem contas sociais conectadas neste workspace.</p>{% endif %}</div></div></body></html>"""

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
                access = evaluate_workspace_access(conn, session.get("workspace_slug"))
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

    @server.after_request
    def security_headers(response):
        # Páginas autenticadas nunca devem ser reutilizadas pelo cache do browser.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @server.get("/logout")
    def logout():
        # Invalida completamente a sessão atual e remove o cookie no browser.
        session.clear()
        response = redirect(url_for("login", logged_out="1"))
        response.delete_cookie(
            server.config.get("SESSION_COOKIE_NAME", "session"),
            path=server.config.get("SESSION_COOKIE_PATH", "/"),
            domain=server.config.get("SESSION_COOKIE_DOMAIN"),
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

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
                    return redirect("/")
                return redirect(url_for("billing"))
            except Exception as erro:
                print(f"[BILLING LOGIN ERROR] {erro}")
                return redirect(url_for("billing"))

        error = None

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            valid = False
            user_workspace_slug = None

            configured_username = os.getenv("ADMIN_USERNAME", "").strip()
            password_hash = os.getenv("ADMIN_PASSWORD_HASH", "").strip()

            if configured_username and password_hash:
                try:
                    valid = (
                        username == configured_username
                        and bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
                    )
                except (ValueError, TypeError):
                    valid = False

            if valid:
                user_workspace_slug = os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
            else:
                try:
                    conn = get_connection()
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                """SELECT email, password_hash, workspace_id
                                   FROM users
                                   WHERE LOWER(email) = LOWER(%s)
                                     AND COALESCE(is_active, TRUE) = TRUE
                                   LIMIT 1;""",
                                (username,),
                            )
                            row = cur.fetchone()
                            if row and row[1]:
                                valid = bcrypt.checkpw(password.encode("utf-8"), row[1].encode("utf-8"))
                                if valid and row[2]:
                                    cur.execute("SELECT slug FROM workspaces WHERE id = %s LIMIT 1;", (row[2],))
                                    workspace_row = cur.fetchone()
                                    if workspace_row:
                                        user_workspace_slug = workspace_row[0]
                    finally:
                        conn.close()
                except Exception as erro:
                    print(f"[LOGIN DATABASE ERROR] {erro}")
                    valid = False

            if valid:
                session.clear()
                session["authenticated"] = True
                session["username"] = username
                session["workspace_slug"] = user_workspace_slug or os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
                session.permanent = True
                return redirect("/")

            error = "Email/username ou password inválido."

        return render_template_string(LOGIN_PAGE, error=error)

    @server.route("/signup", methods=["GET", "POST"])
    def signup():
        if session.get("authenticated"):
            return redirect("/")

        error = None
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not name or not email or not password:
                error = "Preencha todos os campos obrigatórios."
            elif len(password) < 8:
                error = "A password deve ter pelo menos 8 caracteres."
            elif password != confirm_password:
                error = "As passwords não coincidem."
            else:
                conn = None
                try:
                    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                    base_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "workspace"
                    workspace_slug = f"{base_slug}-{uuid.uuid4().hex[:8]}"

                    conn = get_connection()
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1;", (email,))
                        if cur.fetchone():
                            error = "Este email já está registado."
                        else:
                            cur.execute(
                                """INSERT INTO workspaces
                                   (name, slug, trial_started_at, trial_ends_at, subscription_status, subscription_plan)
                                   VALUES (%s, %s, NOW(), NOW() + INTERVAL '30 days', 'trialing', 'trial')
                                   RETURNING id, slug;""",
                                (f"{name} Workspace", workspace_slug),
                            )
                            workspace_id, workspace_slug = cur.fetchone()
                            cur.execute(
                                """INSERT INTO users
                                   (name, email, password_hash, plan_type, workspace_id, role, is_active)
                                   VALUES (%s, %s, %s, 'free', %s, 'owner', TRUE);""",
                                (name, email, password_hash, workspace_id),
                            )
                            conn.commit()
                            session.clear()
                            session["authenticated"] = True
                            session["username"] = email
                            session["workspace_slug"] = workspace_slug
                            session.permanent = True
                            return redirect("/")
                except Exception as erro:
                    if conn:
                        conn.rollback()
                    print(f"[SIGNUP ERROR] {erro}")
                    error = "Não foi possível criar a conta. Tente novamente."
                finally:
                    if conn:
                        conn.close()

        return render_template_string(SIGNUP_PAGE, error=error)

    @server.get("/oauth/instagram")
    def instagram_oauth():
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        if not _oauth_configured():
            return redirect(
                url_for(
                    "account",
                    error="Instagram OAuth ainda não está configurado no servidor. Verifique as variáveis de ambiente.",
                )
            )

        try:
            state = secrets.token_urlsafe(32)
            session["instagram_oauth_state"] = state
            session["instagram_oauth_workspace"] = session.get("workspace_slug")
            session.modified = True

            authorize_url = _instagram_authorize_url(state)
            print(
                "[INSTAGRAM OAUTH START] "
                f"workspace={session.get('workspace_slug')} "
                f"redirect_uri={os.getenv('INSTAGRAM_OAUTH_REDIRECT_URI')}"
            )
            return redirect(authorize_url)

        except Exception as erro:
            print(f"[INSTAGRAM OAUTH START ERROR] {type(erro).__name__}: {erro}")
            return redirect(
                url_for(
                    "account",
                    error=f"Não foi possível iniciar o Instagram OAuth: {type(erro).__name__}.",
                )
            )

    @server.get("/oauth/instagram/callback")
    def instagram_oauth_callback():
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        if request.args.get("error"):
            return redirect(url_for("account", error="A autorização do Instagram foi cancelada ou recusada."))
        state = request.args.get("state", "")
        expected = session.pop("instagram_oauth_state", None)
        workspace_slug = session.pop("instagram_oauth_workspace", None)
        if not state or not expected or not secrets.compare_digest(state, expected):
            return redirect(url_for("account", error="Falha de segurança no estado OAuth. Tente novamente."))
        code = request.args.get("code", "")
        if not code or workspace_slug != session.get("workspace_slug"):
            return redirect(url_for("account", error="Código OAuth inválido ou sessão expirada."))
        conn = None
        try:
            token_response = requests.post(
                "https://api.instagram.com/oauth/access_token",
                data={
                    "client_id": os.getenv("INSTAGRAM_APP_ID"),
                    "client_secret": os.getenv("INSTAGRAM_APP_SECRET"),
                    "grant_type": "authorization_code",
                    "redirect_uri": os.getenv("INSTAGRAM_OAUTH_REDIRECT_URI"),
                    "code": code,
                },
                timeout=15,
            )
            token_response.raise_for_status()
            short_data = token_response.json()
            short_token = short_data.get("access_token")
            if not short_token:
                raise RuntimeError("Instagram não devolveu access_token.")

            long_response = requests.get(
                "https://graph.instagram.com/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": os.getenv("INSTAGRAM_APP_SECRET"),
                    "access_token": short_token,
                },
                timeout=15,
            )
            long_response.raise_for_status()
            long_data = long_response.json()
            access_token = long_data.get("access_token", short_token)

            profile_response = requests.get(
                "https://graph.instagram.com/v25.0/me",
                params={"fields": "id,username,account_type", "access_token": access_token},
                timeout=15,
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
            ig_id = str(profile.get("id", ""))
            username = profile.get("username") or ("instagram_" + ig_id)
            account_type = str(profile.get("account_type", "")).upper()
            if not ig_id:
                raise RuntimeError("Não foi possível obter o ID do Instagram.")
            if account_type and account_type not in {"BUSINESS", "CREATOR"}:
                raise RuntimeError(
                    "A conta Instagram precisa ser profissional (Business ou Creator)."
                )

            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT id, subscription_plan FROM workspaces WHERE slug=%s LIMIT 1", (workspace_slug,))
                workspace = cur.fetchone()
                if not workspace:
                    raise RuntimeError("Workspace não encontrado.")

                plan_key = workspace[1] or "trial"
                account_limit = PLANS.get(plan_key, PLANS["trial"])["accounts"]
                cur.execute(
                    """SELECT COUNT(*) FROM social_accounts
                       WHERE workspace_id=%s AND COALESCE(is_active, TRUE)=TRUE""",
                    (workspace[0],),
                )
                active_accounts = cur.fetchone()[0]
                cur.execute(
                    """SELECT id FROM social_accounts
                       WHERE workspace_id=%s AND platform='Instagram'
                         AND LOWER(username)=LOWER(%s) LIMIT 1""",
                    (workspace[0], username),
                )
                existing = cur.fetchone()

                # A renovação de uma conta já conectada não consome novo limite.
                # Uma nova conta só pode ser criada dentro do limite do plano.
                if not existing and isinstance(account_limit, int) and active_accounts >= account_limit:
                    raise RuntimeError(
                        f"O plano {PLANS.get(plan_key, PLANS['trial'])['name']} permite no máximo "
                        f"{account_limit} contas sociais ativas."
                    )

                encrypted = _encrypt_token(access_token)
                if existing:
                    cur.execute(
                        """UPDATE social_accounts
                           SET access_token=%s, display_name=%s,
                               connection_status='connected', is_active=TRUE,
                               last_sync_at=NOW()
                           WHERE id=%s AND workspace_id=%s""",
                        (encrypted, username, existing[0], workspace[0]),
                    )
                else:
                    cur.execute(
                        """INSERT INTO social_accounts
                           (workspace_id, platform, username, display_name,
                            access_token, connection_status, is_active, last_sync_at)
                           VALUES (%s,'Instagram',%s,%s,%s,'connected',TRUE,NOW())""",
                        (workspace[0], username, username, encrypted),
                    )
            conn.commit()
            return redirect(url_for("account", message="Instagram conectado com sucesso."))
        except (requests.RequestException, ValueError, InvalidToken) as erro:
            if conn: conn.rollback()
            print(f"[INSTAGRAM OAUTH ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível concluir a ligação ao Instagram."))
        except Exception as erro:
            if conn: conn.rollback()
            print(f"[INSTAGRAM OAUTH ERROR] {erro}")
            return redirect(url_for("account", error="Erro ao configurar a conta Instagram."))
        finally:
            if conn: conn.close()

    @server.route("/account", methods=["GET", "POST"])
    def account():
        if not session.get("authenticated"):
            return redirect("/login")

        message = request.args.get("message")
        error = request.args.get("error")
        conn = None

        try:
            conn = get_connection()
            slug = session.get("workspace_slug")

            with conn.cursor() as cur:
                if request.method == "POST":
                    name = request.form.get("name", "").strip()
                    if not name:
                        return redirect(url_for("account", error="O nome é obrigatório."))

                    cur.execute(
                        """UPDATE users
                           SET name=%s
                           WHERE LOWER(email)=LOWER(%s)
                             AND workspace_id=(SELECT id FROM workspaces WHERE slug=%s)""",
                        (name, session.get("username"), slug),
                    )
                    conn.commit()
                    return redirect(url_for("account", message="Perfil atualizado com sucesso."))

                cur.execute(
                    """SELECT u.name,u.email,w.name,w.subscription_plan,w.trial_ends_at
                       FROM users u
                       LEFT JOIN workspaces w ON w.id=u.workspace_id
                       WHERE LOWER(u.email)=LOWER(%s) AND w.slug=%s
                       LIMIT 1""",
                    (session.get("username"), slug),
                )
                user = cur.fetchone()

                cur.execute(
                    """SELECT id,platform,username,display_name,connection_status,is_active
                       FROM social_accounts
                       WHERE workspace_id=(SELECT id FROM workspaces WHERE slug=%s)
                       ORDER BY created_at DESC""",
                    (slug,),
                )
                accounts = [
                    {
                        "id": str(row[0]),
                        "platform": row[1],
                        "username": row[2],
                        "display_name": row[3],
                        "connection_status": row[4],
                        "is_active": row[5],
                    }
                    for row in cur.fetchall()
                ]

            if not user:
                session.clear()
                return redirect("/login")

            from datetime import datetime, timezone
            days_left = 0
            if user[4]:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                days_left = max(
                    0,
                    int(((user[4] - now).total_seconds() + 86399) // 86400),
                )

            return render_template_string(
                ACCOUNT_PAGE,
                user_name=user[0] or "",
                email=user[1] or session.get("username", ""),
                workspace_name=user[2] or slug,
                plan_name=PLANS.get(user[3] or "trial", PLANS["trial"])["name"],
                days_left=days_left,
                social_accounts=accounts,
                message=message,
                error=error,
            )

        except Exception as erro:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass

            import traceback
            print(f"[ACCOUNT ERROR] {type(erro).__name__}: {erro}")
            traceback.print_exc()

            return render_template_string(
                ACCOUNT_PAGE,
                user_name=session.get("username", ""),
                email=session.get("username", ""),
                workspace_name=session.get("workspace_slug", ""),
                plan_name="Trial",
                days_left=0,
                social_accounts=[],
                message=message,
                error="Não foi possível carregar os dados da conta. O erro foi registado no servidor.",
            ), 200

    @server.post("/account/password")
    def change_password():
        if not session.get("authenticated"): return redirect(url_for("login"))
        current=request.form.get("current_password",""); new=request.form.get("new_password",""); confirm=request.form.get("confirm_password","")
        if len(new)<8: return redirect(url_for("account",error="A nova password deve ter pelo menos 8 caracteres."))
        if new!=confirm: return redirect(url_for("account",error="As novas passwords não coincidem."))
        conn=None
        try:
            conn=get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE LOWER(email)=LOWER(%s) LIMIT 1",(session.get("username"),)); row=cur.fetchone()
                if not row or not row[0] or not bcrypt.checkpw(current.encode(),row[0].encode()): return redirect(url_for("account",error="A password atual está incorreta."))
                cur.execute("UPDATE users SET password_hash=%s WHERE LOWER(email)=LOWER(%s)",(bcrypt.hashpw(new.encode(),bcrypt.gensalt()).decode(),session.get("username")))
            conn.commit(); return redirect(url_for("account",message="Password alterada com sucesso."))
        except Exception as erro:
            if conn: conn.rollback()
            print(f"[PASSWORD ERROR] {erro}"); return redirect(url_for("account",error="Não foi possível alterar a password."))
        finally:
            if conn: conn.close()

    @server.post("/social-accounts/add")
    def add_social_account():
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        platform = request.form.get("platform", "").strip()
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip() or username

        if platform == "Instagram":
            return redirect(url_for("instagram_oauth"))

        # As integrações que ainda não têm OAuth implementado não devem
        # pedir username/token manualmente. O fluxo comercial deve sempre
        # encaminhar o cliente para a autenticação oficial da plataforma.
        coming_soon = {
            "Facebook": "Facebook OAuth",
            "WhatsApp": "WhatsApp Business",
            "TikTok": "TikTok OAuth",
            "YouTube": "YouTube / Google OAuth",
            "LinkedIn": "LinkedIn OAuth",
            "X": "X OAuth",
        }
        if platform in coming_soon:
            return redirect(
                url_for(
                    "account",
                    error=f"{coming_soon[platform]} ainda está em implementação. "
                          "Não introduza credenciais ou tokens manualmente.",
                )
            )

        return redirect(url_for("account", error="Plataforma social inválida."))

        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, subscription_plan FROM workspaces WHERE slug=%s LIMIT 1",
                    (session.get("workspace_slug"),),
                )
                workspace = cur.fetchone()
                if not workspace:
                    return redirect(url_for("account", error="Workspace não encontrado."))

                plan = workspace[1] or "trial"
                limit = PLANS.get(plan, PLANS["trial"])["accounts"]

                cur.execute(
                    """SELECT COUNT(*) FROM social_accounts
                       WHERE workspace_id=%s AND COALESCE(is_active, TRUE)""",
                    (workspace[0],),
                )
                count = cur.fetchone()[0]

                if isinstance(limit, int) and count >= limit:
                    plan_name = PLANS.get(plan, PLANS["trial"])["name"]
                    return redirect(
                        url_for(
                            "account",
                            error=f"O plano {plan_name} permite no máximo {limit} contas sociais ativas.",
                        )
                    )

                cur.execute(
                    """INSERT INTO social_accounts
                       (workspace_id, platform, username, display_name,
                        connection_status, is_active)
                       VALUES (%s, %s, %s, %s, 'pending', TRUE)""",
                    (workspace[0], platform, username, display_name),
                )

            conn.commit()
            return redirect(url_for("account", message=f"{platform} adicionada com sucesso."))
        except Exception as erro:
            if conn:
                conn.rollback()
            print(f"[SOCIAL ACCOUNT ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível adicionar a conta social."))
        finally:
            if conn:
                conn.close()

    @server.post("/social-accounts/<account_id>/toggle")
    def toggle_social_account(account_id):
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE social_accounts
                       SET is_active = NOT COALESCE(is_active, TRUE)
                       WHERE id=%s
                         AND workspace_id=(SELECT id FROM workspaces WHERE slug=%s)""",
                    (account_id, session.get("workspace_slug")),
                )
            conn.commit()
            return redirect(url_for("account"))
        except Exception as erro:
            if conn:
                conn.rollback()
            print(f"[SOCIAL TOGGLE ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível alterar o estado da conta."))
        finally:
            if conn:
                conn.close()

