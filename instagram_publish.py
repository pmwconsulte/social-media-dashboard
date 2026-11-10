"""Instagram single-photo publishing route for Instagram Login accounts."""
import ipaddress
import time
from urllib.parse import urlsplit
import secrets
import os

import requests
from flask import redirect, render_template_string, request, session, url_for


def _public_https_image_url(value):
    """Accept a public HTTPS image URL; Meta fetches it directly."""
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        host = parsed.hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
            return ""
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                return ""
        except ValueError:
            if "." not in host:
                return ""
        return value
    except ValueError:
        return ""


def register_instagram_publish_route(server, get_connection, token_cipher):
    @server.route("/instagram/publish", methods=["GET", "POST"])
    def instagram_publish():
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        csrf = session.setdefault("instagram_publish_csrf", secrets.token_urlsafe(32))
        workspace_slug = session.get("workspace_slug")
        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sa.id, sa.username, sa.access_token
                       FROM social_accounts sa
                       JOIN workspaces w ON w.id=sa.workspace_id
                       WHERE w.slug=%s AND LOWER(sa.platform)='instagram'
                         AND COALESCE(sa.is_active, TRUE)=TRUE
                         AND LOWER(COALESCE(sa.connection_status,''))='connected'
                       ORDER BY sa.last_sync_at DESC NULLS LAST""",
                    (workspace_slug,),
                )
                accounts = cur.fetchall()
        except Exception as exc:
            print(f"[INSTAGRAM PUBLISH] account_lookup_error={type(exc).__name__}")
            return "Não foi possível carregar a conta Instagram ligada.", 500
        finally:
            if conn:
                conn.close()

        if not accounts:
            return render_template_string(
                _PAGE, message="Não existe uma conta Instagram profissional ligada neste workspace.",
                accounts=[], csrf=csrf, selected="", image_url="", caption=""
            )

        selected_id = request.form.get("account_id", "") if request.method == "POST" else request.args.get("account", "")
        selected = next((row for row in accounts if str(row[0]) == selected_id), None)
        if request.method == "POST" and not selected:
            return "Conta Instagram inválida para este workspace. Actualize a página.", 400
        selected = selected or accounts[0]
        account_id, username, encrypted_token = selected
        options = [{"id": str(row[0]), "username": str(row[1])} for row in accounts]

        if request.method == "GET":
            return render_template_string(
                _PAGE, message="", accounts=options, csrf=csrf, selected=str(account_id),
                image_url="", caption=""
            )

        received = request.form.get("csrf_token", "")
        if not received or not secrets.compare_digest(csrf, received):
            return "Pedido inválido ou expirado. Actualize a página.", 400

        image_url = _public_https_image_url(request.form.get("image_url", ""))
        caption = (request.form.get("caption") or "").strip()
        if not image_url:
            return render_template_string(
                _PAGE, message="Indique um URL HTTPS público válido para uma fotografia JPEG.",
                accounts=options, csrf=csrf, selected=str(account_id),
                image_url=request.form.get("image_url", ""), caption=caption
            ), 400
        if len(caption) > 2200:
            return render_template_string(
                _PAGE, message="A legenda não pode ultrapassar 2.200 caracteres.",
                accounts=options, csrf=csrf, selected=str(account_id),
                image_url=image_url, caption=caption
            ), 400

        try:
            access_token = token_cipher().decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
            version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
            if not version.startswith("v") or not version[1:].replace(".", "").isdigit():
                raise ValueError("Invalid Instagram Graph API version")
            base = "https://graph.instagram.com/" + version
            me = requests.get(
                f"{base}/me", params={"fields": "id,username", "access_token": access_token},
                timeout=15
            )
            me.raise_for_status()
            ig_id = str(me.json().get("id", ""))
            if not ig_id:
                raise ValueError("Instagram account id missing")

            quota = requests.get(
                base + "/" + ig_id + "/content_publishing_limit",
                params={"access_token": access_token}, timeout=15,
            )
            quota.raise_for_status()
            quota_data = quota.json().get("data", [])
            if quota_data:
                quota_info = quota_data[0]
                quota_total = (quota_info.get("config") or {}).get("quota_total")
                quota_usage = quota_info.get("quota_usage")
                if quota_total is not None and quota_usage is not None and int(quota_usage) >= int(quota_total):
                    return render_template_string(
                        _PAGE, message="A conta atingiu o limite de publicações permitido pela Meta. Tente novamente mais tarde.",
                        accounts=options, csrf=csrf, selected=str(account_id),
                        image_url=image_url, caption=caption
                    ), 429

            container_params = {"image_url": image_url, "access_token": access_token}
            if caption:
                container_params["caption"] = caption
            created = requests.post(f"{base}/{ig_id}/media", data=container_params, timeout=30)
            try:
                created_payload = created.json()
            except ValueError:
                created_payload = {}
            if not created.ok or not created_payload.get("id"):
                err = created_payload.get("error", {}) if isinstance(created_payload, dict) else {}
                print(
                    f"[INSTAGRAM PUBLISH] container_failed http={created.status_code} "
                    f"code={err.get('code')} subcode={err.get('error_subcode')} "
                    f"message={str(err.get('message','') or '')[:250]}"
                )
                return render_template_string(
                    _PAGE, message="A Meta não conseguiu preparar a fotografia. Confirme se o URL aponta para um JPEG público e se a permissão de publicação está autorizada.",
                    accounts=options, csrf=csrf, selected=str(account_id),
                    image_url=image_url, caption=caption
                ), 502

            container_id = str(created_payload["id"])
            status = ""
            for poll_index in range(5):
                check = requests.get(
                    f"{base}/{container_id}",
                    params={"fields": "status_code", "access_token": access_token},
                    timeout=15
                )
                check.raise_for_status()
                status = str(check.json().get("status_code", ""))
                if status in ("FINISHED", "ERROR", "EXPIRED", "PUBLISHED"):
                    break
                if poll_index < 4:
                    time.sleep(60)
            if status == "PUBLISHED":
                return render_template_string(
                    _PAGE, message="A Meta indica que esta publicação já foi publicada. Não foi enviada novamente.",
                    accounts=options, csrf=csrf, selected=str(account_id),
                    image_url="", caption=""
                )
            if status != "FINISHED":
                print(f"[INSTAGRAM PUBLISH] container_not_ready status={status or 'unknown'}")
                return render_template_string(
                    _PAGE, message="A Meta ainda não concluiu o processamento da fotografia. Tente novamente mais tarde.",
                    accounts=options, csrf=csrf, selected=str(account_id),
                    image_url=image_url, caption=caption
                ), 502

            published = requests.post(
                f"{base}/{ig_id}/media_publish",
                data={"creation_id": container_id, "access_token": access_token},
                timeout=30
            )
            try:
                published_payload = published.json()
            except ValueError:
                published_payload = {}
            if not published.ok or not published_payload.get("id"):
                try:
                    reconcile = requests.get(
                        base + "/" + container_id,
                        params={"fields": "status_code", "access_token": access_token},
                        timeout=15,
                    )
                    reconcile.raise_for_status()
                    reconciled_status = str(reconcile.json().get("status_code", ""))
                except requests.RequestException:
                    reconciled_status = ""
                if reconciled_status == "PUBLISHED":
                    return render_template_string(
                        _PAGE, message="A Meta confirmou que a fotografia foi publicada. Não repita o envio.",
                        accounts=options, csrf=csrf, selected=str(account_id),
                        image_url="", caption=""
                    )
                err = published_payload.get("error", {}) if isinstance(published_payload, dict) else {}
                print(
                    f"[INSTAGRAM PUBLISH] publish_failed http={published.status_code} "
                    f"code={err.get('code')} subcode={err.get('error_subcode')} "
                    f"message={str(err.get('message','') or '')[:250]}"
                )
                return render_template_string(
                    _PAGE, message="A fotografia foi preparada, mas a Meta não confirmou a publicação. Verifique a conta Instagram.",
                    accounts=options, csrf=csrf, selected=str(account_id),
                    image_url=image_url, caption=caption
                ), 502

            media_id = str(published_payload["id"])
            print(f"[INSTAGRAM PUBLISH] success=true account={account_id} media_id={media_id}")
            return render_template_string(
                _PAGE, message="Fotografia publicada com sucesso no Instagram.",
                accounts=options, csrf=csrf, selected=str(account_id),
                image_url="", caption=""
            )
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            print(f"[INSTAGRAM PUBLISH] api_request_failed status={status} type={type(exc).__name__}")
            return render_template_string(
                _PAGE, message="Não foi possível concluir a publicação. Verifique a ligação à Meta e tente novamente.",
                accounts=options, csrf=csrf, selected=str(account_id),
                image_url=image_url, caption=caption
            ), 502
        except Exception as exc:
            print(f"[INSTAGRAM PUBLISH] unexpected_error={type(exc).__name__}")
            return render_template_string(
                _PAGE, message="Ocorreu um erro ao preparar a publicação.",
                accounts=options, csrf=csrf, selected=str(account_id),
                image_url=image_url, caption=caption
            ), 500


_PAGE = """<!doctype html><html lang="pt"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Publicar fotografia — PMW Dashboard AI</title>
<style>
body{margin:0;padding:24px;background:#020617;color:#f8fafc;font-family:Arial,sans-serif}
main{max-width:760px;margin:30px auto;background:#111827;border:1px solid #334155;border-radius:16px;padding:24px}
label{display:block;margin:16px 0 7px;font-weight:700}
input,select,textarea{width:100%;box-sizing:border-box;padding:12px;border:1px solid #475569;border-radius:9px;background:#020617;color:#fff;font:inherit}
textarea{min-height:130px;resize:vertical}
button,.back{display:inline-block;margin-top:18px;padding:12px 18px;border:0;border-radius:9px;background:#db2777;color:#fff;font-weight:700;text-decoration:none;cursor:pointer}
.note{color:#cbd5e1;font-size:13px;line-height:1.5}.message{padding:12px;border-radius:9px;background:#1e3a8a;margin:14px 0}
</style></head><body><main><a class="back" href="/account">← Minha Conta</a>
<h1>📷 Publicar fotografia</h1>
<p class="note">A publicação é feita directamente pela API oficial do Instagram. Use uma fotografia JPEG num endereço HTTPS público, acessível sem login. A Meta obtém a fotografia a partir desse endereço.</p>
{% if message %}<div class="message">{{ message }}</div>{% endif %}
{% if accounts %}<form method="post" onsubmit="return confirm(&quot;Confirma a publicação desta fotografia na conta Instagram seleccionada?&quot;);">
<input type="hidden" name="csrf_token" value="{{ csrf }}">
<label for="account_id">Conta Instagram</label><select id="account_id" name="account_id">{% for item in accounts %}
<option value="{{ item.id }}" {% if item.id == selected %}selected{% endif %}>{{ item.username }}</option>{% endfor %}</select>
<label for="image_url">URL público da fotografia JPEG</label>
<input id="image_url" name="image_url" type="url" inputmode="url" maxlength="2048" value="{{ image_url }}" placeholder="https://exemplo.com/fotografia.jpg" required>
<label for="caption">Legenda</label><textarea id="caption" name="caption" maxlength="2200" placeholder="Escreva a legenda da publicação...">{{ caption }}</textarea>
<button type="submit">Publicar no Instagram</button>
<p class="note">Ao seleccionar Publicar, a fotografia será publicada na conta escolhida. Confirme o URL e a legenda antes de continuar.</p>
</form>{% endif %}</main></body></html>"""
