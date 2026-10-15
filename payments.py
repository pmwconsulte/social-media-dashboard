"""IntegraÃ§Ã£o segura PayPal Subscriptions (Sandbox/Live)."""
import os
from urllib.parse import urlparse
import requests
from flask import abort, redirect, request, session, url_for
from database import get_connection

PLAN_ENV = {"starter": "PAYPAL_PLAN_STARTER_ID", "pro": "PAYPAL_PLAN_PRO_ID", "business": "PAYPAL_PLAN_BUSINESS_ID"}
TIMEOUT = 20

def _enabled():
    return os.getenv("PAYMENT_AUTOMATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

def _api_base():
    mode = os.getenv("PAYPAL_MODE", "sandbox").strip().lower()
    if mode == "sandbox": return "https://api-m.sandbox.paypal.com"
    if mode == "live": return "https://api-m.paypal.com"
    raise RuntimeError("PAYPAL_MODE deve ser sandbox ou live.")

def _access_token():
    client_id, secret = os.getenv("PAYPAL_CLIENT_ID", "").strip(), os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if not client_id or not secret: raise RuntimeError("Credenciais PayPal nÃ£o configuradas.")
    r = requests.post(_api_base()+"/v1/oauth2/token", auth=(client_id,secret), data={"grant_type":"client_credentials"}, headers={"Accept":"application/json","Accept-Language":"en_US"}, timeout=TIMEOUT)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token: raise RuntimeError("PayPal nÃ£o devolveu token OAuth.")
    return token

def _api_request(method, path, **kwargs):
    headers=kwargs.pop("headers",{})
    headers.update({"Authorization":"Bearer "+_access_token(),"Accept":"application/json"})
    r=requests.request(method,_api_base()+path,headers=headers,timeout=TIMEOUT,**kwargs)
    r.raise_for_status()
    return r.json() if r.content else {}

def _plan_id(key):
    value=os.getenv(PLAN_ENV.get(key,""),"").strip()
    if not value: raise RuntimeError("O Plan ID PayPal deste plano ainda nÃ£o estÃ¡ configurado.")
    return value

def _ensure_webhook_table(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS paypal_webhook_events (
            event_id VARCHAR(255) PRIMARY KEY, event_type VARCHAR(120) NOT NULL,
            received_at TIMESTAMP NOT NULL DEFAULT NOW());""")
    conn.commit()

def _verify_webhook(payload):
    webhook_id=os.getenv("PAYPAL_WEBHOOK_ID","").strip()
    if not webhook_id: raise RuntimeError("PAYPAL_WEBHOOK_ID nÃ£o estÃ¡ configurado.")
    fields={"auth_algo":request.headers.get("PAYPAL-AUTH-ALGO"),"cert_url":request.headers.get("PAYPAL-CERT-URL"),
        "transmission_id":request.headers.get("PAYPAL-TRANSMISSION-ID"),"transmission_sig":request.headers.get("PAYPAL-TRANSMISSION-SIG"),
        "transmission_time":request.headers.get("PAYPAL-TRANSMISSION-TIME")}
    if not all(fields.values()): return False
    result=_api_request("POST","/v1/notifications/verify-webhook-signature",json={**fields,"webhook_id":webhook_id,"webhook_event":payload})
    return result.get("verification_status")=="SUCCESS"

def _set_active(subscription, expected_workspace, conn=None):
    if subscription.get("custom_id") != expected_workspace or subscription.get("status", "").upper() != "ACTIVE":
        return False

    sub_id, plan_id = subscription.get("id"), subscription.get("plan_id")
    plan_key = next(
        (k for k, env_name in PLAN_ENV.items()
         if plan_id and os.getenv(env_name, "").strip() == plan_id),
        None,
    )
    if not sub_id or not plan_key:
        return False

    owns_connection = conn is None
    if owns_connection:
        conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM workspaces WHERE slug=%s LIMIT 1",
                (expected_workspace,),
            )
            workspace = cur.fetchone()
            if not workspace:
                return False

            cur.execute(
                "SELECT id FROM workspaces WHERE subscription_id=%s AND id<>%s LIMIT 1",
                (sub_id, workspace[0]),
            )
            if cur.fetchone():
                return False

            cur.execute(
                """UPDATE workspaces
                   SET subscription_status='active', subscription_plan=%s,
                       payment_provider='paypal', subscription_id=%s
                   WHERE id=%s""",
                (plan_key, sub_id, workspace[0]),
            )

        if owns_connection:
            conn.commit()
        return True
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()


def _set_inactive(subscription, event_type, conn=None):
    sub_id = subscription.get("id")
    status = {
        "BILLING.SUBSCRIPTION.CANCELLED": "cancelled",
        "BILLING.SUBSCRIPTION.SUSPENDED": "suspended",
        "BILLING.SUBSCRIPTION.EXPIRED": "expired",
    }.get(event_type)

    if not sub_id or not status:
        return False

    owns_connection = conn is None
    if owns_connection:
        conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE workspaces
                   SET subscription_status=%s
                   WHERE subscription_id=%s AND payment_provider='paypal'""",
                (status, sub_id),
            )
            changed = cur.rowcount > 0

        if owns_connection:
            conn.commit()
        return changed
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()


def register_paypal_routes(server):
    try:
        conn=get_connection()
        try: _ensure_webhook_table(conn)
        finally: conn.close()
    except Exception as exc:
        print(f"[PAYPAL INIT] {type(exc).__name__}")

    @server.get("/billing/paypal/subscribe/<plan_key>")
    def paypal_subscribe(plan_key):
        if not session.get("authenticated"): return redirect(url_for("login"))
        if not _enabled(): return "Checkout PayPal desativado durante os testes.",503
        if plan_key not in PLAN_ENV: abort(404)
        slug=session.get("workspace_slug")
        if not slug or len(slug)>127: return "Workspace invÃ¡lido.",400
        try:
            plan_id=_plan_id(plan_key)
            conn=get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM workspaces WHERE slug=%s LIMIT 1",(slug,))
                    if not cur.fetchone(): return "Workspace nÃ£o encontrado.",404
            finally: conn.close()
            base=os.getenv("APP_BASE_URL",request.url_root.rstrip("/")).rstrip("/")
            parsed=urlparse(base)
            if parsed.scheme!="https" and parsed.hostname not in {"localhost","127.0.0.1"}: return "APP_BASE_URL deve usar HTTPS.",503
            result=_api_request("POST","/v1/billing/subscriptions",json={"plan_id":plan_id,"custom_id":slug,
                "application_context":{"brand_name":"PMW Social Media Dashboard AI","user_action":"SUBSCRIBE_NOW",
                "return_url":base+"/billing/paypal/return","cancel_url":base+"/billing?paypal=cancelled"}},
                headers={"Content-Type":"application/json"})
            approve=next((x.get("href") for x in result.get("links",[]) if x.get("rel")=="approve"),None)
            if not result.get("id") or not approve: raise RuntimeError("PayPal nÃ£o devolveu o link de aprovaÃ§Ã£o.")
            return redirect(approve,code=303)
        except requests.RequestException as exc:
            print(f"[PAYPAL CREATE ERROR] {type(exc).__name__}")
            return "NÃ£o foi possÃ­vel iniciar a subscriÃ§Ã£o PayPal.",502
        except Exception as exc:
            print(f"[PAYPAL CREATE ERROR] {type(exc).__name__}: {exc}")
            return "Checkout indisponÃ­vel. Confirme os Plan IDs.",503

    @server.get("/billing/paypal/return")
    def paypal_return():
        if not session.get("authenticated"): return redirect(url_for("login"))
        if not _enabled(): return "Checkout PayPal desativado durante os testes.",503
        sub_id=request.args.get("subscription_id","").strip()
        if not sub_id or len(sub_id)>255: return redirect(url_for("billing"))
        try:
            sub=_api_request("GET","/v1/billing/subscriptions/"+sub_id)
            slug=session.get("workspace_slug")
            if sub.get("custom_id")!=slug: return "A subscriÃ§Ã£o nÃ£o corresponde ao workspace autenticado.",403
            if _set_active(sub,slug): return redirect("/billing?paypal=active")
            return redirect("/billing?paypal=pending")
        except requests.RequestException as exc:
            print(f"[PAYPAL RETURN ERROR] {type(exc).__name__}")
            return redirect("/billing?paypal=verify_error")

    @server.post("/webhooks/paypal")
    def paypal_webhook():
        conn = None
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return "JSON inv?lido", 400

            if not _verify_webhook(payload):
                return "Assinatura do webhook inv?lida", 400

            if not _enabled():
                return "Webhook verificado; automa??o desativada.", 202

            event_id = payload.get("id")
            event_type = payload.get("event_type", "")
            resource = payload.get("resource") or {}
            sub_id = resource.get("id")

            if not isinstance(event_id, str) or not event_id.strip():
                return "Evento incompleto", 400
            if not isinstance(event_type, str) or not event_type.strip():
                return "Evento incompleto", 400

            # Obter o estado atual confirmado pelo PayPal antes de alterar a BD.
            sub = None
            if event_type.startswith("BILLING.SUBSCRIPTION.") and sub_id:
                sub = _api_request(
                    "GET",
                    "/v1/billing/subscriptions/" + str(sub_id),
                )

            conn = get_connection()
            _ensure_webhook_table(conn)

            # A inser??o permanece sem commit at? ? atualiza??o da subscri??o.
            # A chave prim?ria impede que dois pedidos confirmem o mesmo evento.
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO paypal_webhook_events(event_id, event_type)
                       VALUES(%s, %s)
                       ON CONFLICT(event_id) DO NOTHING
                       RETURNING event_id""",
                    (event_id, event_type),
                )
                inserted = cur.fetchone()

            if not inserted:
                conn.commit()
                return "Evento j? processado.", 200

            if sub is not None:
                if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
                    applied = _set_active(sub, sub.get("custom_id"), conn=conn)
                    if not applied:
                        conn.rollback()
                        return "Ativa??o n?o conclu?da; ser? poss?vel tentar novamente.", 503

                elif event_type in {
                    "BILLING.SUBSCRIPTION.CANCELLED",
                    "BILLING.SUBSCRIPTION.SUSPENDED",
                    "BILLING.SUBSCRIPTION.EXPIRED",
                }:
                    applied = _set_inactive(sub, event_type, conn=conn)
                    if not applied:
                        conn.rollback()
                        return "Atualiza??o n?o conclu?da; ser? poss?vel tentar novamente.", 503

            conn.commit()
            return "OK", 200

        except requests.RequestException as exc:
            if conn is not None:
                conn.rollback()
            print(f"[PAYPAL WEBHOOK ERROR] {type(exc).__name__}")
            return "Erro temporário a validar evento", 503

        except Exception as exc:
            if conn is not None:
                conn.rollback()
            print(f"[PAYPAL WEBHOOK ERROR] {type(exc).__name__}: {exc}")
            return "Erro ao processar evento", 500

        finally:
            if conn is not None:
                conn.close()
