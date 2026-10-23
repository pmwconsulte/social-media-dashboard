import os
import traceback

import pandas as pd
import plotly.express as px
import dash

from dash import html, dcc
from flask import Response, has_request_context, request, session, send_from_directory
from dash.dependencies import Input, Output
from dotenv import load_dotenv

# Carregar o .env antes de importar database.py, cuja inicialização é automática.
load_dotenv()

from database import get_connection, initialize_database
from auth import setup_auth

from ai_engine import (
    prever_crescimento,
    gerar_alerta,
    gerar_recomendacao,
    calcular_score,
    gerar_relatorio_executivo,
)

APP_TITLE = "PMW Social Media Dashboard AI"

COLORS = {
    "background": "#020617",
    "card": "#111827",
    "card_kpi": "#172554",
    "primary": "#38bdf8",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "purple": "#a855f7",
    "danger": "#ef4444",
    "text": "#f8fafc",
    "muted": "#94a3b8",
    "border": "#1e293b",
}

app = dash.Dash(
    __name__,
    title=APP_TITLE,
    suppress_callback_exceptions=True,
)

server = app.server

# Serve the existing PMW brand asset from the repository root.
@server.get("/logo.png")
def pmw_logo():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "logo.png", mimetype="image/png")

# Google Search Console HTML verification
@server.route("/google70d27d788b9e13bf.html")
def google_site_verification():
    return Response(
        "google-site-verification: google70d27d788b9e13bf.html",
        mimetype="text/html",
    )

# SEO endpoints: expose only public entry pages to search engines.
PUBLIC_BASE_URL = "https://social-media-dashboard-s0i4.onrender.com"

@server.get("/robots.txt")
def robots_txt():
    body = "\n".join([
        "User-agent: *",
        # Leave /login and /signup crawlable so search engines can process noindex.
        "Disallow: /account",
        "Disallow: /billing",
        "Disallow: /logout",
        "Disallow: /oauth/",
        "Disallow: /social-accounts/",
        "Disallow: /_dash-",
        "Disallow: /health",
        f"Sitemap: {PUBLIC_BASE_URL}/sitemap.xml",
    ]) + "\n"
    return Response(body, mimetype="text/plain")

# Página pública de apresentação do produto (o dashboard principal continua protegido).
PUBLIC_HOME_PAGE = """<!doctype html>
<html lang="pt">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PMW Social Media Dashboard AI | Análise de Redes Sociais</title>
  <meta name="description" content="Acompanhe métricas de redes sociais, crescimento, alcance e envolvimento num dashboard com indicadores e apoio de inteligência artificial.">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="https://social-media-dashboard-s0i4.onrender.com/home">
  <meta property="og:type" content="website">
  <meta property="og:title" content="PMW Social Media Dashboard AI">
  <meta property="og:description" content="Métricas, tendências e relatórios de redes sociais num único dashboard com apoio de IA.">
  <meta property="og:url" content="https://social-media-dashboard-s0i4.onrender.com/home">
  <meta name="theme-color" content="#020617">
  <style>
    :root{color-scheme:dark;--bg:#020617;--panel:#111827;--line:#263449;--text:#f8fafc;--muted:#a7b4c7;--blue:#38bdf8;--green:#22c55e}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top,#0c2344 0,var(--bg) 55%);color:var(--text);font-family:Inter,Arial,sans-serif;line-height:1.6}
    a{color:inherit}.wrap{width:min(1120px,92%);margin:auto}.nav{display:flex;justify-content:space-between;align-items:center;gap:18px;padding:22px 0}.brand{display:flex;align-items:center;gap:12px;font-weight:800;font-size:18px}.brand img{width:64px;height:64px;object-fit:cover;object-position:center;border-radius:12px;background:#fff;padding:0;flex:0 0 64px}.brand-copy{display:flex;flex-direction:column;gap:3px}.brand-slogan{display:block;max-width:270px;color:var(--muted);font-size:13px;line-height:1.35;font-weight:700}.navlinks{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.btn{display:inline-block;text-decoration:none;font-weight:700;padding:11px 17px;border-radius:10px;background:var(--blue);color:#06111f}.btn.secondary{background:transparent;border:1px solid #456078;color:var(--text)}.btn:hover{filter:brightness(1.08);transform:translateY(-1px)}.btn:focus-visible,a:focus-visible{outline:3px solid #7dd3fc;outline-offset:3px}.trust-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:8px 0 24px}.trust-item{display:flex;gap:10px;align-items:flex-start;padding:15px;border:1px solid var(--line);border-radius:12px;background:rgba(15,23,42,.72)}.trust-item strong{display:block;font-size:14px}.trust-item span{display:block;color:var(--muted);font-size:13px;line-height:1.45}.microcopy{color:var(--muted);font-size:13px;max-width:680px;margin-top:14px}.step-number{display:inline-grid;place-items:center;width:32px;height:32px;border-radius:9px;background:#0b2b45;color:var(--blue);font-weight:800}
    .hero{padding:72px 0 64px;max-width:850px}.eyebrow{color:var(--blue);font-weight:800;letter-spacing:.12em;font-size:12px;text-transform:uppercase}.hero h1{font-size:clamp(36px,6vw,64px);line-height:1.08;letter-spacing:-.04em;margin:18px 0}.hero p{font-size:19px;color:var(--muted);max-width:720px}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:28px}
    .section{padding:42px 0}.section h2{font-size:clamp(26px,4vw,36px);margin:0 0 12px}.intro{color:var(--muted);max-width:760px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-top:24px}.card{background:rgba(17,24,39,.86);border:1px solid var(--line);border-radius:16px;padding:22px}.card h3{margin:8px 0}.card p{color:var(--muted);margin-bottom:0}.symbol{font-size:25px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:30px}.metric{padding:18px;border-radius:14px;background:#0b1730;border:1px solid #1d3554}.metric strong{display:block;color:var(--blue);font-size:21px}.metric span{color:var(--muted);font-size:13px}.cta{margin:45px 0;padding:30px;border-radius:18px;border:1px solid #1d4562;background:linear-gradient(120deg,#0c2440,#111827)}footer{border-top:1px solid var(--line);padding:24px 0;color:var(--muted);font-size:13px}
    @media(max-width:600px){.nav{align-items:flex-start;flex-direction:column}.hero{padding:46px 0 35px}.hero p{font-size:17px}}
  </style>
</head>
<body>
<header class="wrap nav">
  <div class="brand"><img src="/logo.png" alt="Símbolo PMW Consultoria"><span class="brand-copy"><span>PMW Dashboard AI</span><span class="brand-slogan">Transforme os Seus Dados em Oportunidades de Negócio</span></span></div>
  <nav class="navlinks" aria-label="Navegação principal">
    <a class="btn secondary" href="/login">Entrar</a>
    <a class="btn" href="/signup">Criar conta</a>
    <a class="btn secondary" href="#contacto">Contacto</a>
  </nav>
</header>
<main class="wrap">
  <section class="hero">
    <div class="eyebrow">Analytics • Métricas • Inteligência Artificial</div>
    <h1>Acompanhe as suas redes sociais. Decida com inteligência.</h1>
    <p>Transforme métricas em decisões estratégicas. Analise o desempenho, acompanhe tendências e descubra oportunidades para impulsionar os resultados do seu negócio — com indicadores claros e apoio de inteligência artificial.</p>
    <div class="actions">
      <a class="btn" href="/signup">Começar o trial de 30 dias</a>
      <a class="btn secondary" href="/login">Já tenho uma conta</a>
    </div>
    <p class="microcopy">Comece com uma visão mais clara do desempenho das suas redes sociais. Crie a sua conta e explore as funcionalidades disponíveis durante o período de experiência de 30 dias.</p>
    <div class="trust-strip" aria-label="Benefícios e confiança">
      <div class="trust-item"><span aria-hidden="true">🔎</span><div><strong>Visão clara</strong><span>Métricas organizadas para compreender o desempenho.</span></div></div>
      <div class="trust-item"><span aria-hidden="true">🔐</span><div><strong>Acesso protegido</strong><span>As áreas privadas exigem autenticação.</span></div></div>
      <div class="trust-item"><span aria-hidden="true">🔗</span><div><strong>Ligação autorizada</strong><span>Conecte contas através do fluxo oficial de autorização disponível.</span></div></div>
    </div>
    <div class="metrics" aria-label="Indicadores disponíveis">
      <div class="metric"><strong>Seguidores</strong><span>Evolução da audiência</span></div>
      <div class="metric"><strong>Alcance</strong><span>Distribuição do conteúdo</span></div>
      <div class="metric"><strong>Engajamento</strong><span>Interação com o público</span></div>
      <div class="metric"><strong>Score IA</strong><span>Indicador de apoio à análise</span></div>
    </div>
  </section>
  <section class="section">
    <h2>O que pode acompanhar</h2>
    <p class="intro">Uma visão organizada para ajudar a compreender o desempenho, identificar alterações e orientar os próximos passos.</p>
    <div class="grid">
      <article class="card"><div class="symbol">📈</div><h3>Métricas num só lugar</h3><p>Visualize seguidores, alcance, impressões, visualizações do perfil, cliques e taxa de envolvimento quando esses dados estiverem disponíveis.</p></article>
      <article class="card"><div class="symbol">🤖</div><h3>Apoio de inteligência artificial</h3><p>Consulte alertas, recomendações e um relatório executivo gerados a partir dos dados disponíveis no dashboard.</p></article>
      <article class="card"><div class="symbol">🔐</div><h3>Privacidade e controlo</h3><p>O acesso ao dashboard exige autenticação. A ligação ao Instagram é feita através do processo oficial de autorização; a PMW não precisa de receber a palavra-passe do Instagram. Os indicadores apresentados dependem das permissões concedidas e da disponibilidade da API.</p></article>
    </div>
  </section>
  <section class="cta">
    <h2>Experimente uma forma mais inteligente de acompanhar as suas redes sociais.</h2>
    <p class="intro">Comece com o período de experiência de 30 dias e descubra como os indicadores, alertas e recomendações podem apoiar a sua análise. A disponibilidade de cada métrica depende da rede social, das permissões e dos dados recebidos.</p>
    <div class="actions"><a class="btn" href="/signup">Experimentar gratuitamente por 30 dias</a><a class="btn secondary" href="/login">Já tenho uma conta</a></div>
    <p class="microcopy">Sem promessas de resultados automáticos: a plataforma ajuda a interpretar dados para apoiar decisões mais informadas. Consulte os detalhes do plano antes de subscrever.</p>
  </section>
  <section class="section">
    <h2>Como começar</h2>
    <p class="intro">São três passos simples para começar a explorar a plataforma.</p>
    <div class="grid">
      <article class="card"><span class="step-number">1</span><h3>Crie a sua conta</h3><p>Registe-se para iniciar o período de experiência de 30 dias.</p></article>
      <article class="card"><span class="step-number">2</span><h3>Ligue uma rede social</h3><p>Autorize a ligação através do fluxo oficial e conceda apenas as permissões solicitadas.</p></article>
      <article class="card"><span class="step-number">3</span><h3>Explore os indicadores</h3><p>Consulte os dados disponíveis, acompanhe tendências e reveja as recomendações.</p></article>
    </div>
  </section>
  <section class="section" id="contacto">
    <h2>Contacte a PMW Consultoria</h2>
    <p class="intro">Tem dúvidas ou precisa de apoio? Entre em contacto connosco através do email ou telefone.</p>
    <div class="grid">
      <article class="card">
        <div class="symbol">✉️</div>
        <h3>Email</h3>
        <p><a href="mailto:info@pmwconsultoria.com">info@pmwconsultoria.com</a></p>
      </article>
      <article class="card">
        <div class="symbol">☎️</div>
        <h3>Telefone / WhatsApp</h3>
        <p><a href="tel:+258849550202">+258 84 955 0202</a></p>
        <div class="actions"><a class="btn" href="https://wa.me/258823002555" target="_blank" rel="noopener noreferrer">Contactar pelo WhatsApp</a></div>
      </article>
      <article class="card">
        <div class="symbol">📍</div>
        <h3>Morada</h3>
        <p>Avenida Patrice Lumumba, n.º 1125, R/C</p>
      </article>
    </div>
    <div class="actions">
      <a class="btn" href="mailto:info@pmwconsultoria.com?subject=Contacto%20PMW%20Consultoria">Enviar email</a>
      <a class="btn secondary" href="tel:+258849550202">Ligar para +258 84 955 0202</a>
    </div>
  </section>
</main>
<footer><div class="wrap">© 2026 PMW Consultoria &amp; Tecnologia · PMW Social Media Dashboard AI · <a href="#contacto">Contacto</a> · <a href="mailto:info@pmwconsultoria.com">info@pmwconsultoria.com</a> · <a href="tel:+258849550202">+258 84 955 0202</a> · Avenida Patrice Lumumba, n.º 1125, R/C</div></footer>
</body>
</html>"""

