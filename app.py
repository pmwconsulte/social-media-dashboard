import os
import traceback

import pandas as pd
import plotly.express as px
import dash

from dash import html, dcc
from dash.dependencies import Input, Output
from flask import session
from dotenv import load_dotenv

from database import get_connection, create_tables
from auth import criar_utilizador, autenticar_utilizador

from ai_engine import (
    prever_crescimento,
    gerar_alerta,
    gerar_recomendacao,
    calcular_score,
    gerar_relatorio_executivo,
)

# ============================================================
# CONFIGURAÇÃO
# ============================================================

load_dotenv()

ALLOW_DEMO_DATA = os.getenv("ALLOW_DEMO_DATA", "false").lower() == "true"
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "change-me-in-production"

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

# ============================================================
# DASH APPLICATION
# ============================================================

app = dash.Dash(
    __name__,
    title=APP_TITLE,
    suppress_callback_exceptions=True,
)

server = app.server
server.secret_key = FLASK_SECRET_KEY
server.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true")

# ============================================================
# DATABASE
# ============================================================

def testar_database():
    """
    Testa a ligação ao PostgreSQL.

    Importante:
    Não é executado automaticamente durante o import do app.
    """

    try:

        inicializar_database()

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

        print(
            f"[DATABASE ERROR] {erro}"
        )

        traceback.print_exc()

        return False


def inicializar_database():
    """
    Cria/verifica as tabelas apenas quando explicitamente chamado.
    """

    try:

        create_tables()

        print(
            "[DATABASE] Tabelas verificadas com sucesso."
        )

        return True

    except Exception as erro:

        print(
            f"[DATABASE WARNING] {erro}"
        )

        traceback.print_exc()

        return False


# ============================================================
# DADOS
# ============================================================

