"""Instagram Platform webhook verification and signed event acknowledgement."""
import hashlib
import hmac
import json
import os

from flask import Response, request


def _app_secret():
    return os.getenv("INSTAGRAM_APP_SECRET", "").strip()


def _signature_is_valid(raw_body):
    secret = _app_secret()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not secret or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def register_instagram_webhooks(server):
    """Register the Instagram webhook endpoint without touching OAuth or WhatsApp."""
    @server.route("/webhooks/instagram", methods=["GET", "POST"])
    def instagram_webhook():
        if request.method == "GET":
            verify_token = os.getenv("INSTAGRAM_VERIFY_TOKEN", "").strip()
            mode = request.args.get("hub.mode", "")
            token = request.args.get("hub.verify_token", "")
            challenge = request.args.get("hub.challenge", "")

            if not verify_token:
                return Response(
                    "Instagram webhook verification is not configured.",
                    status=503,
                    mimetype="text/plain",
                )
            if (
                mode == "subscribe"
                and token
                and challenge
                and hmac.compare_digest(token, verify_token)
            ):
                return Response(challenge, status=200, mimetype="text/plain")
            return Response("Forbidden", status=403, mimetype="text/plain")

        raw_body = request.get_data(cache=True)
        if not _signature_is_valid(raw_body):
            return Response("Invalid webhook signature", status=401, mimetype="text/plain")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Webhook payload must be a JSON object.")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return Response("Invalid JSON payload", status=400, mimetype="text/plain")

        # Acknowledge valid Meta events. Event persistence/processing is intentionally
        # separate from this verification endpoint and can be added without changing OAuth.
        obj = str(payload.get("object") or "unknown")[:80]
        entries = payload.get("entry") or []
        print(
            "[INSTAGRAM WEBHOOK] Evento recebido: "
            f"object={obj!r} entries={len(entries) if isinstance(entries, list) else 0}.",
            flush=True,
        )
        return Response("EVENT_RECEIVED", status=200, mimetype="text/plain")
