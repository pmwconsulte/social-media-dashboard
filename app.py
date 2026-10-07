import os
import traceback

import pandas as pd
import plotly.express as px
import dash

from dash import html, dcc
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
        workspace_slug = os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
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


df = preparar_dados(carregar_dados())

if df.empty and os.getenv("ALLOW_DEMO_DATA", "false").strip().lower() in {
    "1", "true", "yes", "on"
}:
    print("[INFO] Nenhum dado encontrado. A utilizar dados demonstrativos.")
    df = pd.DataFrame({
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


if "plataforma" in df.columns:
    plataformas = sorted(
        df["plataforma"].dropna().astype(str).unique().tolist()
    )
else:
    plataformas = []

if not plataformas:
    plataformas = ["Instagram"]

kpis_iniciais = calcular_kpis(df)

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
    dados = df[df["plataforma"].astype(str) == str(plataforma)].copy()
    dados = dados.sort_values("data")

    if dados.empty:
        vazio = grafico_sem_dados("Sem dados")
        return (
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