@server.get("/home")
def public_home():
    return Response(PUBLIC_HOME_PAGE, mimetype="text/html")

@server.get("/sitemap.xml")
def sitemap_xml():
    # Inclui apenas a página pública de apresentação; não indexa áreas privadas.
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <url><loc>{PUBLIC_BASE_URL}/home</loc></url>\n'
        '</urlset>\n'
    )
    return Response(body, mimetype="application/xml")

setup_auth(server)


@server.before_request
def reject_unknown_dash_paths():
    """Return HTTP 404 instead of the Dash HTML shell for unknown URLs.

    Dash uses an index route with a path converter as a fallback. Only intercept
    requests matched to that fallback endpoint; explicit Flask routes and Dash
    API/component/static endpoints continue through their normal handlers.
    The root path remains available for the authenticated dashboard.
    """
    rule = request.url_rule
    if (
        rule is not None
        and rule.rule == "/<path:path>"
        and request.path != "/"
    ):
        return Response("Not Found\n", status=404, mimetype="text/plain")


def testar_database():
    try:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            resultado = cursor.fetchone()
            cursor.close()
            return resultado is not None
        finally:
            conn.close()
    except Exception as erro:
        print(f"[DATABASE ERROR] {erro}")
        traceback.print_exc()
        return False


def inicializar_database():
    try:
        resultado = initialize_database()
        if resultado:
            print("[DATABASE] Inicialização confirmada.")
        return resultado
    except Exception as erro:
        print(f"[DATABASE WARNING] {erro}")
        traceback.print_exc()
        return False


def carregar_dados():
    conn = None
    try:
        conn = get_connection()
        query = """
            SELECT
                dm.report_date AS data,
                sa.platform AS plataforma,
                sa.username AS username,
                dm.followers AS seguidores,
                dm.reach AS alcance,
                dm.impressions AS impressoes,
                dm.engagement_rate AS engajamento,
                dm.interactions_count AS interacoes,
                dm.profile_views AS visualizacoes_perfil,
                dm.website_clicks AS cliques_site
            FROM daily_metrics dm
            INNER JOIN social_accounts sa
                ON sa.id = dm.social_account_id
            INNER JOIN workspaces w
                ON w.id = sa.workspace_id
            WHERE w.slug = %s
              AND COALESCE(sa.is_active, TRUE) = TRUE
            ORDER BY dm.report_date ASC
        """
        workspace_slug = (session.get("workspace_slug") if has_request_context() else None) or os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
        dados = pd.read_sql_query(query, conn, params=(workspace_slug,))
        print(f"[DATABASE] {len(dados)} registos carregados.")
        return dados
    except Exception as erro:
        print(f"[DATABASE ERROR] Erro ao carregar dados: {erro}")
        traceback.print_exc()
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()


