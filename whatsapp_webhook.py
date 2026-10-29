"""WhatsApp Cloud API webhook endpoints.

Configure WHATSAPP_VERIFY_TOKEN in the hosting environment and use
https://pmwconsultoria.com/webhooks/whatsapp as the Meta Callback URL.
"""
import hmac
import os

from flask import Response, request


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

        # Acknowledge valid JSON webhook notifications quickly. Detailed event
        # processing/logging can be added separately without exposing tokens.
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return Response("Invalid JSON", status=400, mimetype="text/plain")

        # Meta expects a 200 response to acknowledge webhook delivery.
        # Do not log access tokens or full message payloads.
        return Response("EVENT_RECEIVED", status=200, mimetype="text/plain")