def carregar_dados(user_id=None):

    """
    Carrega os dados reais do PostgreSQL.

    Estrutura:

    social_accounts
            |
            +---- daily_metrics
    """

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

            WHERE sa.user_id = %s
            ORDER BY
                dm.report_date ASC

        """

        if not user_id:
            return pd.DataFrame()

        dados = pd.read_sql_query(
            query,
            conn,
            params=(user_id,)
        )

        print(
            f"[DATABASE] {len(dados)} registos carregados."
        )

        return dados

    except Exception as erro:

        print(
            f"[DATABASE ERROR] Erro ao carregar dados: {erro}"
        )

        traceback.print_exc()

        return pd.DataFrame()

    finally:

        if conn:

            conn.close()


# ============================================================
# PREPARAÇÃO DOS DADOS
# ============================================================

def preparar_dados(dados):

    if dados.empty:

        return dados

    dados = dados.copy()

    dados["data"] = pd.to_datetime(
        dados["data"],
        errors="coerce"
    )

    dados["plataforma"] = (
        dados["plataforma"]
        .astype(str)
        .str.strip()
    )

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
                dados[coluna],
                errors="coerce"
            )

    dados = dados.dropna(
        subset=[
            "data",
            "plataforma",
        ]
    )

    dados = dados.sort_values(
        "data"
    )

    dados = dados.reset_index(
        drop=True
    )

    return dados


def gerar_dados_demo():
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


def obter_dados_dashboard(user_id=None):
    dados = preparar_dados(carregar_dados(user_id))
    if dados.empty and ALLOW_DEMO_DATA:
        print("[INFO] PostgreSQL sem dados. ALLOW_DEMO_DATA=true: a utilizar dados demonstrativos.")
        return preparar_dados(gerar_dados_demo())
    if dados.empty:
        print("[INFO] Nenhum dado real encontrado no PostgreSQL. Dashboard sem dados.")
    return dados


df = pd.DataFrame()

# KPI
# ============================================================

def calcular_kpis(dados):

    if dados.empty:

        return {
            "seguidores": 0,
            "alcance": 0,
            "engajamento": 0,
            "crescimento": 0,
            "score": 0,
        }

    dados = dados.sort_values(
        "data"
    )

    seguidores = pd.to_numeric(
        dados["seguidores"],
        errors="coerce"
    ).dropna()

    alcance = pd.to_numeric(
        dados["alcance"],
        errors="coerce"
    ).dropna()

    engajamento = pd.to_numeric(
        dados["engajamento"],
        errors="coerce"
    ).dropna()

    primeiro = (
        float(seguidores.iloc[0])
        if not seguidores.empty
        else 0
    )

    ultimo = (
        float(seguidores.iloc[-1])
        if not seguidores.empty
        else 0
    )

    if primeiro > 0:

        crescimento = (
            (ultimo - primeiro)
            / primeiro
        ) * 100

    else:

        crescimento = 0

    try:

        score = calcular_score(
            dados
        )

    except Exception:

        score = 0

    return {

        "seguidores": int(
            seguidores.max()
        ) if not seguidores.empty else 0,

        "alcance": int(
            alcance.max()
        ) if not alcance.empty else 0,

        "engajamento": round(
            float(engajamento.mean()),
            2
        ) if not engajamento.empty else 0,

        "crescimento": round(
            crescimento,
            2
        ),

        "score": score,
    }


# ============================================================
# FORMATAÇÃO
# ============================================================

def numero(valor):

    try:

        return f"{float(valor):,.0f}"

    except Exception:

        return "0"


# ============================================================
# KPI CARD
# ============================================================

def criar_kpi(
    valor,
    titulo,
    cor,
    identificador
):

    return html.Div(

        [

            html.H2(

                str(valor),

                id=identificador,

                style={

                    "color": cor,

                    "fontSize": (
                        "clamp(22px, 3vw, 30px)"
                    ),

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

            "border": (
                "1px solid "
                "rgba(148,163,184,0.10)"
            ),

            "boxShadow": (
                "0 5px 18px "
                "rgba(0,0,0,0.22)"
            ),
        },
    )


# ============================================================
# GRÁFICO
# ============================================================

def configurar_grafico(fig):

    fig.update_layout(

        template="plotly_dark",

        paper_bgcolor=COLORS["card"],

        plot_bgcolor=COLORS["card"],

        font={
            "family": "Arial",
            "color": COLORS["text"],
        },

        margin={
            "l": 45,
            "r": 20,
            "t": 70,
            "b": 45,
        },

        hovermode="x unified",

    )

    fig.update_xaxes(
        showgrid=False,
        automargin=True
    )

    fig.update_yaxes(
        gridcolor=(
            "rgba(148,163,184,0.12)"
        ),
        automargin=True
    )

    return fig


# ============================================================
# FIGURA SEM DADOS
# ============================================================

def grafico_sem_dados(titulo):

    fig = px.scatter(
        title=titulo
    )

    fig.add_annotation(

        text="Sem dados disponíveis",

        x=0.5,

        y=0.5,

        xref="paper",

        yref="paper",

        showarrow=False,

        font={
            "color": COLORS["muted"],
            "size": 16,
        },
    )

    return configurar_grafico(
        fig
    )


# ============================================================
# LAYOUT
# ============================================================

plataformas = ["Instagram", "Facebook", "LinkedIn", "YouTube", "TikTok", "X"]

if not plataformas:

    plataformas = ["Instagram"]


def login_layout():
    return html.Div([
        html.H1("📊 Social Media Dashboard AI", style={"color": "white"}),
        html.P("Acesso seguro à sua área de analytics.", style={"color": COLORS["muted"]}),
        dcc.Input(id="login-email", type="email", placeholder="Email", style={"width":"100%","marginBottom":"10px","padding":"12px","boxSizing":"border-box"}),
        dcc.Input(id="login-password", type="password", placeholder="Palavra-passe", style={"width":"100%","marginBottom":"10px","padding":"12px","boxSizing":"border-box"}),
        html.Button("Entrar", id="login-button", n_clicks=0, style={"width":"100%","padding":"12px","marginBottom":"10px"}),
        html.Hr(),
        dcc.Input(id="register-name", type="text", placeholder="Nome", style={"width":"100%","marginBottom":"10px","padding":"12px","boxSizing":"border-box"}),
        dcc.Input(id="register-email", type="email", placeholder="Email para registo", style={"width":"100%","marginBottom":"10px","padding":"12px","boxSizing":"border-box"}),
        dcc.Input(id="register-password", type="password", placeholder="Palavra-passe (mín. 8 caracteres)", style={"width":"100%","marginBottom":"10px","padding":"12px","boxSizing":"border-box"}),
        html.Button("Criar conta", id="register-button", n_clicks=0, style={"width":"100%","padding":"12px"}),
        html.Div(id="auth-message", style={"marginTop":"15px","color":COLORS["warning"],"whiteSpace":"pre-wrap"}),
    ], style={"maxWidth":"420px","margin":"80px auto","padding":"30px","background":COLORS["card"],"borderRadius":"16px","color":"white"})


def dashboard_page(user_id):
    dados = obter_dados_dashboard(user_id)
    if dados.empty:
        dados = pd.DataFrame(columns=["data","plataforma","username","seguidores","alcance","impressoes","engajamento","visualizacoes_perfil","cliques_site"])
    return html.Div([
        html.Div(
            html.Button(
                "🚪 Sair",
                id="logout-button",
                n_clicks=0,
                style={
                    "padding": "10px 18px",
                    "borderRadius": "10px",
                    "border": "1px solid rgba(148,163,184,0.25)",
                    "background": COLORS["card"],
                    "color": COLORS["text"],
                    "cursor": "pointer",
                },
            ),
            style={"textAlign": "right", "marginBottom": "10px"},
        ),
        dashboard_layout,
    ])

dashboard_layout = html.Div(

    [

        # ----------------------------------------------------
        # HEADER
        # ----------------------------------------------------

        html.Div(

            [

                html.H1(

                    "📊 Social Media Dashboard AI",

                    style={

                        "textAlign": "center",

                        "fontSize": (
                            "clamp(26px, 4vw, 40px)"
                        ),

                        "margin": "5px 0 10px",

                        "color": "white",

                    },
                ),

                html.P(

                    "Social Media Analytics • "
                    "Artificial Intelligence",

                    style={

                        "textAlign": "center",

                        "color": COLORS["muted"],

                        "fontSize": "15px",

                        "marginBottom": "30px",

                    },
                ),
            ]
        ),

        # ----------------------------------------------------
        # KPIs
        # ----------------------------------------------------

        html.Div(

            [

                criar_kpi(
                    numero(
                        calcular_kpis(df)[
                            "seguidores"
                        ]
                    ),
                    "Seguidores",
                    COLORS["primary"],
                    "kpi-seguidores",
                ),

                criar_kpi(
                    numero(
                        calcular_kpis(df)[
                            "alcance"
                        ]
                    ),
                    "Alcance",
                    COLORS["success"],
                    "kpi-alcance",
                ),

                criar_kpi(
                    (
                        f'{calcular_kpis(df)["engajamento"]}%'
                    ),
                    "Engajamento Médio",
                    COLORS["warning"],
                    "kpi-engajamento",
                ),

                criar_kpi(
                    (
                        f'{calcular_kpis(df)["crescimento"]}%'
                    ),
                    "Crescimento",
                    COLORS["purple"],
                    "kpi-crescimento",
                ),

                criar_kpi(
                    (
                        f'{calcular_kpis(df)["score"]}/100'
                    ),
                    "Score IA",
                    COLORS["danger"],
                    "kpi-score",
                ),

            ],

            style={

                "display": "grid",

                "gridTemplateColumns": (
                    "repeat("
                    "auto-fit, "
                    "minmax(180px, 1fr)"
                    ")"
                ),

                "gap": "15px",

                "width": "100%",

                "marginBottom": "25px",

            },
        ),

        # ----------------------------------------------------
        # FILTRO
        # ----------------------------------------------------

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

                    options=[

                        {
                            "label": p,
                            "value": p,
                        }

                        for p in plataformas

                    ],

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

                "border": (
                    "1px solid "
                    "rgba(148,163,184,0.10)"
                ),
            },
        ),

        # ----------------------------------------------------
        # ASSISTENTE IA
        # ----------------------------------------------------

        html.Div(

            [

                html.H2(

                    "🤖 Assistente IA",

                    style={
                        "color": COLORS["primary"],
                        "marginTop": "0",
                    },
                ),

                html.Div(
                    id="alerta-ia",
                    style={
                        "color": "#facc15",
                        "whiteSpace": "pre-wrap",
                        "lineHeight": "1.7",
                    },
                ),

                html.Div(
                    id="recomendacao-ia",
                    style={
                        "color": COLORS["success"],
                        "whiteSpace": "pre-wrap",
                        "lineHeight": "1.7",
                        "marginTop": "10px",
                    },
                ),

            ],

            style={

                "background": "#172554",

                "padding": "22px",

                "borderRadius": "16px",

                "marginBottom": "20px",

                "border": (
                    "1px solid "
                    "rgba(56,189,248,0.15)"
                ),
            },
        ),

        # ----------------------------------------------------
        # RELATÓRIO EXECUTIVO
        # ----------------------------------------------------

        html.Div(

            [

                html.H2(

                    "📋 Relatório Executivo IA",

                    style={
                        "marginTop": "0",
                        "color": "white",
                    },
                ),

                html.Pre(

                    id="relatorio-ia",

                    style={

                        "whiteSpace": "pre-wrap",

                        "overflowWrap": "anywhere",

                        "color": "#e2e8f0",

                        "fontFamily": (
                            "Arial, sans-serif"
                        ),

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

        # ----------------------------------------------------
        # GRÁFICOS
        # ----------------------------------------------------

        html.Div(

            [

                html.Div(
                    dcc.Graph(
                        id="grafico_seguidores",
                        config={
                            "responsive": True,
                            "displaylogo": False,
                        },
                        style={
                            "height": "380px"
                        },
                    )
                ),

                html.Div(
                    dcc.Graph(
                        id="grafico_alcance",
                        config={
                            "responsive": True,
                            "displaylogo": False,
                        },
                        style={
                            "height": "380px"
                        },
                    )
                ),

                html.Div(
                    dcc.Graph(
                        id="grafico_engajamento",
                        config={
                            "responsive": True,
                            "displaylogo": False,
                        },
                        style={
                            "height": "380px"
                        },
                    )
                ),

                html.Div(
                    dcc.Graph(
                        id="grafico_previsao",
                        config={
                            "responsive": True,
                            "displaylogo": False,
                        },
                        style={
                            "height": "380px"
                        },
                    )
                ),

            ],

            style={

                "display": "grid",

                "gridTemplateColumns": (
                    "repeat("
                    "auto-fit, "
                    "minmax(400px, 1fr)"
                    ")"
                ),

                "gap": "20px",

                "width": "100%",

            },
        ),

        # ----------------------------------------------------
        # FOOTER
        # ----------------------------------------------------

        html.Hr(
            style={
                "borderColor": COLORS["border"],
                "marginTop": "35px",
            }
        ),

        html.P(

            "© 2026 PMW Consultoria & Tecnologia",

            style={

                "textAlign": "center",

                "color": COLORS["muted"],

                "fontSize": "13px",

                "padding": "15px",

            },
        ),

    ],

    style={

        "backgroundColor": COLORS["background"],

        "minHeight": "100vh",

        "width": "100%",

        "boxSizing": "border-box",

        "padding": (
            "clamp(12px, 2vw, 28px)"
        ),

        "fontFamily": "Arial, sans-serif",

        "overflowX": "hidden",

    },
)




app.layout = html.Div([dcc.Location(id="url"), html.Div(id="page-content")])


@app.callback(
    Output("page-content", "children"),
    Input("url", "pathname")
)
def render_page(pathname):
    if session.get("user_id"):
        return dashboard_page(session["user_id"])
    return login_layout()


@app.callback(
    Output("auth-message", "children"),
    Output("url", "pathname", allow_duplicate=True),
    Input("login-button", "n_clicks"),
    Input("register-button", "n_clicks"),
    [dash.dependencies.State("login-email", "value"), dash.dependencies.State("login-password", "value"), dash.dependencies.State("register-name", "value"), dash.dependencies.State("register-email", "value"), dash.dependencies.State("register-password", "value")],
    prevent_initial_call=True,
)
def autenticar(login_clicks, register_clicks, login_email, login_password, register_name, register_email, register_password):
    ctx = dash.callback_context
    if not ctx.triggered:
        return "", dash.no_update
    trigger = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        if trigger == "register-button":
            ok, result = criar_utilizador(register_name, register_email, register_password)
            if ok:
                session["user_id"] = result
                return "Conta criada com sucesso.", "/"
            return result, dash.no_update
        user = autenticar_utilizador(login_email, login_password)
        if not user:
            return "Email ou palavra-passe inválidos.", dash.no_update
        session["user_id"] = user["id"]
        return f"Bem-vindo, {user['name']}.", "/"
    except Exception as erro:
        print(f"[AUTH ERROR] {erro}")
        traceback.print_exc()
        return "Não foi possível concluir a operação. Verifique a configuração do PostgreSQL.", dash.no_update


@app.callback(
    Output("url", "pathname", allow_duplicate=True),
    Input("logout-button", "n_clicks"),
    prevent_initial_call=True,
)
def logout(n_clicks):
    if n_clicks:
        session.clear()
        return "/"
    return dash.no_update

# ============================================================
# CALLBACK
# ============================================================

@app.callback(

    [

        Output(
            "kpi-seguidores",
            "children"
        ),

        Output(
            "kpi-alcance",
            "children"
        ),

        Output(
            "kpi-engajamento",
            "children"
        ),

        Output(
            "kpi-crescimento",
            "children"
        ),

        Output(
            "kpi-score",
            "children"
        ),

        Output(
            "alerta-ia",
            "children"
        ),

        Output(
            "recomendacao-ia",
            "children"
        ),

        Output(
            "relatorio-ia",
            "children"
        ),

        Output(
            "grafico_seguidores",
            "figure"
        ),

        Output(
            "grafico_alcance",
            "figure"
        ),

        Output(
            "grafico_engajamento",
            "figure"
        ),

        Output(
            "grafico_previsao",
            "figure"
        ),

    ],

    [
        Input(
            "plataforma",
            "value"
        )
    ],
)
def atualizar(plataforma):

    if not session.get("user_id"):
        vazio = grafico_sem_dados("Sessão não autenticada")
        return ("0","0","0%","0%","0/100","🔒 Faça login para consultar os dados.","","Sessão não autenticada.",vazio,vazio,vazio,vazio)

    dados_atuais = obter_dados_dashboard(session["user_id"])

    dados = dados_atuais[
        dados_atuais["plataforma"].astype(str)
        == str(plataforma)
    ].copy()

    dados = dados.sort_values(
        "data"
    )

    # --------------------------------------------------------
    # SEM DADOS
    # --------------------------------------------------------

    if dados.empty:

        vazio = grafico_sem_dados(
            "Sem dados"
        )

        return (

            "0",

            "0",

            "0%",

            "0%",

            "0/100",

            "⚠️ Não existem dados para esta plataforma.",

            "💡 Adicione dados para obter recomendações.",

            "Relatório indisponível.",

            vazio,

            vazio,

            vazio,

            vazio,

        )

    # --------------------------------------------------------
    # KPIs
    # --------------------------------------------------------

    kpis = calcular_kpis(
        dados
    )

    # --------------------------------------------------------
    # IA
    # --------------------------------------------------------

    try:

        alerta = gerar_alerta(
            dados
        )

    except Exception as erro:

        print(
            f"[AI ALERT ERROR] {erro}"
        )

        alerta = (
            "⚠️ Não foi possível "
            "gerar o alerta."
        )

    try:

        recomendacao = gerar_recomendacao(
            dados
        )

    except Exception as erro:

        print(
            f"[AI RECOMMENDATION ERROR] "
            f"{erro}"
        )

        recomendacao = (
            "💡 Não foi possível "
            "gerar a recomendação."
        )

    try:

        relatorio = gerar_relatorio_executivo(
            dados
        )

    except Exception as erro:

        print(
            f"[AI REPORT ERROR] {erro}"
        )

        relatorio = (
            "Relatório executivo "
            "indisponível."
        )

    # --------------------------------------------------------
    # GRÁFICO 1
    # --------------------------------------------------------

    fig1 = px.line(

        dados,

        x="data",

        y="seguidores",

        markers=True,

        title=(
            f"📈 Evolução de Seguidores — "
            f"{plataforma}"
        ),

        labels={
            "data": "Data",
            "seguidores": "Seguidores",
        },
    )

    fig1.update_traces(
        line={"width": 3},
        marker={"size": 7},
    )

    configurar_grafico(
        fig1
    )

    # --------------------------------------------------------
    # GRÁFICO 2
    # --------------------------------------------------------

    fig2 = px.bar(

        dados,

        x="data",

        y="alcance",

        title=(
            f"📊 Alcance — "
            f"{plataforma}"
        ),

        labels={
            "data": "Data",
            "alcance": "Alcance",
        },
    )

    configurar_grafico(
        fig2
    )

    # --------------------------------------------------------
    # GRÁFICO 3
    # --------------------------------------------------------

    fig3 = px.area(

        dados,

        x="data",

        y="engajamento",

        markers=True,

        title=(
            f"💬 Engajamento — "
            f"{plataforma}"
        ),

        labels={
            "data": "Data",
            "engajamento": "Engajamento (%)",
        },
    )

    configurar_grafico(
        fig3
    )

    # --------------------------------------------------------
    # GRÁFICO 4 - PREVISÃO
    # --------------------------------------------------------

    try:

        previsao = prever_crescimento(
            dados,
            dias=7
        )

        datas = pd.date_range(

            start=(
                dados["data"].max()
                + pd.Timedelta(days=1)
            ),

            periods=len(previsao),

            freq="D",

        )

        fig4 = px.line(

            x=datas,

            y=previsao,

            markers=True,

            title=(
                f"🤖 Previsão de Seguidores — "
                f"{plataforma}"
            ),

            labels={
                "x": "Data",
                "y": "Seguidores previstos",
            },
        )

        fig4.update_traces(
            line={
                "width": 3,
                "dash": "dash",
            },
            marker={
                "size": 8
            },
        )

        configurar_grafico(
            fig4
        )

    except Exception as erro:

        print(
            f"[AI FORECAST ERROR] {erro}"
        )

        fig4 = grafico_sem_dados(
            "🤖 Previsão IA"
        )

    # --------------------------------------------------------
    # RETURN
    # --------------------------------------------------------

    return (

        numero(
            kpis["seguidores"]
        ),

        numero(
            kpis["alcance"]
        ),

        f'{kpis["engajamento"]}%',

        f'{kpis["crescimento"]}%',

        f'{kpis["score"]}/100',

        str(alerta),

        str(recomendacao),

        str(relatorio),

        fig1,

        fig2,

        fig3,

        fig4,

    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    # Criar/verificar tabelas apenas quando
    # executamos diretamente:
    #
    # python app.py
    #
    # No Render, Gunicorn importa app.py e
    # NÃO executa este bloco.

    inicializar_database()

    port = int(
        os.environ.get(
            "PORT",
            8050
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
@app.callback(
    Output("page-content", "children"),
    Input("url", "pathname")
)
def render_page(pathname):
    if session.get("user_id"):
        return dashboard_page(session["user_id"])
    return login_layout()


@app.callback(
    Output("auth-message", "children"),
    Output("url", "pathname"),
    Input("login-button", "n_clicks"),
    Input("register-button", "n_clicks"),
    [dash.dependencies.State("login-email", "value"), dash.dependencies.State("login-password", "value"), dash.dependencies.State("register-name", "value"), dash.dependencies.State("register-email", "value"), dash.dependencies.State("register-password", "value")],
    prevent_initial_call=True,
)
def autenticar(login_clicks, register_clicks, login_email, login_password, register_name, register_email, register_password):
    ctx = dash.callback_context
    if not ctx.triggered:
        return "", dash.no_update
    trigger = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        if trigger == "register-button":
            ok, result = criar_utilizador(register_name, register_email, register_password)
            if ok:
                session["user_id"] = result
                return "Conta criada com sucesso.", "/"
            return result, dash.no_update
        user = autenticar_utilizador(login_email, login_password)
        if not user:
            return "Email ou palavra-passe inválidos.", dash.no_update
        session["user_id"] = user["id"]
        return f"Bem-vindo, {user['name']}.", "/"
    except Exception as erro:
        print(f"[AUTH ERROR] {erro}")
        traceback.print_exc()
        return "Não foi possível concluir a operação. Verifique a configuração do PostgreSQL.", dash.no_update