def carregar_insights_periodo(periodo_dias):
    """Carrega o último total oficial do período por conta Instagram no workspace."""
    conn = None
    try:
        conn = get_connection()
        workspace_slug = (
            session.get("workspace_slug") if has_request_context() else None
        ) or os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT SUM(reach), SUM(views), SUM(interactions), MAX(snapshot_date)
                FROM (
                    SELECT DISTINCT ON (m.social_account_id)
                        m.social_account_id, m.reach, m.views, m.interactions, m.snapshot_date
                    FROM instagram_period_snapshots m
                    INNER JOIN social_accounts sa ON sa.id = m.social_account_id
                    INNER JOIN workspaces w ON w.id = sa.workspace_id
                    WHERE w.slug = %s
                      AND POSITION('instagram' IN LOWER(sa.platform)) > 0
                      AND COALESCE(sa.is_active, TRUE) = TRUE
                      AND m.snapshot_date >= CURRENT_DATE - INTERVAL '2 days'
                      AND m.period_days = %s
                    ORDER BY m.social_account_id, m.snapshot_date DESC, m.created_at DESC
                ) latest
                """,
                (workspace_slug, int(periodo_dias or 7)),
            )
            row = cur.fetchone()
        return {
            "reach": int(row[0]) if row and row[0] is not None else None,
            "views": int(row[1]) if row and row[1] is not None else None,
            "interactions": int(row[2]) if row and row[2] is not None else None,
            "snapshot_date": row[3].isoformat() if row and row[3] else None,
        }
    except Exception as erro:
        print(f"[DATABASE] Totais oficiais do período indisponíveis: {type(erro).__name__}: {erro}")
        return {"reach": None, "views": None, "interactions": None, "snapshot_date": None}
    finally:
        if conn:
            conn.close()



def carregar_insights_facebook(periodo_dias=28):
    """Carrega totais Facebook reais: snapshot agregado de 28 dias ou agregação diária identificada."""
    conn = None
    try:
        conn = get_connection()
        workspace_slug = (
            session.get("workspace_slug") if has_request_context() else None
        ) or os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
        with conn.cursor() as cur:
            if int(periodo_dias or 7) == 28:
                cur.execute(
                    """
                    SELECT SUM(views), SUM(unique_viewers), SUM(interactions), MAX(snapshot_date)
                    FROM (
                        SELECT DISTINCT ON (m.social_account_id)
                            m.social_account_id, m.views, m.unique_viewers, m.interactions, m.snapshot_date
                        FROM facebook_period_snapshots m
                        INNER JOIN social_accounts sa ON sa.id=m.social_account_id
                        INNER JOIN workspaces w ON w.id=sa.workspace_id
                        WHERE w.slug=%s AND sa.platform='Facebook'
                          AND COALESCE(sa.is_active, TRUE)=TRUE
                          AND m.snapshot_date >= CURRENT_DATE - INTERVAL '2 days'
                          AND m.period_days=28
                        ORDER BY m.social_account_id, m.snapshot_date DESC, m.created_at DESC
                    ) latest
                    """,
                    (workspace_slug,),
                )
                row = cur.fetchone()
                return {
                    "views": int(row[0]) if row and row[0] is not None else None,
                    "reach": int(row[1]) if row and row[1] is not None else None,
                    "interactions": int(row[2]) if row and row[2] is not None else None,
                    "snapshot_date": row[3].isoformat() if row and row[3] else None,
                    "reach_label": "Pessoas únicas",
                }
            cur.execute(
                """
                SELECT SUM(dm.impressions), SUM(dm.reach), SUM(dm.interactions_count), MAX(dm.report_date)
                FROM daily_metrics dm
                INNER JOIN social_accounts sa ON sa.id=dm.social_account_id
                INNER JOIN workspaces w ON w.id=sa.workspace_id
                WHERE w.slug=%s AND sa.platform='Facebook'
                  AND COALESCE(sa.is_active, TRUE)=TRUE
                  AND dm.report_date >= CURRENT_DATE - (%s * INTERVAL '1 day')
                """,
                (workspace_slug, int(periodo_dias or 7)),
            )
            row = cur.fetchone()
        return {
            "views": int(row[0]) if row and row[0] is not None else None,
            "reach": int(row[1]) if row and row[1] is not None else None,
            "interactions": int(row[2]) if row and row[2] is not None else None,
            "snapshot_date": row[3].isoformat() if row and row[3] else None,
            "reach_label": "Soma do alcance diário",
        }
    except Exception as erro:
        print(f"[DATABASE] Insights Facebook indisponíveis: {type(erro).__name__}: {erro}")
        return {"views": None, "reach": None, "interactions": None, "snapshot_date": None,
                "reach_label": "Alcance"}
    finally:
        if conn:
            conn.close()


def carregar_insights_reels(periodo_dias):
    """Carrega o snapshot mais recente de Reels no workspace e janela selecionados."""
    conn = None
    try:
        conn = get_connection()
        workspace_slug = (
            session.get("workspace_slug") if has_request_context() else None
        ) or os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT SUM(views), SUM(reach), SUM(interactions), MAX(snapshot_date)
                FROM (
                    SELECT DISTINCT ON (m.social_account_id)
                        m.social_account_id, m.views, m.reach, m.interactions, m.snapshot_date
                    FROM instagram_reels_period_snapshots m
                    INNER JOIN social_accounts sa ON sa.id = m.social_account_id
                    INNER JOIN workspaces w ON w.id = sa.workspace_id
                    WHERE w.slug = %s AND sa.platform = 'Instagram'
                      AND COALESCE(sa.is_active, TRUE) = TRUE
                      AND m.snapshot_date >= CURRENT_DATE - INTERVAL '2 days'
                      AND m.period_days = %s
                    ORDER BY m.social_account_id, m.snapshot_date DESC, m.created_at DESC
                ) latest
                """,
                (workspace_slug, int(periodo_dias or 7)),
            )
            row = cur.fetchone()
        return {
            "views": int(row[0]) if row and row[0] is not None else None,
            "reach": int(row[1]) if row and row[1] is not None else None,
            "interactions": int(row[2]) if row and row[2] is not None else None,
            "snapshot_date": row[3].isoformat() if row and row[3] else None,
        }
    except Exception as erro:
        print(f"[DATABASE] Insights de Reels indisponíveis: {type(erro).__name__}: {erro}")
        return {"views": None, "reach": None, "interactions": None, "snapshot_date": None}
    finally:
        if conn:
            conn.close()


def carregar_insights_28d():
    """Carrega o snapshot mais recente de 28 dias por conta do workspace."""
    conn = None
    try:
        conn = get_connection()
        workspace_slug = (
            session.get("workspace_slug") if has_request_context() else None
        ) or os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
        query = """
            SELECT SUM(reach), SUM(views), SUM(interactions)
            FROM (
                SELECT DISTINCT ON (m.social_account_id)
                    m.social_account_id, m.reach, m.views, m.interactions
                FROM instagram_insights_28d m
                INNER JOIN social_accounts sa ON sa.id = m.social_account_id
                INNER JOIN workspaces w ON w.id = sa.workspace_id
                WHERE w.slug = %s
                  AND COALESCE(sa.is_active, TRUE) = TRUE
                  AND m.snapshot_date >= CURRENT_DATE - INTERVAL '2 days'
                ORDER BY m.social_account_id, m.snapshot_date DESC, m.created_at DESC
            ) latest
        """
        with conn.cursor() as cur:
            cur.execute(query, (workspace_slug,))
            row = cur.fetchone()
        valores = {
            "reach": int(row[0]) if row and row[0] is not None else None,
            "views": int(row[1]) if row and row[1] is not None else None,
            "interactions": int(row[2]) if row and row[2] is not None else None,
        }
        print(
            "[INSIGHTS 28D DEBUG] "
            f"workspace={workspace_slug!r} valores={valores!r}"
        )
        return valores
    except Exception as erro:
        print(
            "[DATABASE] Não foi possível carregar Insights de 28 dias: "
            f"{type(erro).__name__}: {erro}"
        )
        traceback.print_exc()
        return {"reach": None, "views": None, "interactions": None}
    finally:
        if conn:
            conn.close()


def preparar_dados(dados):
    if dados.empty:
        return dados

    dados = dados.copy()
    dados["data"] = pd.to_datetime(dados["data"], errors="coerce")
    dados["plataforma"] = dados["plataforma"].astype(str).str.strip()

    colunas_numericas = [
        "seguidores",
        "alcance",
        "impressoes",
        "engajamento",
        "interacoes",
        "visualizacoes_perfil",
        "cliques_site",
    ]

    for coluna in colunas_numericas:
        if coluna in dados.columns:
            dados[coluna] = pd.to_numeric(
                dados[coluna], errors="coerce"
            )

    dados = dados.dropna(subset=["data", "plataforma"])
    return dados.sort_values("data").reset_index(drop=True)


def dados_demonstrativos():
    """Retorna dados de demonstração somente quando explicitamente habilitados."""
    return pd.DataFrame({
        "data": pd.date_range(start="2026-01-01", periods=7, freq="D"),
        "plataforma": ["Instagram"] * 7,
        "username": ["@demo"] * 7,
        "seguidores": [1200, 1400, 1650, 1900, 2150, 2500, 2900],
        "alcance": [10000, 12500, 14500, 18000, 23000, 28000, 35000],
        "impressoes": [15000, 18000, 22000, 27000, 33000, 40000, 50000],
        "engajamento": [3.5, 4.2, 4.8, 5.2, 5.9, 6.4, 8.1],
        "visualizacoes_perfil": [150, 180, 210, 250, 300, 350, 430],
        "cliques_site": [20, 25, 30, 38, 45, 52, 65],
    })


