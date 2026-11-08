"""Read-only Facebook Page posts view for connected PMW workspaces."""
import html
import os

import requests
from flask import redirect, request, session, url_for


def register_facebook_posts_route(server, get_connection, token_cipher):
    @server.get("/facebook/posts")
    def facebook_posts():
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        workspace_slug = session.get("workspace_slug")
        conn = None
        pages = []
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sa.id, sa.username, sa.display_name, sa.access_token
                       FROM social_accounts sa
                       JOIN workspaces w ON w.id=sa.workspace_id
                       WHERE w.slug=%s AND sa.platform='Facebook'
                         AND COALESCE(sa.is_active, TRUE)=TRUE
                         AND LOWER(COALESCE(sa.connection_status, ''))='connected'
                       ORDER BY sa.display_name""",
                    (workspace_slug,),
                )
                pages = cur.fetchall()
        except Exception as exc:
            print(f"[FACEBOOK POSTS] Falha ao carregar Páginas: {type(exc).__name__}.")
            return "Não foi possível carregar as Páginas ligadas. Tente novamente mais tarde.", 500
        finally:
            if conn:
                conn.close()

        version = os.getenv("FACEBOOK_GRAPH_API_VERSION", "v25.0").strip()
        selected_id = request.args.get("page", "").strip()
        if selected_id and not any(str(p[0]) == selected_id for p in pages):
            selected_id = ""
        selected = next((p for p in pages if str(p[0]) == selected_id), None)
        if selected is None and pages:
            selected = pages[0]

        options = "".join(
            '<option value="' + html.escape(str(p[0]), quote=True) + '"' +
            (" selected" if selected and str(p[0]) == str(selected[0]) else "") + ">" +
            html.escape(str(p[2] or p[1])) + "</option>"
            for p in pages
        )
        body = ""
        if selected:
            try:
                token = token_cipher().decrypt(selected[3].encode("utf-8")).decode("utf-8")
                response = requests.get(
                    f"https://graph.facebook.com/{version}/{selected[1]}/published_posts",
                    params={
                        # These are the exact fields confirmed in the successful
                        # Graph API Explorer test for this Page.
                        "fields": "id,message,created_time",
                        "limit": 25,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=20,
                )
                payload = response.json()
                if not response.ok:
                    error = payload.get("error", {}) if isinstance(payload, dict) else {}
                    # Log the Meta diagnostic message, but never log request headers,
                    # access tokens, or the complete response payload.
                    error_message = str(error.get("message") or "Sem mensagem de erro")[:500]
                    error_type = str(error.get("type") or "desconhecido")[:100]
                    print(
                        "[FACEBOOK POSTS] API indisponível: "
                        f"HTTP={response.status_code} type={error_type!r} "
                        f"code={error.get('code')!r} "
                        f"subcode={error.get('error_subcode')!r} "
                        f"message={error_message!r} "
                        f"fbtrace_id={payload.get('fbtrace_id')!r}."
                    )
                    body = '<p class="muted">A Meta não disponibilizou as publicações desta Página com as permissões atuais. Os seguidores continuam preservados.</p>'
                else:
                    posts = payload.get("data") or []
                    if not posts:
                        body = '<p class="muted">A API não devolveu publicações para esta Página.</p>'
                    else:
                        cards = []
                        for post in posts:
                            message = html.escape(str(post.get("message") or "Publicação sem texto"))
                            created = html.escape(str(post.get("created_time") or ""))
                            cards.append(
                                '<article class="post"><p class="date">' + created +
                                '</p><p>' + message + '</p></article>'
                            )
                        body = "".join(cards)
            except Exception as exc:
                print(f"[FACEBOOK POSTS] Erro ao consultar publicações: {type(exc).__name__}.")
                body = '<p class="muted">Não foi possível consultar as publicações. Verifique os logs do servidor.</p>'
        else:
            body = '<p class="muted">Ainda não existem Páginas Facebook ligadas a este workspace.</p>'

        return """<!doctype html><html lang="pt"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Publicações Facebook — PMW Dashboard AI</title>
        <style>
        body{margin:0;background:#020617;color:#f8fafc;font-family:Arial,sans-serif;padding:24px}
        main{max-width:1050px;margin:auto}.muted,.date{color:#94a3b8}
        select,button{padding:11px;border-radius:8px;border:1px solid #475569;background:#111827;color:#fff}
        button{background:#0284c7;border:0;font-weight:bold;cursor:pointer}
        .post{background:#111827;border:1px solid #334155;border-radius:12px;padding:18px;margin:14px 0}
        .post img{max-width:100%;max-height:360px;object-fit:contain;border-radius:8px}
        .stats{color:#7dd3fc}.post a,a{color:#7dd3fc}
        </style></head><body><main><p><a href="/account">← Voltar à conta</a></p>
        <h1>Publicações do Facebook</h1>
        <p class="muted">Publicações devolvidas pela API oficial. Apenas os campos confirmados pela Meta são apresentados.</p>
        <form method="get"><label for="page">Página: </label>
        <select id="page" name="page">""" + options + """</select>
        <button type="submit">Consultar</button></form>""" + body + """</main></body></html>"""
    return facebook_posts
