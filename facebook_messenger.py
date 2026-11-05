"""Facebook Messenger inbox: webhook persistence and workspace-isolated replies."""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from html import escape

import requests
from flask import Response, redirect, request, session, url_for

from database import get_connection
from auth import _token_cipher


def _graph_version():
    return os.getenv("FACEBOOK_GRAPH_API_VERSION", "v25.0").strip() or "v25.0"


def _verify_signature(raw):
    secret = (os.getenv("FACEBOOK_APP_SECRET", "").strip()
              or os.getenv("INSTAGRAM_APP_SECRET", "").strip())
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not secret or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _decrypt_token(encrypted):
    return _token_cipher().decrypt(str(encrypted).encode("utf-8")).decode("utf-8")


def _ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS facebook_messenger_messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            social_account_id UUID NOT NULL REFERENCES social_accounts(id) ON DELETE CASCADE,
            page_id VARCHAR(255) NOT NULL,
            message_id VARCHAR(255) NOT NULL UNIQUE,
            sender_psid VARCHAR(255) NOT NULL,
            message_text TEXT,
            message_type VARCHAR(40) NOT NULL DEFAULT 'text',
            message_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            direction VARCHAR(20) NOT NULL DEFAULT 'inbound',
            status VARCHAR(30) NOT NULL DEFAULT 'received',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_fb_messenger_workspace_time
        ON facebook_messenger_messages(workspace_id, message_timestamp DESC)
    """)


def _save_webhook(payload):
    saved = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            for entry in payload.get("entry", []) or []:
                if not isinstance(entry, dict):
                    continue
                page_id = str(entry.get("id") or "").strip()
                if not page_id:
                    continue
                cur.execute("""
                    SELECT sa.id, sa.workspace_id
                    FROM social_accounts sa
                    WHERE sa.username=%s AND LOWER(sa.platform)='facebook'
                      AND COALESCE(sa.is_active, TRUE)=TRUE
                      AND LOWER(COALESCE(sa.connection_status,''))='connected'
                    LIMIT 1
                """, (page_id,))
                account = cur.fetchone()
                if not account:
                    continue
                social_account_id, workspace_id = account
                for event in entry.get("messaging", []) or []:
                    if not isinstance(event, dict):
                        continue
                    sender = event.get("sender") or {}
                    message = event.get("message") or {}
                    psid = str(sender.get("id") or "").strip()
                    mid = str(message.get("mid") or "").strip()
                    if not psid or not mid or message.get("is_echo"):
                        continue
                    text = message.get("text")
                    kind = "text" if isinstance(text, str) else "attachment"
                    if not isinstance(text, str):
                        attachments = message.get("attachments") or []
                        kind = str((attachments[0] or {}).get("type") or "attachment")[:40] if attachments else "non_text"
                        text = "[Mensagem sem texto]"
                    stamp = event.get("timestamp")
                    try:
                        message_time = datetime.fromtimestamp(int(stamp) / 1000, tz=timezone.utc)
                    except (TypeError, ValueError, OverflowError):
                        message_time = datetime.now(timezone.utc)
                    cur.execute("""
                        INSERT INTO facebook_messenger_messages
                            (workspace_id, social_account_id, page_id, message_id,
                             sender_psid, message_text, message_type, message_timestamp,
                             direction, status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'inbound','received')
                        ON CONFLICT (message_id) DO NOTHING
                    """, (workspace_id, social_account_id, page_id, mid, psid,
                          str(text)[:10000], kind, message_time))
                    saved += cur.rowcount
        conn.commit()
        return saved
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _workspace_account(workspace_slug, account_id=None):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            cur.execute("""
                SELECT sa.id, sa.username, sa.display_name, sa.access_token, sa.workspace_id
                FROM social_accounts sa
                JOIN workspaces w ON w.id=sa.workspace_id
                WHERE w.slug=%s AND LOWER(sa.platform)='facebook'
                  AND COALESCE(sa.is_active, TRUE)=TRUE
                  AND LOWER(COALESCE(sa.connection_status,''))='connected'
                  AND (%s='' OR sa.id::text=%s)
                ORDER BY sa.last_sync_at DESC NULLS LAST
                LIMIT 1
            """, (workspace_slug, str(account_id or ""), str(account_id or "")))
            return cur.fetchone()
    finally:
        conn.close()


def _api_error(response):
    try:
        error = response.json().get("error", {})
        message = str(error.get("message") or "A Meta recusou o pedido.")[:350]
        code = error.get("code")
        return f"Meta Graph API HTTP {response.status_code}" + (f" (código {code})" if code is not None else "") + f": {message}"
    except (ValueError, AttributeError):
        return f"Meta Graph API HTTP {response.status_code}."


def _send_reply(account, psid, message_text):
    social_account_id, page_id, page_name, encrypted_token, workspace_id = account
    token = _decrypt_token(encrypted_token)
    response = requests.post(
        f"https://graph.facebook.com/{_graph_version()}/{page_id}/messages",
        params={"access_token": token},
        json={
            "recipient": {"id": psid},
            "messaging_type": "RESPONSE",
            "message": {"text": message_text},
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(_api_error(response))
    payload = response.json()
    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        raise RuntimeError("A Meta não confirmou o identificador da mensagem enviada.")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_table(cur)
            cur.execute("""
                INSERT INTO facebook_messenger_messages
                    (workspace_id, social_account_id, page_id, message_id,
                     sender_psid, message_text, message_type, message_timestamp,
                     direction, status)
                VALUES (%s,%s,%s,%s,%s,%s,'text',NOW(),'outbound','sent')
                ON CONFLICT (message_id) DO NOTHING
            """, (workspace_id, social_account_id, page_id, message_id, psid, message_text))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _subscribe_page(account):
    social_account_id, page_id, page_name, encrypted_token, workspace_id = account
    token = _decrypt_token(encrypted_token)
    response = requests.post(
        f"https://graph.facebook.com/{_graph_version()}/{page_id}/subscribed_apps",
        params={
            "subscribed_fields": "messages,messaging_postbacks",
            "access_token": token,
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(_api_error(response))
    return response.json()


def register_facebook_messenger(server):
    @server.route("/webhooks/messenger", methods=["GET", "POST"])
    def facebook_messenger_webhook():
        if request.method == "GET":
            verify_token = os.getenv("FACEBOOK_MESSENGER_VERIFY_TOKEN", "").strip()
            mode = request.args.get("hub.mode", "")
            token = request.args.get("hub.verify_token", "")
            challenge = request.args.get("hub.challenge", "")
            if not verify_token:
                return Response("Messenger webhook verification is not configured.", status=503)
            if mode == "subscribe" and token and hmac.compare_digest(token, verify_token) and challenge:
                return Response(challenge, status=200, mimetype="text/plain")
            return Response("Forbidden", status=403, mimetype="text/plain")

        raw = request.get_data(cache=True)
        if not _verify_signature(raw):
            return Response("Invalid webhook signature", status=401, mimetype="text/plain")
        try:
            payload = request.get_json(force=False, silent=False)
        except Exception:
            return Response("Invalid JSON", status=400, mimetype="text/plain")
        if not isinstance(payload, dict) or payload.get("object") != "page":
            return Response("Ignored", status=200, mimetype="text/plain")
        try:
            saved = _save_webhook(payload)
            print(f"[FACEBOOK MESSENGER WEBHOOK] Novas mensagens guardadas: {saved}.")
        except Exception as exc:
            print(f"[FACEBOOK MESSENGER WEBHOOK] Falha ao guardar evento: {type(exc).__name__}.")
            return Response("Webhook processing failed", status=500, mimetype="text/plain")
        return Response("EVENT_RECEIVED", status=200, mimetype="text/plain")

    @server.route("/facebook/messenger/inbox", methods=["GET", "POST"])
    def facebook_messenger_inbox():
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        workspace_slug = (session.get("workspace_slug") or "").strip()
        requested_account = request.values.get("account", "").strip()
        conn = None
        try:
            account = _workspace_account(workspace_slug, requested_account)
            if not account:
                return Response(
                    "<h2>Facebook Messenger</h2><p>Não existe uma Página Facebook ligada neste workspace.</p><a href='/account'>Voltar</a>",
                    status=200, mimetype="text/html",
                )
            account_id, page_id, page_name, encrypted_token, workspace_id = account
            csrf = session.setdefault("facebook_messenger_csrf", os.urandom(32).hex())
            if request.method == "POST":
                expected = session.get("facebook_messenger_csrf", "")
                received = request.form.get("csrf_token", "")
                if not expected or not received or not hmac.compare_digest(expected, received):
                    return Response("Pedido inválido ou expirado. Actualize a página.", status=400)
                action = request.form.get("action", "")
                if action == "subscribe":
                    result = _subscribe_page(account)
                    if result.get("success") is not True:
                        raise RuntimeError("A Meta não confirmou a subscrição da Página ao webhook.")
                    return redirect(url_for("facebook_messenger_inbox", account=str(account_id), message="Webhook da Página activado."))
                if action == "reply":
                    psid = "".join(ch for ch in (request.form.get("recipient") or "") if ch.isdigit())
                    message_text = (request.form.get("message") or "").strip()
                    if not psid or not message_text or len(message_text) > 2000:
                        return Response("Indique um contacto e uma mensagem entre 1 e 2.000 caracteres.", status=400)
                    conn = get_connection()
                    try:
                        with conn.cursor() as cur:
                            _ensure_table(cur)
                            cur.execute("""
                                SELECT MAX(message_timestamp)
                                FROM facebook_messenger_messages
                                WHERE workspace_id=%s AND social_account_id=%s
                                  AND sender_psid=%s AND direction='inbound'
                            """, (workspace_id, account_id, psid))
                            last_inbound = cur.fetchone()[0]
                        if not last_inbound or last_inbound < datetime.now(timezone.utc) - timedelta(hours=24):
                            return Response("A janela de atendimento de 24 horas terminou. Não é possível enviar esta resposta livre.", status=400)
                    finally:
                        conn.close()
                    _send_reply(account, psid, message_text)
                    return redirect(url_for("facebook_messenger_inbox", account=str(account_id), q=psid, message="Resposta enviada."))
                return Response("Acção desconhecida.", status=400)

            term = (request.args.get("q") or "").strip()[:120]
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    _ensure_table(cur)
                    cur.execute("""
                        SELECT sender_psid, message_text, message_type, message_timestamp,
                               direction, status
                        FROM facebook_messenger_messages
                        WHERE workspace_id=%s AND social_account_id=%s
                          AND (%s='' OR sender_psid ILIKE %s OR COALESCE(message_text,'') ILIKE %s)
                        ORDER BY message_timestamp DESC
                        LIMIT 100
                    """, (workspace_id, account_id, term, f"%{term}%", f"%{term}%"))
                    rows = cur.fetchall()
            finally:
                conn.close()

            cards = []
            for psid, body, kind, stamp, direction, status in rows:
                safe_psid = escape(str(psid or ""))
                safe_body = escape(str(body or "Mensagem sem texto"))
                date_label = escape(stamp.strftime("%d/%m/%Y %H:%M") if stamp else "Data indisponível")
                direction_label = "Enviada" if direction == "outbound" else "Recebida"
                reply_form = ""
                if direction == "inbound" and stamp and stamp >= datetime.now(timezone.utc) - timedelta(hours=24):
                    reply_form = (
                        "<form method='post' class='reply'>"
                        f"<input type='hidden' name='csrf_token' value='{escape(csrf)}'>"
                        f"<input type='hidden' name='account' value='{escape(str(account_id))}'>"
                        f"<input type='hidden' name='recipient' value='{safe_psid}'>"
                        "<input type='hidden' name='action' value='reply'>"
                        "<textarea name='message' maxlength='2000' required placeholder='Escreva uma resposta...'></textarea>"
                        "<button type='submit'>Enviar resposta</button></form>"
                        "<small>Resposta livre disponível dentro das 24 horas após a última mensagem do cliente.</small>"
                    )
                cards.append(
                    "<article class='message'><div class='top'><strong>Contacto Messenger</strong>"
                    f"<span>{date_label}</span></div><div class='psid'>PSID: {safe_psid}</div>"
                    f"<p>{safe_body}</p><small>{direction_label} · {escape(str(kind))} · {escape(str(status))}</small>{reply_form}</article>"
                )
            listing = "".join(cards) or "<div class='empty'>Ainda não existem mensagens Messenger nesta Página.</div>"
            notice = escape(request.args.get("message", ""))
            page = f"""<!doctype html><html lang="pt"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Facebook Messenger | PMW Dashboard</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#020617;color:#f8fafc;font-family:Arial,sans-serif}}
