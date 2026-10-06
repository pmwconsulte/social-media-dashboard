
import os
import pandas as pd
import plotly.express as px
import dash

from dash import html, dcc
from dash.dependencies import Input, Output
from dotenv import load_dotenv

from database import get_connection, create_tables

from ai_engine import (
    prever_crescimento,
    gerar_alerta,
    gerar_recomendacao,
    calcular_score,
    gerar_relatorio_executivo
)

# ==================================================
# CONFIGURAÇÃO
# ==================================================

load_dotenv()
create_tables()

app = dash.Dash(__name__)
app.title = "Social Media Dashboard IA"
server = app.server


# ==================================================
# DADOS
# ==================================================

def carregar_dados():
    try:
        conn = get_connection()

        try:
            dados = pd.read_sql_query(
                "SELECT * FROM historico",
                conn
            )
        finally:
            conn.close()

        return dados

    except Exception as erro:
        print(f"Erro ao carregar dados: {erro}")
        return pd.DataFrame()


df = carregar_dados()

# Dados demonstrativos quando a base de dados está vazia
if df.empty:
    df = pd.DataFrame({
        "data": pd.date_range(
            start="2026-01-01",
            periods=7,
            freq="D"
        ),
        "plataforma": ["Instagram"] * 7,
        "seguidores": [
            1200, 1400, 1650, 1900, 2150, 2500, 2900
        ],
        "engajamento": [
            3.5, 4.2, 4.8, 5.2, 5.9, 6.4, 8.1
        ],
        "alcance": [
            10000, 12500, 14500, 18000,
            23000, 28000, 35000
        ]
    })

# Validar as colunas necessárias
colunas_obrigatorias = [
    "data",
    "plataforma",
    "seguidores",
    "engajamento",
    "alcance"
]

colunas_em_falta = [
    coluna for coluna in colunas_obrigatorias
    if coluna not in df.columns
]

if colunas_em_falta:
    raise ValueError(
        "Colunas em falta na tabela historico: "
        + ", ".join(colunas_em_falta)
    )

df["data"] = pd.to_datetime(
    df["data"],
    errors="coerce"
)

for coluna in ["seguidores", "engajamento", "alcance"]:
    df[coluna] = pd.to_numeric(
        df[coluna],
        errors="coerce"
    )

df = df.dropna(
    subset=[
        "data",
        "plataforma",
        "seguidores",
        "engajamento",
        "alcance"
    ]
)

df = df.sort_values("data").reset_index(drop=True)

if df.empty:
    raise ValueError(
        "Não existem registos válidos para apresentar."
    )


# ==================================================
# KPIS
# ==================================================

seguidores_total = int(df["seguidores"].max())

alcance_total = int(df["alcance"].max())

engajamento_medio = round(
    df["engajamento"].mean(),
    2
)

primeiros_seguidores = float(df["seguidores"].iloc[0])
ultimos_seguidores = float(df["seguidores"].iloc[-1])

if primeiros_seguidores != 0:
    crescimento = round(
        (
            (ultimos_seguidores - primeiros_seguidores)
            / primeiros_seguidores
        ) * 100,
        2
    )
else:
    crescimento = 0.0

# ==================================================
# INTELIGÊNCIA ARTIFICIAL
# ==================================================

score_ia = calcular_score(df)

alerta_ia = gerar_alerta(df)

recomendacao_ia = gerar_recomendacao(df)

relatorio_ia = gerar_relatorio_executivo(df)


# ==================================================
# COMPONENTE KPI
# ==================================================

def criar_kpi(valor, titulo, cor):
    return html.Div(
        [
            html.H2(
                str(valor),
                style={
                    "color": cor,
                    "marginBottom": "10px",
                    "fontSize": "26px"
                }
            ),

            html.P(
                titulo,
                style={
                    "color": "white",
                    "fontSize": "16px",
                    "margin": "0"
                }
            )
        ],
        style={
            "background": "#172554",
            "padding": "20px 10px",
            "borderRadius": "15px",
            "width": "18%",
            "boxSizing": "border-box",
            "textAlign": "center",
            "boxShadow": "0px 5px 15px rgba(0,0,0,0.3)"
        }
    )


# ==================================================
# LAYOUT
# ==================================================

plataformas = sorted(
    df["plataforma"].astype(str).unique().tolist()
)