def calcular_kpis(dados):
    """Calcula KPIs sem apresentar zeros como métricas válidas quando faltam dados da API."""
    if dados.empty:
        return {"seguidores": None, "alcance": None, "engajamento": None,
                "crescimento": None, "score": None}

    dados = dados.copy()
    dados["data"] = pd.to_datetime(dados["data"], errors="coerce")
    dados = dados.sort_values("data")

    for coluna in ("seguidores", "alcance", "engajamento"):
        dados[coluna] = pd.to_numeric(dados[coluna], errors="coerce")

    seguidores = dados["seguidores"].dropna()
    seguidores_atual = float(seguidores.iloc[-1]) if not seguidores.empty else None

    # Alcance zero com histórico de seguidores, mas sem insights disponíveis,
    # não permite distinguir ausência de dados de um zero real. Não o usamos
    # como prova de que a conta não alcançou ninguém.
    alcance_valido = dados.loc[dados["alcance"] > 0, "alcance"]
    alcance_atual = (
        float(dados.iloc[-1]["alcance"])
        if pd.notna(dados.iloc[-1]["alcance"]) and dados.iloc[-1]["alcance"] > 0
        else None
    )

    # Só interpretar engajamento em linhas com alcance positivo; uma taxa
    # sem denominador válido pode inflacionar o KPI e o Score IA.
    dados_validos = dados.loc[
        (dados["alcance"] > 0) & dados["engajamento"].notna()
    ].copy()
    engajamento = dados_validos["engajamento"]
    engajamento_medio = (
        round(float(engajamento.mean()), 2) if not engajamento.empty else None
    )

    crescimento = None
    historico_seguidores = dados[["data", "seguidores"]].dropna(
        subset=["data", "seguidores"]
    ).drop_duplicates(subset=["data"], keep="last").sort_values("data")

    # O KPI de crescimento deve usar o mesmo horizonte temporal de 28 dias
    # dos Insights. A janela é relativa à data mais recente disponível no
    # histórico, não à data do servidor, para evitar comparar períodos sem dados.
    if not historico_seguidores.empty:
        data_fim = historico_seguidores["data"].max()
        data_inicio = data_fim - pd.Timedelta(days=27)
        historico_seguidores = historico_seguidores.loc[
            historico_seguidores["data"].between(data_inicio, data_fim)
        ].sort_values("data")

    # Exigir cobertura real de 28 dias antes de publicar o KPI de crescimento.
    # Dois ou oito dias de dados não representam uma janela completa de 28 dias.
    cobertura_dias = (
        (historico_seguidores["data"].max() - historico_seguidores["data"].min()).days
        if not historico_seguidores.empty else 0
    )
    if (
        historico_seguidores["data"].nunique() >= 2
        and cobertura_dias >= 27
        and float(historico_seguidores["seguidores"].iloc[0]) > 0
    ):
        inicial = float(historico_seguidores["seguidores"].iloc[0])
        final = float(historico_seguidores["seguidores"].iloc[-1])
        crescimento = round(((final - inicial) / inicial) * 100, 2)

    score = None
    if (
        not dados_validos.empty
        and not seguidores.empty
        and not alcance_valido.empty
    ):
        try:
            score = calcular_score(dados_validos)
            if dados_validos["data"].nunique() < 2:
                print("[AI SCORE] Score preliminar: poucos dias com métricas de alcance válidas.")
        except Exception as erro:
            print(f"[AI SCORE ERROR] {type(erro).__name__}")

    return {
        "seguidores": int(seguidores_atual) if seguidores_atual is not None else None,
        "alcance": int(alcance_atual) if alcance_atual is not None else None,
        "engajamento": engajamento_medio,
        "crescimento": crescimento,
        "score": score,
    }


EN_TRANSLATIONS = {
    "Analytics • Métricas • IA": "Analytics • Metrics • AI",
    "Transforme os Seus Dados em Oportunidades de Negócio": "Turn Your Data into Business Opportunities",
    "🔗 Conectar Redes": "🔗 Connect Social Accounts", "💳 Plano": "💳 Plan", "🚪 Logout": "🚪 Log out",
    "Seguidores": "Followers", "Alcance": "Reach", "Taxa de interação": "Engagement rate",
    "Crescimento": "Growth", "Score IA": "AI Score",
    "📆 Instagram — resumo oficial dos últimos 28 dias": "📆 Instagram — official summary for the last 28 days",
    "📆 Facebook — resumo oficial dos últimos 28 dias": "📆 Facebook — official summary for the last 28 days",
    "Totais agregados oficiais do Instagram. O alcance é único para o período.": "Official aggregated Instagram totals. Reach is unique for the period.",
    "Totais agregados consultados na API oficial da Página do Facebook. Métricas não disponibilizadas pela API permanecem N/D.": "Aggregated totals retrieved from the official Facebook Page API. Metrics unavailable from the API remain N/A.",
    "📊 Instagram — totais oficiais por período": "📊 Instagram — official totals by period",
    "📊 Facebook — métricas por período": "📊 Facebook — metrics by period",
    "Período de análise": "Analysis period", "Últimos 7 dias": "Last 7 days", "Últimos 14 dias": "Last 14 days",
    "Últimos 30 dias": "Last 30 days", "Últimos 60 dias": "Last 60 days",
    "🔎 Selecionar plataforma": "🔎 Select platform", "🤖 Assistente IA": "🤖 AI Assistant",
    "📋 Relatório Executivo IA": "📋 AI Executive Report",
    "Visualizações (28 dias)": "Views (28 days)", "Interações (28 dias)": "Interactions (28 days)",
    "Alcance único (28 dias)": "Unique reach (28 days)", "Views registadas": "Recorded views",
    "Alcance diário acumulado": "Cumulative daily reach", "Interações registadas": "Recorded interactions",
    "Visualizações de Reels": "Reels views", "Alcance de Reels": "Reels reach", "Interações de Reels": "Reels interactions",
    "Sem dados disponíveis": "No data available", "Sem dados": "No data",
    "Visualizações oficiais": "Official views", "Alcance único oficial": "Official unique reach",
    "Interações oficiais": "Official interactions", "Última sincronização:": "Last synced:",
    "Os valores são totais agregados oficiais do Instagram.": "These are official aggregated Instagram totals.",
    "No Facebook, o alcance acumulado de dias não equivale a pessoas únicas no período.": "For Facebook, cumulative daily reach is not the same as unique people for the period.",
    "Selecione Instagram para consultar métricas de Reels.": "Select Instagram to view Reels metrics.",
    "A API pode ter atraso de até 48 horas.": "The API may be delayed by up to 48 hours.",
    "Ainda não existem snapshots oficiais de Reels para este período.": "There are no official Reels snapshots for this period yet.",
    "Aguarde a próxima sincronização automática; os valores não são estimados.": "Wait for the next automatic sync; values are not estimated.",
    "A aguardar dados oficiais da API.": "Waiting for official API data.",
    "Evolução de Seguidores": "Follower Growth", "Data": "Date", "Engajamento": "Engagement",
    "Engajamento (%)": "Engagement (%)", "Previsão de Seguidores": "Follower Forecast",
    "Seguidores previstos": "Predicted followers", "Previsão IA": "AI Forecast",
    "Relatório executivo indisponível.": "Executive report unavailable.",
    "⚠️ Não foi possível gerar o alerta.": "⚠️ Unable to generate the alert.",
    "💡 Não foi possível gerar a recomendação.": "💡 Unable to generate a recommendation.",
    "QUALIDADE E CONFIANÇA": "DATA QUALITY AND CONFIDENCE",
    "A taxa de interação apresentada é agregada para os últimos 28 dias.": "The displayed engagement rate is aggregated for the last 28 days.",
    "Histórico insuficiente: existe apenas um dia de dados.": "Insufficient history: data is available for only one day.",
    "O crescimento histórico não pode ser determinado e a previsão repete o valor mais recente; não deve ser interpretada como tendência.": "Historical growth cannot be determined and the forecast repeats the latest value; it should not be interpreted as a trend.",
    "Amostra reduzida (alcance inferior a 30): interpretar os indicadores como preliminares.": "Small sample (reach below 30): treat the indicators as preliminary.",
    "© 2026 PMW Consultoria & Tecnologia": "© 2026 PMW Consultoria & Tecnologia"
}
def traduzir_texto(valor):
    if not isinstance(valor, str):
        return valor
    for origem, destino in sorted(EN_TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True):
        valor = valor.replace(origem, destino)
    return valor

def traduzir_saida(valor, idioma):
    if idioma != "en":
        return valor
    if isinstance(valor, str):
        return traduzir_texto(valor)
    if isinstance(valor, (list, tuple)):
        convertido = [traduzir_saida(item, idioma) for item in valor]
        return tuple(convertido) if isinstance(valor, tuple) else convertido
    if isinstance(valor, dict):
        return {chave: traduzir_saida(item, idioma) for chave, item in valor.items()}
    if hasattr(valor, "children"):
        try:
            valor.children = traduzir_saida(valor.children, idioma)
            if hasattr(valor, "options") and valor.options:
                valor.options = traduzir_saida(valor.options, idioma)
        except Exception:
            pass
        return valor
    if hasattr(valor, "update_layout") and hasattr(valor, "layout"):
        try:
            if getattr(valor.layout.title, "text", None):
                valor.update_layout(title=traduzir_texto(valor.layout.title.text))
            for eixo in ("xaxis", "yaxis"):
                eixo_titulo = getattr(getattr(valor.layout, eixo), "title", None)
                if eixo_titulo and getattr(eixo_titulo, "text", None):
                    valor.update_layout(**{f"{eixo}.title.text": traduzir_texto(eixo_titulo.text)})
            for anotacao in getattr(valor.layout, "annotations", []) or []:
                if getattr(anotacao, "text", None):
                    anotacao.text = traduzir_texto(anotacao.text)
        except Exception:
            pass
        return valor
    return valor


def numero(valor):
    if valor is None or pd.isna(valor):
        return "N/D"
    try:
        return f"{float(valor):,.0f}"
    except (TypeError, ValueError):
        return "N/D"


def percentual(valor):
    if valor is None or pd.isna(valor):
        return "N/D"
    try:
        return f"{float(valor):.2f}%"
    except (TypeError, ValueError):
        return "N/D"