main{{max-width:1050px;margin:0 auto;padding:28px 18px}}header{{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:22px}}
h1{{font-size:25px;margin:0}}a{{color:#38bdf8;text-decoration:none;font-weight:bold}}.muted{{color:#94a3b8;margin-top:7px}}
.panel{{background:#111827;border:1px solid #263449;border-radius:15px;padding:19px}}
.message{{padding:15px 2px;border-bottom:1px solid #263449;overflow-wrap:anywhere}}.top{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}}
.top span,.psid,small{{font-size:12px;color:#94a3b8}}.psid{{margin-top:5px}}.message p{{white-space:pre-wrap;line-height:1.5;color:#e2e8f0}}
.reply{{display:flex;gap:9px;margin:12px 0 5px}}textarea{{flex:1;min-width:0;min-height:48px;padding:10px;border:1px solid #334155;border-radius:8px;background:#081426;color:#fff;font:inherit}}
button{{border:0;border-radius:8px;padding:11px 16px;background:#2563eb;color:#fff;font-weight:bold;cursor:pointer}}
.tools{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}}input[type=search]{{padding:11px;border:1px solid #334155;border-radius:8px;background:#081426;color:#fff;min-width:220px}}
.empty{{padding:35px;text-align:center;color:#94a3b8}}.notice{{color:#86efac;margin:10px 0}}
</style></head><body><main><header><div><h1>Facebook Messenger — Caixa de Entrada</h1>
<div class="muted">{escape(str(page_name or page_id))} · Últimas 100 mensagens</div></div><a href="/">← Voltar ao Dashboard</a></header>
<section class="panel"><div class="notice">{notice}</div>
<form method="post" class="tools"><input type="hidden" name="csrf_token" value="{escape(csrf)}">
<input type="hidden" name="account" value="{escape(str(account_id))}"><input type="hidden" name="action" value="subscribe">
<button type="submit">Activar webhook desta Página</button></form>
<form method="get" class="tools"><input type="hidden" name="account" value="{escape(str(account_id))}">
<input type="search" name="q" value="{escape(term)}" placeholder="Pesquisar por PSID ou mensagem"><button type="submit">Pesquisar</button></form>
{listing}</section></main></body></html>"""
            return Response(page, status=200, mimetype="text/html")
        except requests.RequestException as exc:
            print(f"[FACEBOOK MESSENGER API] {type(exc).__name__}.")
            return Response("Não foi possível comunicar com a Meta. Confirme as permissões pages_messaging e pages_manage_metadata.", status=502)
        except Exception as exc:
            print(f"[FACEBOOK MESSENGER INBOX] {type(exc).__name__}: {str(exc)[:300]}")
            return Response("Não foi possível abrir a Inbox Facebook Messenger. Verifique a configuração e os logs.", status=500)
