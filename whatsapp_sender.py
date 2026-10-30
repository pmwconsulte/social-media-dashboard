"""Send WhatsApp Cloud API text messages from the authenticated PMW workspace."""
import os
import re
import secrets

import requests
from flask import Response, redirect, request, session
from database import get_connection


def _workspace_id(cur, slug):
    cur.execute("SELECT id FROM workspaces WHERE slug=%s LIMIT 1", (slug,))
    row = cur.fetchone()
    return row[0] if row else None


def _ensure_message_columns(cur):
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


def register_whatsapp_sender(server):
    @server.post("/whatsapp/send")
    def whatsapp_send():
        if not session.get("authenticated"):
            return Response("", status=302, headers={"Location": "/login"})

        slug = (session.get("workspace_slug") or "").strip()
        expected_csrf = session.get("whatsapp_send_csrf")
        supplied_csrf = request.form.get("csrf_token", "")
        if not expected_csrf or not secrets.compare_digest(expected_csrf, supplied_csrf):
            return Response("Pedido inválido. Actualize a caixa de entrada e tente novamente.", status=400, mimetype="text/plain")

        recipient = re.sub(r"\D", "", (request.form.get("to") or "").strip())
        message = (request.form.get("message") or "").strip()
        if not (8 <= len(recipient) <= 15) or not message or len(message) > 4096:
            return Response("Indique um número internacional válido e uma mensagem até 4096 caracteres.", status=400, mimetype="text/plain")

        token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
        phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
        version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0").strip()
        if not token or not phone_id:
            return Response("Envio indisponível: configure WHATSAPP_ACCESS_TOKEN e WHATSAPP_PHONE_NUMBER_ID no servidor.", status=503, mimetype="text/plain")

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": message},
        }
        try:
            response = requests.post(
                f"https://graph.facebook.com/{version}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=20,
            )
            result = response.json() if response.content else {}
        except (requests.RequestException, ValueError) as exc:
            print(f"[WHATSAPP SEND] Falha de comunicação: {type(exc).__name__}.")
            return Response("Não foi possível contactar a API do WhatsApp. Tente novamente.", status=502, mimetype="text/plain")

        if not response.ok:
            error = result.get("error", {}) if isinstance(result, dict) else {}
            code = error.get("code", "desconhecido")
            message_error = str(error.get("error_user_msg") or error.get("message") or "A Meta recusou o envio.")[:300]
            print(f"[WHATSAPP SEND] Meta recusou envio HTTP={response.status_code}, código={code}.")
            return Response(
                f"WhatsApp não aceitou a mensagem (código {code}): {message_error}",
                status=502, mimetype="text/plain",
            )

        messages = result.get("messages") or []
        wamid = str((messages[0] or {}).get("id") or "") if messages else ""
        if not wamid:
            print("[WHATSAPP SEND] API respondeu sem identificador de mensagem.")
            return Response("A Meta respondeu sem confirmar o identificador da mensagem.", status=502, mimetype="text/plain")

        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                _ensure_message_columns(cur)
                workspace_id = _workspace_id(cur, slug)
                if not workspace_id:
                    raise RuntimeError("Workspace não encontrado.")
                cur.execute("""
                    INSERT INTO whatsapp_messages
                        (workspace_id, message_id, phone_number_id, sender_phone,
                         sender_name, message_type, message_text, message_timestamp,
                         direction, status)
                    VALUES (%s,%s,%s,%s,'PMW','text',%s,NOW(),'outbound','accepted')
                    ON CONFLICT (message_id) DO NOTHING
                """, (workspace_id, wamid, phone_id, recipient, message))
            conn.commit()
        except Exception as exc:
            if conn:
                conn.rollback()
            print(f"[WHATSAPP SEND] Mensagem aceite pela Meta, mas falhou o registo local: {type(exc).__name__}.")
            return Response("A Meta aceitou o envio, mas o registo local falhou. Consulte os estados no WhatsApp.", status=202, mimetype="text/plain")
        finally:
            if conn:
                conn.close()

        return redirect("/whatsapp/inbox?sent=1", code=303)