def criar_kpi(valor, titulo, cor, identificador, label_id=None):
    return html.Div(
        [
            html.H2(
                str(valor),
                id=identificador,
                style={
                    "color": cor,
                    "fontSize": "clamp(22px, 3vw, 30px)",
                    "fontWeight": "700",
                    "margin": "0 0 8px 0",
                    "overflowWrap": "anywhere",
                },
            ),
            html.P(
                titulo,
                **({"id": label_id} if label_id is not None else {}),
                style={
                    "color": COLORS["text"],
                    "fontSize": "14px",
                    "margin": "0",
                },
            ),
        ],
        style={
            "background": COLORS["card_kpi"],
            "padding": "22px 12px",
            "borderRadius": "16px",
            "width": "100%",
            "minWidth": "0",
            "boxSizing": "border-box",
            "textAlign": "center",
            "border": "1px solid rgba(148,163,184,0.10)",
            "boxShadow": "0 5px 18px rgba(0,0,0,0.22)",
        },
    )


def configurar_grafico(fig):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COLORS["card"],
        plot_bgcolor=COLORS["card"],
        font={"family": "Arial", "color": COLORS["text"]},
        margin={"l": 45, "r": 20, "t": 70, "b": 45},
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, automargin=True)
    fig.update_yaxes(gridcolor="rgba(148,163,184,0.12)", automargin=True)
    return fig


def grafico_sem_dados(titulo):
    fig = px.scatter(title=titulo)
    fig.add_annotation(
        text="Sem dados disponíveis",
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font={"color": COLORS["muted"], "size": 16},
    )
    return configurar_grafico(fig)


plataformas = ["Instagram"]
kpis_iniciais = calcular_kpis(pd.DataFrame())

app.layout = html.Div(
    [
        html.Div(
            [
                # Marca compacta à esquerda: logótipo, nome e slogan legíveis.
                html.Div(
                    [
                        html.Img(
                            src="/logo.png",
                            alt="Logótipo PMW Consultoria",
                            style={
                                "width": "58px", "height": "58px",
                                "objectFit": "cover", "objectPosition": "center",
                                "borderRadius": "10px", "background": "white",
                                "padding": "0", "flex": "0 0 58px",
                            },
                        ),
                        html.Div(
                            [
                                html.Div(
                                    "PMW Dashboard AI",
                                    style={
                                        "fontSize": "15px", "fontWeight": "700",
                                        "color": "white", "lineHeight": "1.25",
                                    },
                                ),
                                html.Div(
                                    "Analytics • Métricas • IA",
                                    id="marca-subtitulo",
                                    style={
                                        "fontSize": "11px", "fontWeight": "600",
                                        "color": COLORS["muted"], "marginTop": "3px",
                                    },
                                ),
                                html.Div(
                                    "Transforme os Seus Dados em Oportunidades de Negócio",
                                    id="marca-slogan",
                                    style={
                                        "fontSize": "11px", "lineHeight": "1.35",
                                        "fontWeight": "600", "color": COLORS["primary"],
                                        "maxWidth": "250px", "marginTop": "4px",
                                    },
                                ),
                            ],
                            style={"display": "flex", "flexDirection": "column", "minWidth": "0"},
                        ),
                    ],
                    style={
                        "display": "flex", "alignItems": "center", "gap": "12px",
                        "flex": "1 1 250px", "minWidth": "220px",
                    },
                ),
                # Título principal à direita da marca.
                html.H1(
                    "PMW Dashboard",
                    id="titulo-dashboard",
                    style={
                        "fontSize": "clamp(21px, 2.5vw, 30px)",
                        "textAlign": "right", "margin": "0",
                        "color": "white", "flex": "0 1 auto",
                    },
                ),
                html.Div(
                    [
                        html.Div([
                            html.Label("Idioma / Language", htmlFor="idioma", style={"color": COLORS["muted"], "fontSize": "11px", "display": "block", "marginBottom": "3px"}),
                            dcc.Dropdown(
                                id="idioma",
                                options=[{"label": "Português", "value": "pt"}, {"label": "English", "value": "en"}],
                                value="pt",
                                clearable=False,
                                searchable=False,
                                persistence=True,
                                persistence_type="session",
                                style={"color": "#111827", "minWidth": "125px"},
                            ),
                        ], style={"minWidth": "125px"}),
                        html.A(
                            "🔗 Conectar Redes",
                            id="botao-conectar",
                            href="/account#social-connections",
                            style={
                                "display": "inline-block",
                                "padding": "9px 14px",
                                "borderRadius": "9px",
                                "background": "#a855f7",
                                "color": "white",
                                "textDecoration": "none",
                                "fontWeight": "700",
                                "fontSize": "13px",
                            },
                        ),
                        html.A(
                            "💳 Plano",
                            id="botao-plano",
                            href="/billing",
                            style={
                                "display": "inline-block",
                                "padding": "9px 14px",
                                "borderRadius": "9px",
                                "background": COLORS["primary"],
                                "color": COLORS["background"],
                                "textDecoration": "none",
                                "fontWeight": "700",
                                "fontSize": "13px",
                            },
                        ),
                        html.A(
                            "🚪 Logout",
                            id="botao-logout",
                            href="/logout",
                            style={
                                "display": "inline-block",
                                "padding": "9px 14px",
                                "borderRadius": "9px",
                                "background": COLORS["danger"],
                                "color": "white",
                                "textDecoration": "none",
                                "fontWeight": "700",
                                "fontSize": "13px",
                            },
                        ),
                    ],
                    style={
                        "display": "flex", "alignItems": "center",
                        "justifyContent": "flex-end", "gap": "8px",
                        "flex": "0 1 auto", "flexWrap": "wrap",
                    },
                ),
            ],
            style={
                "display": "flex", "alignItems": "center",
                "justifyContent": "space-between", "gap": "18px",
                "flexWrap": "wrap", "marginBottom": "25px",
                "width": "100%",
            },
        ),

        html.Div(
            [
                criar_kpi(numero(kpis_iniciais["seguidores"]), "Seguidores", COLORS["primary"], "kpi-seguidores", "label-seguidores"),
                criar_kpi(numero(kpis_iniciais["alcance"]), "Alcance", COLORS["success"], "kpi-alcance", "label-alcance"),
                criar_kpi(percentual(kpis_iniciais["engajamento"]), "Taxa de interação (28 dias)", COLORS["warning"], "kpi-engajamento", "label-engajamento"),
                criar_kpi(percentual(kpis_iniciais["crescimento"]), "Crescimento", COLORS["purple"], "kpi-crescimento", "label-crescimento"),
                criar_kpi(f'{kpis_iniciais["score"]}/100' if kpis_iniciais["score"] is not None else "N/D", "Score IA*", COLORS["danger"], "kpi-score", "label-score"),
            ],
            style={
                "display": "grid",
                "gridTemplateColumns": "repeat(auto-fit, minmax(180px, 1fr))",
                "gap": "15px",
                "width": "100%",
                "marginBottom": "25px",
            },
        ),

        html.Div(
            [
                html.H3("📆 Instagram — resumo oficial dos últimos 28 dias", id="titulo-28d",
                        style={"color": "white", "margin": "0 0 12px"}),
                html.P(
                    "Totais agregados devolvidos pela API. O alcance deste bloco é único para o período e não deve ser substituído pela soma do alcance diário.",
                    id="descricao-28d", style={"color": COLORS["muted"], "margin": "0 0 16px"},
                ),
                html.Div(
                    id="kpis-28d",
                    children=[
                        criar_kpi("N/D", "Alcance único (28 dias)", COLORS["success"], "kpi-alcance-28d"),
                        criar_kpi("N/D", "Visualizações (28 dias)", COLORS["primary"], "kpi-views-28d"),
                        criar_kpi("N/D", "Interações (28 dias)", COLORS["warning"], "kpi-interacoes-28d"),
                    ],
                    style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(180px, 1fr))",
                           "gap": "15px", "width": "100%"},
                ),
            ],
            style={"background": COLORS["card"], "padding": "20px", "borderRadius": "16px",
                   "marginBottom": "20px", "border": "1px solid rgba(148,163,184,0.10)"},
        ),

        html.Div(
            [
                html.H3("📊 Instagram — totais oficiais por período", id="titulo-periodo", style={"color": "white", "margin": "0 0 12px"}),
                html.P(
                    "Valores agregados consultados na API oficial para a janela selecionada. Não são somas de snapshots diários; se a API não devolver um total válido, o cartão mostra N/D.",
                    id="descricao-periodo", style={"color": COLORS["muted"], "margin": "0 0 14px"},
                ),
                html.Label("Período de análise", id="label-periodo", style={"color": COLORS["text"], "fontWeight": "600", "display": "block", "marginBottom": "8px"}),
                dcc.Dropdown(
                    id="periodo-insights",
                    options=[
                        {"label": "Últimos 7 dias", "value": 7},
                        {"label": "Últimos 14 dias", "value": 14},
                        {"label": "Últimos 30 dias", "value": 30},
                        {"label": "Últimos 60 dias", "value": 60},
                    ],
                    value=7,
                    clearable=False,
                    searchable=False,
                    style={"color": "#111827", "maxWidth": "320px", "marginBottom": "16px"},
                ),
                html.Div(
                    id="kpis-periodo",
                    children=[
                        criar_kpi("N/D", "Views registadas", COLORS["primary"], "periodo-views"),
                        criar_kpi("N/D", "Alcance diário acumulado", COLORS["success"], "periodo-reach"),
                        criar_kpi("N/D", "Interações registadas", COLORS["warning"], "periodo-interacoes"),
                    ],
                    style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(180px, 1fr))", "gap": "15px", "width": "100%"},
                ),
                html.P(id="nota-periodo", children="Selecione um período para consultar o histórico disponível.", style={"color": COLORS["muted"], "margin": "12px 0 0"}),
            ],
            style={"background": COLORS["card"], "padding": "20px", "borderRadius": "16px", "marginBottom": "20px", "border": "1px solid rgba(148,163,184,0.10)"},
        ),

        html.Div(
            [
                html.H3("🎬 Instagram Reels — métricas específicas", id="titulo-reels", style={"color": "white", "margin": "0 0 12px"}),
                html.P(
                    "Este bloco fica separado das métricas gerais da conta e consulta o breakdown oficial da API por tipo de conteúdo (REELS). Se a API ou as permissões não disponibilizarem esse breakdown, os valores permanecem N/D; não são copiados nem inferidos a partir dos totais gerais.",
                    id="descricao-reels", style={"color": COLORS["muted"], "margin": "0 0 16px"},
                ),
                html.Div(id="kpis-reels", children=[
                    criar_kpi("N/D", "Visualizações de Reels", COLORS["primary"], "reels-views"),
                    criar_kpi("N/D", "Alcance de Reels", COLORS["success"], "reels-reach"),
                    criar_kpi("N/D", "Interações de Reels", COLORS["warning"], "reels-interactions"),
                ], style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(180px, 1fr))", "gap": "15px", "width": "100%"}),
                html.P(id="nota-reels", children="A aguardar dados oficiais da API.", style={"color": COLORS["muted"], "margin": "12px 0 0"}),
                html.P("Os valores abaixo são totais oficiais de Reels para a janela selecionada; não são a soma dos valores vitalícios de cada vídeo.", id="nota-explicativa-reels", style={"color": COLORS["muted"], "margin": "12px 0 0"}),
            ],
            id="secao-reels",
            style={"background": COLORS["card"], "padding": "20px", "borderRadius": "16px", "marginBottom": "20px", "border": "1px solid rgba(148,163,184,0.10)"},
        ),

        html.Div(
            [
                html.Label(
                    "🔎 Selecionar plataforma",
                    id="label-plataforma",
                    style={
                        "color": COLORS["text"],
                        "fontWeight": "600",
                        "display": "block",
                        "marginBottom": "8px",
                    },
                ),
                dcc.Dropdown(
                    id="plataforma",
                    options=[{"label": p, "value": p} for p in plataformas],
                    value=plataformas[0],
                    clearable=False,
                    searchable=True,
                ),
            ],
            style={
                "background": COLORS["card"],
                "padding": "20px",
                "borderRadius": "16px",
                "marginBottom": "20px",
                "border": "1px solid rgba(148,163,184,0.10)",
            },
        ),

        html.Div(
            [
                html.H2("🤖 Assistente IA", id="titulo-assistente", style={"color": COLORS["primary"], "marginTop": "0"}),
                html.Div(id="alerta-ia", style={"color": "#facc15", "whiteSpace": "pre-wrap", "lineHeight": "1.7"}),
                html.Div(id="recomendacao-ia", style={"color": COLORS["success"], "whiteSpace": "pre-wrap", "lineHeight": "1.7", "marginTop": "10px"}),
            ],
            style={
                "background": "#172554",
                "padding": "22px",
                "borderRadius": "16px",
                "marginBottom": "20px",
                "border": "1px solid rgba(56,189,248,0.15)",
            },
        ),

        html.Div(
            [
                html.H2("📋 Relatório Executivo IA", id="titulo-relatorio", style={"marginTop": "0", "color": "white"}),
                html.Pre(
                    id="relatorio-ia",
                    style={
                        "whiteSpace": "pre-wrap",
                        "overflowWrap": "anywhere",
                        "color": "#e2e8f0",
                        "fontFamily": "Arial, sans-serif",
                        "fontSize": "14px",
                        "lineHeight": "1.6",
                        "margin": "0",
                    },
                ),
            ],
            style={
                "background": COLORS["card"],
                "padding": "22px",
                "borderRadius": "16px",
                "marginBottom": "20px",
            },
        ),

        html.Div(
            [
                html.Div(dcc.Graph(id="grafico_seguidores", config={"responsive": True, "displaylogo": False}, style={"height": "380px"})),
                html.Div(dcc.Graph(id="grafico_alcance", config={"responsive": True, "displaylogo": False}, style={"height": "380px"})),
                html.Div(dcc.Graph(id="grafico_engajamento", config={"responsive": True, "displaylogo": False}, style={"height": "380px"})),
                html.Div(dcc.Graph(id="grafico_previsao", config={"responsive": True, "displaylogo": False}, style={"height": "380px"})),
            ],
            style={
                "display": "grid",
                "gridTemplateColumns": "repeat(auto-fit, minmax(400px, 1fr))",
                "gap": "20px",
                "width": "100%",
            },
        ),

        html.Hr(style={"borderColor": COLORS["border"], "marginTop": "35px"}),
        html.P(
            "© 2026 PMW Consultoria & Tecnologia",
            id="rodape-dashboard",
            style={"textAlign": "center", "color": COLORS["muted"], "fontSize": "13px", "padding": "15px"},
        ),
    ],
    style={
        "backgroundColor": COLORS["background"],
        "minHeight": "100vh",
        "width": "100%",
        "boxSizing": "border-box",
        "padding": "clamp(12px, 2vw, 28px)",
        "fontFamily": "Arial, sans-serif",
        "overflowX": "hidden",
    },
)


