import os
import traceback

import pandas as pd
import plotly.express as px
import dash

from dash import html, dcc
from flask import Response, has_request_context, session
from dash.dependencies import Input, Output
from dotenv import load_dotenv

from database import get_connection, create_tables
from auth import setup_auth

from ai_engine import (
    prever_crescimento,
    gerar_alerta,
    gerar_recomendacao,
    calcular_score,
    gerar_relatorio_executivo,
)

load_dotenv()

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
    a{color:inherit}.wrap{width:min(1120px,92%);margin:auto}.nav{display:flex;justify-content:space-between;align-items:center;gap:18px;padding:22px 0}.brand{font-weight:800;font-size:18px}.navlinks{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.btn{display:inline-block;text-decoration:none;font-weight:700;padding:11px 17px;border-radius:10px;background:var(--blue);color:#06111f}.btn.secondary{background:transparent;border:1px solid #456078;color:var(--text)}
    .hero{padding:72px 0 64px;max-width:850px}.eyebrow{color:var(--blue);font-weight:800;letter-spacing:.12em;font-size:12px;text-transform:uppercase}.hero h1{font-size:clamp(36px,6vw,64px);line-height:1.08;letter-spacing:-.04em;margin:18px 0}.hero p{font-size:19px;color:var(--muted);max-width:720px}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:28px}
    .section{padding:42px 0}.section h2{font-size:clamp(26px,4vw,36px);margin:0 0 12px}.intro{color:var(--muted);max-width:760px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-top:24px}.card{background:rgba(17,24,39,.86);border:1px solid var(--line);border-radius:16px;padding:22px}.card h3{margin:8px 0}.card p{color:var(--muted);margin-bottom:0}.symbol{font-size:25px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:30px}.metric{padding:18px;border-radius:14px;background:#0b1730;border:1px solid #1d3554}.metric strong{display:block;color:var(--blue);font-size:21px}.metric span{color:var(--muted);font-size:13px}.cta{margin:45px 0;padding:30px;border-radius:18px;border:1px solid #1d4562;background:linear-gradient(120deg,#0c2440,#111827)}footer{border-top:1px solid var(--line);padding:24px 0;color:var(--muted);font-size:13px}
    @media(max-width:600px){.nav{align-items:flex-start;flex-direction:column}.hero{padding:46px 0 35px}.hero p{font-size:17px}}
  </style>
</head>
<body>
<header class="wrap nav">
  <div class="brand">📊 PMW Dashboard AI</div>
  <nav class="navlinks" aria-label="Navegação principal">
    <a class="btn secondary" href="/login">Entrar</a>
    <a class="btn" href="/signup">Criar conta</a>
  </nav>
</header>
<main class="wrap">
  <section class="hero">
    <div class="eyebrow">Analytics • Métricas • Inteligência Artificial</div>
    <h1>Transforme métricas de redes sociais em decisões mais informadas.</h1>
    <p>Consulte indicadores de desempenho, acompanhe tendências e obtenha apoio para interpretar resultados num dashboard concebido para tornar a análise de redes sociais mais clara e prática.</p>
    <div class="actions">
      <a class="btn" href="/signup">Começar o trial de 30 dias</a>
      <a class="btn secondary" href="/login">Já tenho uma conta</a>
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
      <article class="card"><div class="symbol">🔐</div><h3>Acesso protegido</h3><p>O dashboard e os dados da conta exigem autenticação. A ligação ao Instagram utiliza o fluxo oficial de autorização configurado no serviço.</p></article>
    </div>
  </section>
  <section class="cta">
    <h2>Conheça melhor o desempenho das suas redes sociais.</h2>
    <p class="intro">Crie uma conta para aceder ao dashboard. A disponibilidade dos indicadores depende dos dados e das integrações configuradas.</p>
    <div class="actions"><a class="btn" href="/signup">Criar conta</a><a class="btn secondary" href="/login">Entrar no dashboard</a></div>
  </section>
</main>
<footer><div class="wrap">© 2026 PMW Consultoria &amp; Tecnologia · PMW Social Media Dashboard AI</div></footer>
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
        create_tables()
        print("[DATABASE] Tabelas verificadas com sucesso.")
        return True
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
                dm.profile_views AS visualizacoes_perfil,
                dm.website_clicks AS cliques_site
            FROM daily_metrics dm
            INNER JOIN social_accounts sa
                ON sa.id = dm.social_account_id
            INNER JOIN workspaces w
                ON w.id = sa.workspace_id
            WHERE w.slug = %s
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
    if dados.empty:
        return {
            "seguidores": 0,
            "alcance": 0,
            "engajamento": 0,
            "crescimento": 0,
            "score": 0,
        }

    dados = dados.sort_values("data")
    seguidores = pd.to_numeric(dados["seguidores"], errors="coerce").dropna()
    alcance = pd.to_numeric(dados["alcance"], errors="coerce").dropna()
    engajamento = pd.to_numeric(dados["engajamento"], errors="coerce").dropna()

    primeiro = float(seguidores.iloc[0]) if not seguidores.empty else 0
    ultimo = float(seguidores.iloc[-1]) if not seguidores.empty else 0
    crescimento = ((ultimo - primeiro) / primeiro) * 100 if primeiro > 0 else 0

    try:
        score = calcular_score(dados)
    except Exception:
        score = 0

    return {
        "seguidores": int(seguidores.max()) if not seguidores.empty else 0,
        "alcance": int(alcance.max()) if not alcance.empty else 0,
        "engajamento": round(float(engajamento.mean()), 2) if not engajamento.empty else 0,
        "crescimento": round(crescimento, 2),
        "score": score,
    }


def numero(valor):
    try:
        return f"{float(valor):,.0f}"
    except Exception:
        return "0"


def criar_kpi(valor, titulo, cor, identificador):
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
                html.Div(
                    [
                        html.H1(
                            "📊 Social Media Dashboard AI",
                            style={
                                "textAlign": "center",
                                "fontSize": "clamp(26px, 4vw, 40px)",
                                "margin": "5px 0 10px",
                                "color": "white",
                            },
                        ),
                        html.P(
                            "Social Media Analytics • Artificial Intelligence",
                            style={
                                "textAlign": "center",
                                "color": COLORS["muted"],
                                "fontSize": "15px",
                                "marginBottom": "10px",
                            },
                        ),
                    ],
                    style={"flex": "1"},
                ),
                html.Div(
                    [
                        html.A(
                            "👤 Minha Conta",
                            href="/account",
                            style={
                                "display": "inline-block",
                                "padding": "9px 14px",
                                "borderRadius": "9px",
                                "background": COLORS["success"],
                                "color": COLORS["background"],
                                "textDecoration": "none",
                                "fontWeight": "700",
                                "fontSize": "13px",
                            },
                        ),
                        html.A(
                            "🔗 Conectar Redes",
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
                        "display": "flex",
                        "alignItems": "center",
                        "gap": "10px",
                    },
                ),
            ],
            style={
                "display": "flex",
                "alignItems": "center",
                "gap": "15px",
                "marginBottom": "25px",
            },
        ),

        html.Div(
            [
                criar_kpi(numero(kpis_iniciais["seguidores"]), "Seguidores", COLORS["primary"], "kpi-seguidores"),
                criar_kpi(numero(kpis_iniciais["alcance"]), "Alcance", COLORS["success"], "kpi-alcance"),
                criar_kpi(f'{kpis_iniciais["engajamento"]}%', "Engajamento Médio", COLORS["warning"], "kpi-engajamento"),
                criar_kpi(f'{kpis_iniciais["crescimento"]}%', "Crescimento", COLORS["purple"], "kpi-crescimento"),
                criar_kpi(f'{kpis_iniciais["score"]}/100', "Score IA", COLORS["danger"], "kpi-score"),
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
                html.Label(
                    "🔎 Selecionar plataforma",
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
                html.H2("🤖 Assistente IA", style={"color": COLORS["primary"], "marginTop": "0"}),
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
                html.H2("📋 Relatório Executivo IA", style={"marginTop": "0", "color": "white"}),
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
    [Input("plataforma", "value")],
)
def atualizar(plataforma):
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

    if dados.empty:
        vazio = grafico_sem_dados("Sem dados")
        return (
            [{"label": p, "value": p} for p in plataformas_atuais],
            plataforma_atual,
            "0", "0", "0%", "0%", "0/100",
            "⚠️ Não existem dados para esta plataforma.",
            "💡 Adicione dados para obter recomendações.",
            "Relatório indisponível.",
            vazio, vazio, vazio, vazio,
        )

    kpis = calcular_kpis(dados)

    try:
        alerta = gerar_alerta(dados)
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

    return (
        [{"label": p, "value": p} for p in plataformas_atuais],
        plataforma_atual,
        numero(kpis["seguidores"]),
        numero(kpis["alcance"]),
        f'{kpis["engajamento"]}%',
        f'{kpis["crescimento"]}%',
        f'{kpis["score"]}/100',
        str(alerta),
        str(recomendacao),
        str(relatorio),
        fig1, fig2, fig3, fig4,
    )


if __name__ == "__main__":
    inicializar_database()
    port = int(os.environ.get("PORT", 8050))
    app.run(host="0.0.0.0", port=port, debug=False)
