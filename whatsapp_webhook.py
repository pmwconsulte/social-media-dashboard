"""WhatsApp Cloud API webhook with signature validation and message persistence."""
import hashlib
import hmac
import json
import os

import requests
from flask import Response, request
from database import get_connection


def _secret():
    return (os.getenv("WHATSAPP_APP_SECRET", "").strip()
            or os.getenv("FACEBOOK_APP_SECRET", "").strip()
            or os.getenv("INSTAGRAM_APP_SECRET", "").strip())


def _signature_ok(raw):
    secret = _secret()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not secret or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _save_messages(payload):
    slug = os.getenv("WHATSAPP_WORKSPACE_SLUG", "pmw-consultoria-98fdcf3f").strip()
    conn = get_connection()
    saved = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM workspaces WHERE slug = %s LIMIT 1", (slug,))
            row = cur.fetchone()
            if not row:
                raise RuntimeError("Workspace WhatsApp não encontrado.")
            workspace_id = row[0]
            cur.execute("""
                CREATE TABLE IF NOT EXISTS whatsapp_messages (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                    message_id VARCHAR(255) NOT NULL UNIQUE,
                    phone_number_id VARCHAR(80),
                    sender_phone VARCHAR(80) NOT NULL,
                    sender_name VARCHAR(255),
                    message_type VARCHAR(40) NOT NULL DEFAULT 'unknown',
                    message_text TEXT,
                    message_timestamp TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS direction VARCHAR(20) NOT NULL DEFAULT 'inbound'")
            cur.execute("ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'received'")
            for entry in payload.get("entry", []) or []:
                if not isinstance(entry, dict):
                    continue
                for change in entry.get("changes", []) or []:
                    if not isinstance(change, dict) or change.get("field") != "messages":
                        continue
                    value = change.get("value") or {}
                    if not isinstance(value, dict):
                        continue
                    meta = value.get("metadata") or {}
                    phone_id = str(meta.get("phone_number_id") or "")[:80]
                    names = {}
                    for contact in value.get("contacts", []) or []:
                        if isinstance(contact, dict) and contact.get("wa_id"):
                            names[str(contact["wa_id"])] = str(
                                (contact.get("profile") or {}).get("name") or ""
                            )[:255]
                    for msg in value.get("messages", []) or []:
                        if not isinstance(msg, dict):
                            continue
                        msg_id = str(msg.get("id") or "").strip()
                        sender = str(msg.get("from") or "").strip()
                        if not msg_id or not sender:
                            continue
                        kind = str(msg.get("type") or "unknown")[:40]
                        body = None
                        if kind == "text":
                            body = str((msg.get("text") or {}).get("body") or "")[:10000]
                        stamp = str(msg.get("timestamp") or "")
                        cur.execute("""
                            INSERT INTO whatsapp_messages
                                (workspace_id, message_id, phone_number_id, sender_phone,
                                 sender_name, message_type, message_text, message_timestamp)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,
                                CASE WHEN %s ~ '^[0-9]{1,12}$'
                                THEN to_timestamp(%s::double precision) ELSE NOW() END)
                            ON CONFLICT (message_id) DO NOTHING
                        """, (workspace_id, msg_id, phone_id or None, sender[:80],
                              names.get(sender) or None, kind, body, stamp, stamp))
                        saved += cur.rowcount
        conn.commit()
        return saved
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def send_whatsapp_text(phone_number_id, recipient, message_text):
    """Send a customer-service text through the configured WhatsApp Cloud API."""
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN não está configurado.")
    phone_id = str(phone_number_id or "").strip()
    if not phone_id:
        raise RuntimeError("O número de telefone WhatsApp não está identificado.")
    recipient = "".join(ch for ch in str(recipient or "") if ch.isdigit())
    if not recipient or len(recipient) < 8 or len(recipient) > 15:
        raise ValueError("Número de destino inválido.")
    text = str(message_text or "").strip()
    if not text or len(text) > 4096:
        raise ValueError("A mensagem deve ter entre 1 e 4096 caracteres.")

    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip() or "v25.0"
    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if not response.ok:
        error = payload.get("error") or {}
        code = error.get("code")
        subcode = error.get("error_subcode")
        error_type = error.get("type")
        message = str(error.get("message") or "A Meta recusou o envio.")
        error_data = error.get("error_data") or {}
        details = error_data.get("details") or error_data.get("blame_field_specs")
        trace_id = error.get("fbtrace_id")

        diagnostic = {
            "phone_number_id": phone_id,
            "http_status": response.status_code,
            "code": code,
            "error_subcode": subcode,
            "type": error_type,
            "message": message,
            "details": details,
            "fbtrace_id": trace_id,
        }
        print("[WHATSAPP GRAPH API ERROR] " + json.dumps(
            diagnostic, ensure_ascii=False, default=str
        ))

        parts = [f"WhatsApp API ({code})" if code is not None else "WhatsApp API"]
        if subcode is not None:
            parts.append(f"subcode {subcode}")
        if error_type:
            parts.append(str(error_type))
        parts.append(message)
        if details:
            parts.append(f"Detalhes: {details}")
        if trace_id:
            parts.append(f"fbtrace_id: {trace_id}")
        raise RuntimeError(" — ".join(parts))

    messages = payload.get("messages") or []
    message_id = (messages[0] or {}).get("id") if messages else None
    if not message_id:
        raise RuntimeError("A API não confirmou o identificador da mensagem enviada.")
    return message_id



def send_whatsapp_template(phone_number_id, recipient, template_name, language_code="pt_PT"):
    """Send an approved WhatsApp template; caller must enforce approval status."""
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN não está configurado.")
    phone_id = str(phone_number_id or "").strip()
    if not phone_id:
        raise RuntimeError("O número de telefone WhatsApp não está identificado.")
    recipient = "".join(ch for ch in str(recipient or "") if ch.isdigit())
    if not recipient or len(recipient) < 8 or len(recipient) > 15:
        raise ValueError("Número de destino inválido.")
    name = str(template_name or "").strip()
    language = str(language_code or "").strip()
    if not name or not language:
        raise ValueError("O modelo e o idioma são obrigatórios.")

    version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip() or "v25.0"
    response = requests.post(
        f"https://graph.facebook.com/{version}/{phone_id}/messages",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "template",
            "template": {"name": name, "language": {"code": language}},
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        error = payload.get("error") or {}
        detail = str(error.get("message") or "A Meta recusou o envio do modelo.")
        code = error.get("code")
        trace_id = error.get("fbtrace_id")
        print("[WHATSAPP TEMPLATE ERROR] " + json.dumps({
            "phone_number_id": phone_id, "http_status": response.status_code,
            "code": code, "message": detail[:250], "fbtrace_id": trace_id,
        }, ensure_ascii=False))
        raise RuntimeError(f"WhatsApp API ({code}): {detail}" if code is not None else detail)

    messages = payload.get("messages") or []
    message_id = (messages[0] or {}).get("id") if messages else None
    if not message_id:
        raise RuntimeError("A API não confirmou o identificador da mensagem modelo.")
    return message_id


def register_whatsapp_webhooks(server):
    @server.route("/webhooks/whatsapp", methods=["GET", "POST"])
    def whatsapp_webhook():
        if request.method == "GET":
            verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
            mode = request.args.get("hub.mode", "")
            token = request.args.get("hub.verify_token", "")
            challenge = request.args.get("hub.challenge", "")
            if not verify_token:
                return Response("Webhook verification is not configured.", status=503, mimetype="text/plain")
            if mode == "subscribe" and token and hmac.compare_digest(token, verify_token) and challenge:
                return Response(challenge, status=200, mimetype="text/plain")
            return Response("Forbidden", status=403, mimetype="text/plain")

        raw = request.get_data(cache=True)
        if not _signature_ok(raw):
            return Response("Invalid webhook signature", status=401, mimetype="text/plain")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response("Invalid JSON", status=400, mimetype="text/plain")
        if not isinstance(payload, dict):
            return Response("Invalid payload", status=400, mimetype="text/plain")
        try:
            count = _save_messages(payload)
            print(f"[WHATSAPP WEBHOOK] Evento confirmado; mensagens novas: {count}.")
        except Exception as exc:
            print(f"[WHATSAPP WEBHOOK] Falha ao guardar evento: {type(exc).__name__}.")
            return Response("Webhook processing failed", status=500, mimetype="text/plain")
        return Response("EVENT_RECEIVED", status=200, mimetype="text/plain")