@app.callback(
    [
        Output("marca-subtitulo", "children"),
        Output("marca-slogan", "children"),
        Output("titulo-dashboard", "children"),
        Output("botao-conectar", "children"),
        Output("botao-plano", "children"),
        Output("botao-logout", "children"),
        Output("label-seguidores", "children"),
        Output("label-alcance", "children"),
        Output("label-engajamento", "children"),
        Output("label-crescimento", "children"),
        Output("label-score", "children"),
        Output("label-periodo", "children"),
        Output("label-plataforma", "children"),
        Output("titulo-assistente", "children"),
        Output("titulo-relatorio", "children"),
        Output("nota-explicativa-reels", "children"),
        Output("rodape-dashboard", "children"),
        Output("periodo-insights", "options"),
        Output("kpis-28d", "children"),
        Output("titulo-28d", "children"),
        Output("descricao-28d", "children"),
        Output("titulo-periodo", "children"),
        Output("descricao-periodo", "children"),
        Output("titulo-reels", "children"),
        Output("descricao-reels", "children"),
        Output("secao-reels", "style"),
        Output("kpis-periodo", "children"),
        Output("nota-periodo", "children"),
        Output("kpis-reels", "children"),
        Output("nota-reels", "children"),
        Output("plataforma", "options"),
        Output("plataforma", "value"),
        Output("kpi-seguidores", "children"),
        Output("kpi-alcance", "children"),
        Output("kpi-engajamento", "children"),
        Output("kpi-crescimento", "children"),
        Output("kpi-score", "children"),
        Output("alerta-ia", "children"),
        Output("recomendacao-ia", "children"),
        Output("relatorio-ia", "children"),
        Output("grafico_seguidores", "figure"),
        Output("grafico_alcance", "figure"),
        Output("grafico_engajamento", "figure"),
        Output("grafico_previsao", "figure"),
    ],
    [Input("plataforma", "value"), Input("periodo-insights", "value"), Input("idioma", "value")],
)
def atualizar(plataforma, periodo_insights, idioma="pt"):
    workspace_dados = preparar_dados(carregar_dados())

    if workspace_dados.empty and os.getenv("ALLOW_DEMO_DATA", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        print("[INFO] Nenhum dado encontrado no workspace atual. A utilizar dados demonstrativos.")
        workspace_dados = dados_demonstrativos()

    plataformas_atuais = sorted(
        workspace_dados["plataforma"].dropna().astype(str).unique().tolist()
    ) if "plataforma" in workspace_dados.columns else []

    if not plataformas_atuais:
        plataformas_atuais = ["Instagram"]

    plataforma_atual = str(plataforma) if plataforma in plataformas_atuais else plataformas_atuais[0]
    dados = workspace_dados[workspace_dados["plataforma"].astype(str) == plataforma_atual].copy()
    dados = dados.sort_values("data")

    # Totais oficiais agregados por janela, separados dos registos diários.
    dias_periodo = int(periodo_insights or 7)
    is_instagram = plataforma_atual.lower().startswith("instagram")
    is_facebook = plataforma_atual.lower().startswith("facebook")
    snapshot_periodo = (
        carregar_insights_periodo(dias_periodo) if is_instagram
        else carregar_insights_facebook(dias_periodo) if is_facebook
        else {"reach": None, "views": None, "interactions": None, "snapshot_date": None, "reach_label": "Alcance"}
    )
    titulo_28d = "📆 Instagram — resumo oficial dos últimos 28 dias" if is_instagram else "📆 Facebook — resumo oficial dos últimos 28 dias"
    descricao_28d = "Totais agregados oficiais do Instagram. O alcance é único para o período." if is_instagram else "Totais agregados consultados na API oficial da Página do Facebook. Métricas não disponibilizadas pela API permanecem N/D."
    titulo_periodo = "📊 Instagram — totais oficiais por período" if is_instagram else "📊 Facebook — métricas por período"
    descricao_periodo = "Valores agregados consultados na API oficial; não são somas de snapshots diários." if is_instagram else "Valores reais da API do Facebook. Para janelas diferentes de 28 dias, os valores disponíveis são agregados dos registos diários; o alcance acumulado não representa pessoas únicas no período."
    titulo_reels = "🎬 Instagram Reels — métricas específicas"
    descricao_reels = "Este bloco consulta métricas oficiais de Reels do Instagram. Valores indisponíveis na API permanecem N/D."
    estilo_reels = {
        "display": "block" if is_instagram else "none",
        "background": COLORS["card"],
        "padding": "20px",
        "borderRadius": "16px",
        "marginBottom": "20px",
        "border": "1px solid rgba(148,163,184,0.10)",
    }
    def fmt_periodo(value):
        return numero(value) if value is not None else "N/D"
    cards_historico = [
        criar_kpi(fmt_periodo(snapshot_periodo["views"]), f"Visualizações oficiais ({dias_periodo} dias)", COLORS["primary"], "periodo-views"),
        criar_kpi(fmt_periodo(snapshot_periodo["reach"]), f"{snapshot_periodo.get('reach_label', 'Alcance único oficial')} ({dias_periodo} dias)", COLORS["success"], "periodo-reach"),
        criar_kpi(fmt_periodo(snapshot_periodo["interactions"]), f"Interações oficiais ({dias_periodo} dias)", COLORS["warning"], "periodo-interacoes"),
    ]
    if snapshot_periodo["snapshot_date"]:
        nota_historico = (
            f"Totais agregados da API oficial para a janela de {dias_periodo} dias. "
            f"Última sincronização: {snapshot_periodo['snapshot_date']}. "
            + ("Os valores são totais agregados oficiais do Instagram." if is_instagram else "No Facebook, o alcance acumulado de dias não equivale a pessoas únicas no período.")
        )
    else:
        nota_historico = (
            f"A API ainda não devolveu totais válidos para a janela de {dias_periodo} dias. "
            + ("Confirme a permissão read_insights da aplicação Meta e consulte os logs de sincronização." if is_facebook else "Os valores permanecem N/D até à próxima sincronização; não são estimados.")
        )

    reels = carregar_insights_reels(dias_periodo) if plataforma_atual == "Instagram" else {
        "views": None, "reach": None, "interactions": None, "snapshot_date": None
    }
    reels_cards = [
        criar_kpi(numero(reels["views"]), f"Visualizações de Reels ({dias_periodo} dias)", COLORS["primary"], "reels-views"),
        criar_kpi(numero(reels["reach"]), f"Alcance de Reels ({dias_periodo} dias)", COLORS["success"], "reels-reach"),
        criar_kpi(numero(reels["interactions"]), f"Interações de Reels ({dias_periodo} dias)", COLORS["warning"], "reels-interactions"),
    ]
    if plataforma_atual != "Instagram":
        reels_note = "Selecione Instagram para consultar métricas de Reels."
    elif reels["snapshot_date"]:
        reels_note = (
            f"Dados oficiais da API por tipo de conteúdo (REELS), janela de {dias_periodo} dias. "
            f"Última sincronização: {reels['snapshot_date']}. A API pode ter atraso de até 48 horas."
        )
    else:
        reels_note = (
            "Ainda não existem snapshots oficiais de Reels para este período. "
            "Aguarde a próxima sincronização automática; os valores não são estimados."
        )

    # Consolidar várias contas da mesma plataforma por dia evita comparar
    # contas diferentes como se fossem dias consecutivos de uma única conta.
    if not dados.empty:
        colunas_soma = [
            coluna for coluna in (
                "seguidores", "alcance", "impressoes", "interacoes",
                "visualizacoes_perfil", "cliques_site"
            ) if coluna in dados.columns
        ]
        diario = dados.groupby("data", as_index=False)[colunas_soma].sum(min_count=1)
        taxa_calculada = pd.Series(index=diario.index, dtype="float64")
        if "interacoes" in diario.columns and "alcance" in diario.columns:
            taxa_calculada = (
                diario["interacoes"] / diario["alcance"] * 100
            ).where(diario["alcance"] > 0)

        if "engajamento" in dados.columns:
            # Se interações e alcance existem, usar apenas a taxa recalculada
            # a partir dos totais do mesmo dia. Não preencher taxas inválidas
            # com valores antigos potencialmente calculados noutro período.
            tem_interacoes_e_alcance = (
                "interacoes" in dados.columns
                and "alcance" in dados.columns
                and pd.to_numeric(dados["interacoes"], errors="coerce").notna().any()
                and pd.to_numeric(dados["alcance"], errors="coerce").notna().any()
            )
            if tem_interacoes_e_alcance:
                diario["engajamento"] = taxa_calculada
            else:
                ponderados = dados.copy()
                ponderados["peso_engajamento"] = pd.to_numeric(
                    ponderados["alcance"], errors="coerce"
                ).clip(lower=0)
                taxa = pd.to_numeric(ponderados["engajamento"], errors="coerce")
                ponderados["_num"] = taxa * ponderados["peso_engajamento"]
                ponderados["_den"] = ponderados["peso_engajamento"].where(taxa.notna())
                pesos = ponderados.groupby("data")[["_num", "_den"]].sum(min_count=1)
                taxa_ponderada = (
                    pesos["_num"] / pesos["_den"].where(pesos["_den"] > 0)
                ).reindex(diario["data"])
                diario["engajamento"] = taxa_ponderada.to_numpy()
        dados = diario.sort_values("data").reset_index(drop=True)

    if dados.empty:
        vazio = grafico_sem_dados("Sem dados")
        insights_28d = carregar_insights_28d() if is_instagram else carregar_insights_facebook(28) if is_facebook else {"reach": None, "views": None, "interactions": None}
        cards_28d = [
            criar_kpi(numero(insights_28d["reach"]), "Alcance (28 dias)" if is_instagram else "Pessoas únicas (28 dias)", COLORS["success"], "kpi-alcance-28d"),
            criar_kpi(numero(insights_28d["views"]), "Visualizações (28 dias)", COLORS["primary"], "kpi-views-28d"),
            criar_kpi(numero(insights_28d["interactions"]), "Interações (28 dias)", COLORS["warning"], "kpi-interacoes-28d"),
        ]
        alcance_28d = insights_28d.get("reach") if (is_instagram or is_facebook) else None
        interacoes_28d = insights_28d.get("interactions") if (is_instagram or is_facebook) else None
        engajamento_28d = (
            round(interacoes_28d / alcance_28d * 100, 2)
            if alcance_28d is not None and alcance_28d > 0 and interacoes_28d is not None
            else None
        )
        # Sem registos diários, usar o snapshot de 28 dias para os KPIs que
        # têm correspondência directa. Crescimento continua N/D sem histórico.
        return traduzir_saida((
            "Analytics • Métricas • IA",
            "Transforme os Seus Dados em Oportunidades de Negócio",
            "PMW Dashboard",
            "🔗 Conectar Redes",
            "💳 Plano",
            "🚪 Logout",
            "Seguidores",
            "Alcance",
            "Taxa de interação (28 dias)",
            "Crescimento",
            "Score IA*",
            "Período de análise",
            "🔎 Selecionar plataforma",
            "🤖 Assistente IA",
            "📋 Relatório Executivo IA",
            "Os valores abaixo são totais oficiais de Reels para a janela selecionada; não são a soma dos valores vitalícios de cada vídeo.",
            "© 2026 PMW Consultoria & Tecnologia",
            [{"label": traduzir_texto(item["label"]) if idioma == "en" else item["label"], "value": item["value"]} for item in [
                {"label": "Últimos 7 dias", "value": 7}, {"label": "Últimos 14 dias", "value": 14},
                {"label": "Últimos 30 dias", "value": 30}, {"label": "Últimos 60 dias", "value": 60}
            ]],
            cards_28d,
            titulo_28d,
            descricao_28d,
            titulo_periodo,
            descricao_periodo,
            titulo_reels,
            descricao_reels,
            estilo_reels,
            cards_historico,
            nota_historico,
            reels_cards,
            reels_note,
            [{"label": p, "value": p} for p in plataformas_atuais],
            plataforma_atual,
            "N/D",
            numero(alcance_28d),
            percentual(engajamento_28d),
            "N/D",
            "N/D",
            "⚠️ Existem Insights agregados, mas ainda não há histórico diário suficiente para avaliar tendências.",
            "💡 Continue a sincronizar diariamente para calcular crescimento e um Score IA fiável.",
            "Relatório indisponível: existem métricas agregadas dos últimos 28 dias, mas não há histórico diário suficiente para análise temporal.",
            vazio, vazio, vazio, vazio,
        ), idioma)

    kpis = calcular_kpis(dados)
    insights_28d = carregar_insights_28d() if is_instagram else carregar_insights_facebook(28) if is_facebook else {"reach": None, "views": None, "interactions": None}

    # Os cards principais de alcance e taxa de interação usam o snapshot da
    # API dos últimos 28 dias. A taxa é interações/alcance agregados, não a
    # média aritmética das percentagens diárias.
    alcance_28d = insights_28d.get("reach") if (is_instagram or is_facebook) else None
    interacoes_28d = insights_28d.get("interactions") if (is_instagram or is_facebook) else None
    engajamento_28d = (
        round(interacoes_28d / alcance_28d * 100, 2)
        if alcance_28d is not None and alcance_28d > 0 and interacoes_28d is not None
        else None
    )
    if alcance_28d is not None:
        kpis["alcance"] = alcance_28d
        kpis["engajamento"] = engajamento_28d

    # O Score IA exige pelo menos 14 dias distintos com alcance positivo e
    # taxa válida. O motor de IA aplica o mesmo limiar; não exibir 0/100
    # como se fosse uma avaliação real quando a amostra é insuficiente.
    dias_com_metricas_validas = dados.loc[
        (pd.to_numeric(dados["alcance"], errors="coerce") > 0)
        & pd.to_numeric(dados["engajamento"], errors="coerce").notna(),
        "data",
    ].nunique()
    if kpis.get("alcance") is None or dias_com_metricas_validas < 14:
        kpis["score"] = None

    try:
        alerta = gerar_alerta(dados)
        alcance_atual = kpis.get("alcance")
        engajamento_atual = kpis.get("engajamento")
        avisos_qualidade = []
        if alcance_atual is not None and alcance_atual < 30:
            avisos_qualidade.append(
                "⚠️ Amostra reduzida: o alcance diário é inferior a 30; "
                "interprete a taxa de engajamento e o Score IA com cautela."
            )
        if engajamento_atual is not None and engajamento_atual > 100:
            avisos_qualidade.append(
                "🔎 A taxa de engajamento ultrapassa 100%. Isto pode ocorrer "
                "quando há mais interações do que contas alcançadas, mas confirme "
                "se alcance e interações correspondem ao mesmo período."
            )
        if avisos_qualidade:
            alerta = "\n".join(avisos_qualidade + [str(alerta)])
    except Exception as erro:
        print(f"[AI ALERT ERROR] {erro}")
        alerta = "⚠️ Não foi possível gerar o alerta."

    try:
        recomendacao = gerar_recomendacao(dados)
    except Exception as erro:
        print(f"[AI RECOMMENDATION ERROR] {erro}")
        recomendacao = "💡 Não foi possível gerar a recomendação."

    try:
        relatorio = gerar_relatorio_executivo(dados)
        if dias_com_metricas_validas < 14:
            relatorio += (
                "\n\nQUALIDADE E CONFIANÇA\n"
                f"Score IA não apresentado: existem {dias_com_metricas_validas} dias distintos "
                "com alcance positivo e taxa válida; são necessários pelo menos 14. "
                "A taxa de interação apresentada é agregada para os últimos 28 dias.\n"
            )
        if dados["data"].nunique() < 2:
            relatorio += (
                "\n\nQUALIDADE E CONFIANÇA\n"
                "Histórico insuficiente: existe apenas um dia de dados. "
                "O crescimento histórico não pode ser determinado e a previsão "
                "repete o valor mais recente; não deve ser interpretada como tendência.\n"
            )
        if kpis.get("alcance") is not None and kpis["alcance"] < 30:
            relatorio += (
                "Amostra reduzida (alcance inferior a 30): interpretar os "
                "indicadores como preliminares.\n"
            )
        if kpis.get("engajamento") is not None and kpis["engajamento"] > 100:
            relatorio += (
                "Validação recomendada: engajamento superior a 100%; confirmar "
                "que interações e alcance usam o mesmo período de referência.\n"
            )
    except Exception as erro:
        print(f"[AI REPORT ERROR] {erro}")
        relatorio = "Relatório executivo indisponível."

    fig1 = px.line(
        dados,
        x="data",
        y="seguidores",
        markers=True,
        title=f"📈 Evolução de Seguidores — {plataforma}",
        labels={"data": "Data", "seguidores": "Seguidores"},
    )
    fig1.update_traces(line={"width": 3}, marker={"size": 7})
    configurar_grafico(fig1)

    fig2 = px.bar(
        dados,
        x="data",
        y="alcance",
        title=f"📊 Alcance — {plataforma}",
        labels={"data": "Data", "alcance": "Alcance"},
    )
    configurar_grafico(fig2)

    fig3 = px.area(
        dados,
        x="data",
        y="engajamento",
        markers=True,
        title=f"💬 Engajamento — {plataforma}",
        labels={"data": "Data", "engajamento": "Engajamento (%)"},
    )
    configurar_grafico(fig3)

    try:
        previsao = prever_crescimento(dados, dias=7)
        datas = pd.date_range(
            start=dados["data"].max() + pd.Timedelta(days=1),
            periods=len(previsao),
            freq="D",
        )
        fig4 = px.line(
            x=datas,
            y=previsao,
            markers=True,
            title=f"🤖 Previsão de Seguidores — {plataforma}",
            labels={"x": "Data", "y": "Seguidores previstos"},
        )
        fig4.update_traces(
            line={"width": 3, "dash": "dash"},
            marker={"size": 8},
        )
        configurar_grafico(fig4)
    except Exception as erro:
        print(f"[AI FORECAST ERROR] {erro}")
        fig4 = grafico_sem_dados("🤖 Previsão IA")

    cards_28d = [
        criar_kpi(numero(insights_28d["reach"]), "Alcance (28 dias)" if is_instagram else "Pessoas únicas (28 dias)", COLORS["success"], "kpi-alcance-28d"),
        criar_kpi(numero(insights_28d["views"]), "Visualizações (28 dias)", COLORS["primary"], "kpi-views-28d"),
        criar_kpi(numero(insights_28d["interactions"]), "Interações (28 dias)", COLORS["warning"], "kpi-interacoes-28d"),
    ]

    return traduzir_saida((
        "Analytics • Métricas • IA",
        "Transforme os Seus Dados em Oportunidades de Negócio",
        "PMW Dashboard",
        "🔗 Conectar Redes",
        "💳 Plano",
        "🚪 Logout",
        "Seguidores",
        "Alcance",
        "Taxa de interação (28 dias)",
        "Crescimento",
        "Score IA*",
        "Período de análise",
        "🔎 Selecionar plataforma",
        "🤖 Assistente IA",
        "📋 Relatório Executivo IA",
        "Os valores abaixo são totais oficiais de Reels para a janela selecionada; não são a soma dos valores vitalícios de cada vídeo.",
        "© 2026 PMW Consultoria & Tecnologia",
        [{"label": traduzir_texto(item["label"]) if idioma == "en" else item["label"], "value": item["value"]} for item in [
            {"label": "Últimos 7 dias", "value": 7}, {"label": "Últimos 14 dias", "value": 14},
            {"label": "Últimos 30 dias", "value": 30}, {"label": "Últimos 60 dias", "value": 60}
        ]],
        cards_28d,
        titulo_28d,
        descricao_28d,
        titulo_periodo,
        descricao_periodo,
        titulo_reels,
        descricao_reels,
        estilo_reels,
        cards_historico,
        nota_historico,
        reels_cards,
        reels_note,
        [{"label": p, "value": p} for p in plataformas_atuais],
        plataforma_atual,
        numero(kpis["seguidores"]),
        numero(kpis["alcance"]),
        percentual(kpis["engajamento"]),
        percentual(kpis["crescimento"]),
        f'{kpis["score"]}/100*' if kpis["score"] is not None and dados["data"].nunique() < 2 else (f'{kpis["score"]}/100' if kpis["score"] is not None else "N/D"),
        str(alerta),
        str(recomendacao),
        str(relatorio),
        fig1, fig2, fig3, fig4,
    ), idioma)


if __name__ == "__main__":
    inicializar_database()
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