app.layout = html.Div(
    style={
        "backgroundColor": "#020617",
        "minHeight": "100vh",
        "padding": "20px",
        "fontFamily": "Arial",
        "color": "white"
    },

    children=[
        # Cabeçalho
        html.H1(
            "📊 Social Media Dashboard com IA",
            style={
                "textAlign": "center",
                "color": "white"
            }
        ),

        html.P(
            "Análise de desempenho, crescimento e recomendações inteligentes",
            style={
                "textAlign": "center",
                "color": "#94a3b8"
            }
        ),

        html.Br(),

        # KPIs
        html.Div(
            [
                criar_kpi(
                    f"{seguidores_total:,}",
                    "Seguidores",
                    "#38bdf8"
                ),

                criar_kpi(
                    f"{alcance_total:,}",
                    "Alcance",
                    "#22c55e"
                ),

                criar_kpi(
                    f"{engajamento_medio}%",
                    "Engajamento Médio",
                    "#f59e0b"
                ),

                criar_kpi(
                    f"{crescimento}%",
                    "Crescimento",
                    "#a855f7"
                ),

                criar_kpi(
                    f"{score_ia}/100",
                    "Score IA",
                    "#ef4444"
                )
            ],
            style={
                "display": "flex",
                "justifyContent": "space-between",
                "gap": "12px",
                "flexWrap": "wrap"
            }
        ),

        html.Br(),

        # Assistente IA
        html.Div(
            [
                html.H2(
                    "🤖 Assistente IA",
                    style={"color": "#38bdf8"}
                ),

                html.P(
                    str(alerta_ia),
                    style={
                        "color": "#facc15",
                        "fontSize": "16px"
                    }
                ),

                html.P(
                    str(recomendacao_ia),
                    style={
                        "color": "#22c55e",
                        "fontSize": "16px"
                    }
                )
            ],
            style={
                "background": "#172554",
                "padding": "20px",
                "borderRadius": "15px",
                "marginBottom": "20px"
            }
        ),

        # Relatório Executivo IA
        html.Div(
            [
                html.H3(
                    "📋 Relatório Executivo IA",
                    style={"color": "white"}
                ),

                html.Pre(
                    str(relatorio_ia),
                    style={
                        "whiteSpace": "pre-wrap",
                        "overflowWrap": "anywhere",
                        "color": "#e2e8f0",
                        "fontFamily": "Arial",
                        "lineHeight": "1.6"
                    }
                )
            ],
            style={
                "background": "#111827",
                "padding": "20px",
                "borderRadius": "15px",
                "marginBottom": "20px"
            }
        ),

        # Filtro por plataforma
        html.Label(
            "Selecionar plataforma:",
            style={
                "color": "white",
                "fontWeight": "bold"
            }
        ),

        dcc.Dropdown(
            id="plataforma",

            options=[
                {
                    "label": plataforma,
                    "value": plataforma
                }
                for plataforma in plataformas
            ],

            value=plataformas[0],
            clearable=False
        ),

        html.Br(),

        # Gráficos
        dcc.Graph(id="grafico_seguidores"),

        dcc.Graph(id="grafico_alcance"),

        dcc.Graph(id="grafico_engajamento"),

        dcc.Graph(id="grafico_previsao"),

        html.Hr(),

        html.Div(
            "© 2026 PMW Consultoria & Tecnologia",

            style={
                "color": "#94a3b8",
                "textAlign": "center",
                "padding": "20px"
            }
        )
    ]
)


# ==================================================
# CALLBACKS
# ==================================================

@app.callback(
    [
        Output("grafico_seguidores", "figure"),
        Output("grafico_alcance", "figure"),
        Output("grafico_engajamento", "figure"),
        Output("grafico_previsao", "figure")
    ],

    [
        Input("plataforma", "value")
    ]
)
def atualizar(plataforma):

    dados = df[
        df["plataforma"].astype(str) == str(plataforma)
    ].copy()

    dados = dados.sort_values("data")

    # Gráfico 1: Seguidores
    fig1 = px.line(
        dados,
        x="data",
        y="seguidores",
        markers=True,
        title=f"Evolução de Seguidores - {plataforma}",
        labels={
            "data": "Data",
            "seguidores": "Seguidores"
        }
    )

    fig1.update_layout(template="plotly_dark")

    # Gráfico 2: Alcance
    fig2 = px.bar(
        dados,
        x="data",
        y="alcance",
        title=f"Alcance - {plataforma}",
        labels={
            "data": "Data",
            "alcance": "Alcance"
        }
    )

    fig2.update_layout(template="plotly_dark")

    # Gráfico 3: Engajamento
    fig3 = px.area(
        dados,
        x="data",
        y="engajamento",
        title=f"Engajamento - {plataforma}",
        labels={
            "data": "Data",
            "engajamento": "Engajamento (%)"
        }
    )

    fig3.update_layout(template="plotly_dark")

    # Gráfico 4: Previsão IA para 7 dias
    previsao = prever_crescimento(dados)

    datas_futuras = pd.date_range(
        start=dados["data"].max() + pd.Timedelta(days=1),
        periods=len(previsao),
        freq="D"
    )

    fig4 = px.line(
        x=datas_futuras,
        y=previsao,
        title="🤖 Previsão IA - Próximos 7 Dias",
        markers=True,
        labels={
            "x": "Data prevista",
            "y": "Seguidores previstos"
        }
    )

    fig4.update_layout(
        template="plotly_dark",
        xaxis_title="Data",
        yaxis_title="Seguidores previstos"
    )

    return (
        fig1,
        fig2,
        fig3,
        fig4
    )


# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8050)),
        debug=False
    )
