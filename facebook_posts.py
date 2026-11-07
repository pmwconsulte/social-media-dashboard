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
                    f"https://graph.facebook.com/{version}/{selected[1]}/posts",
                    params={
                        "fields": "id,message,created_time,permalink_url,full_picture,shares,comments.limit(0).summary(true),reactions.limit(0).summary(true)",
                        "limit": 25,
                        "access_token": token,
                    },
                    timeout=20,
                )
                payload = response.json()
                if not response.ok:
                    error = payload.get("error", {}) if isinstance(payload, dict) else {}
                    print(
                        "[FACEBOOK POSTS] API indisponível: "
                        f"HTTP={response.status_code} code={error.get('code')!r} "
                        f"subcode={error.get('error_subcode')!r}."
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
                            permalink = html.escape(str(post.get("permalink_url") or ""), quote=True)
                            comments = (post.get("comments") or {}).get("summary", {}).get("total_count")
                            reactions = (post.get("reactions") or {}).get("summary", {}).get("total_count")
                            shares = (post.get("shares") or {}).get("count")
                            stats = []
                            if isinstance(reactions, int):
                                stats.append(f"Reações: {reactions}")
                            if isinstance(comments, int):
                                stats.append(f"Comentários: {comments}")
                            if isinstance(shares, int):
                                stats.append(f"Partilhas: {shares}")
                            stats_text = html.escape(" · ".join(stats) if stats else "Interações não disponibilizadas pela API")
                            picture = post.get("full_picture")
                            image = (
                                '<img src="' + html.escape(str(picture), quote=True) +
                                '" alt="Imagem da publicação" loading="lazy">'
                                if picture and str(picture).startswith("https://") else ""
                            )
                            link = (
                                '<a href="' + permalink + '" target="_blank" rel="noopener noreferrer">Ver publicação no Facebook</a>'
                                if permalink else ""
                            )
                            cards.append(
                                '<article class="post">' + image +
                                '<p class="date">' + created + '</p><p>' + message +
                                '</p><p class="stats">' + stats_text + '</p>' + link + '</article>'
                            )
                        body = "".join(cards)
            except (requests.RequestException, ValueError, TypeError, Exception) as exc:
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
        <p class="muted">Publicações e contagens devolvidas pela API oficial. Campos não disponibilizados pela Meta não são estimados.</p>
        <form method="get"><label for="page">Página: </label>
        <select id="page" name="page">""" + options + """</select>
        <button type="submit">Consultar</button></form>""" + body + """</main></body></html>
    return facebook_posts
