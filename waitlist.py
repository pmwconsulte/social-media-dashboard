"""Public early-access waitlist for PMW Social Media Dashboard AI."""
import html
import re

from flask import Response, request

from database import get_connection


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _page(message="", success=False, email=""):
    safe_message = html.escape(message)
    safe_email = html.escape(email, quote=True)
    alert = ""
    if message:
        alert = (
            '<div class="notice ' + ("success" if success else "error") +
            '" role="status">' + safe_message + "</div>"
        )
    return """<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,follow">
<title>Acesso antecipado | PMW Social Media Dashboard AI</title>
<style>
:root{color-scheme:dark;--bg:#020617;--panel:#111827;--line:#263449;--text:#f8fafc;--muted:#a7b4c7;--blue:#38bdf8;--green:#22c55e}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at top,#0c2344 0,var(--bg) 60%);color:var(--text);font-family:Inter,Arial,sans-serif;line-height:1.6;display:grid;place-items:center;padding:24px}
main{width:min(650px,100%);background:rgba(17,24,39,.94);border:1px solid var(--line);border-radius:20px;padding:clamp(24px,5vw,46px);box-shadow:0 20px 70px #0005}
.brand{color:var(--blue);font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
h1{font-size:clamp(30px,5vw,44px);line-height:1.12;margin:12px 0 16px;letter-spacing:-.03em}
p{color:var(--muted)}label{display:block;font-weight:700;margin:18px 0 8px}
input[type=email]{width:100%;padding:14px;border:1px solid #456078;border-radius:10px;background:#020617;color:var(--text);font:inherit}
.consent{display:flex;align-items:flex-start;gap:10px;font-weight:400;font-size:14px;color:var(--muted)}
.consent input{margin-top:6px;flex:0 0 auto}
button,.back{display:inline-block;border:0;border-radius:10px;padding:13px 18px;font:inherit;font-weight:800;text-decoration:none;cursor:pointer}
button{background:var(--blue);color:#06111f;width:100%;margin-top:18px}
.back{border:1px solid #456078;color:var(--text);margin-top:18px}
.notice{border-radius:10px;padding:12px 14px;margin:18px 0}
.success{background:#052e25;border:1px solid #166534;color:#bbf7d0}.error{background:#451a1a;border:1px solid #991b1b;color:#fecaca}
.small{font-size:13px}.hp{position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden}
a{color:var(--blue)}button:focus-visible,input:focus-visible,a:focus-visible{outline:3px solid #7dd3fc;outline-offset:3px}
</style>
</head>
<body><main>
<div class="brand">PMW Consultoria · Acesso antecipado</div>
<h1>Seja dos primeiros a experimentar o PMW Dashboard AI.</h1>
<p>Entre na lista de espera para receber novidades sobre o acesso antecipado e um convite quando a sua participação estiver disponível.</p>
""" + alert + """
<form method="post" action="/waitlist">
<label for="email">Email</label>
<input id="email" name="email" type="email" autocomplete="email" maxlength="254" required value="""" + safe_email + """">
<div class="hp" aria-hidden="true"><label for="website">Não preencher</label><input id="website" name="website" tabindex="-1" autocomplete="off"></div>
<label class="consent"><input type="checkbox" name="consent" value="yes" required>
<span>Autorizo a PMW Consultoria a usar o meu email para confirmar esta inscrição e enviar informações sobre o acesso antecipado e novidades do PMW Social Media Dashboard AI. Posso retirar o consentimento a qualquer momento contactando <a href="mailto:info@pmwconsultoria.com?subject=Retirar%20consentimento%20da%20lista%20de%20espera">info@pmwconsultoria.com</a>.</span></label>
<button type="submit">Pedir acesso antecipado</button>
</form>
<p class="small">A inscrição não garante acesso imediato. As integrações e métricas dependem das aprovações, permissões e disponibilidade das APIs da Meta. O email será usado para gerir esta lista e as comunicações autorizadas.</p>
<a class="back" href="/home">Voltar ao site da PMW</a>
</main></body></html>"""


def register_waitlist(server):
    @server.route("/waitlist", methods=["GET", "POST"])
    def public_waitlist():
        if request.method == "GET":
            return Response(_page(), mimetype="text/html")

        # Honeypot to reject basic automated submissions.
        if request.form.get("website", "").strip():
            return Response(_page(
                "Não foi possível processar a inscrição. Tente novamente."
            ), status=400, mimetype="text/html")

        email = request.form.get("email", "").strip().lower()
        consent = request.form.get("consent") == "yes"
        if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
            return Response(_page(
                "Introduza um endereço de email válido.", email=email
            ), status=400, mimetype="text/html")
        if not consent:
            return Response(_page(
                "É necessário autorizar as comunicações sobre o acesso antecipado.",
                email=email
            ), status=400, mimetype="text/html")

        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS waitlist_signups (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        email VARCHAR(254) NOT NULL UNIQUE,
                        source VARCHAR(80) NOT NULL DEFAULT 'public_waitlist',
                        consent_at TIMESTAMPTZ NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """)
                cur.execute("""
                    INSERT INTO waitlist_signups (email, source, consent_at)
                    VALUES (%s, 'public_waitlist', NOW())
                    ON CONFLICT (email) DO UPDATE
                    SET consent_at = EXCLUDED.consent_at,
                        updated_at = NOW();
                """, (email,))
            conn.commit()
        except Exception as exc:
            if conn:
                conn.rollback()
            print(f"[WAITLIST] Falha ao guardar inscrição: {type(exc).__name__}")
            return Response(_page(
                "De momento não foi possível guardar a inscrição. Tente novamente mais tarde.",
                email=email
            ), status=503, mimetype="text/html")
        finally:
            if conn:
                conn.close()

        return Response(_page(
            "Inscrição registada. Obrigado pelo interesse! A PMW Consultoria poderá contactar-te sobre o acesso antecipado.",
            success=True
        ), mimetype="text/html")
