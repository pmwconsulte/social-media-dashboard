

import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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


# ============================================================
# CONFIGURAÇÃO
# ============================================================

load_dotenv()

create_tables()

app = dash.Dash(
    __name__,
    title="Social Media Dashboard com IA",
    suppress_callback_exceptions=True
)

server = app.server


# ============================================================
# ESTILO GLOBAL
# ============================================================

PAGE_STYLE = {
    "backgroundColor": "#020617",
    "minHeight": "100vh",
    "padding": "24px",
    "fontFamily": "Arial, sans-serif",
    "color": "#e2e8f0"
}

CARD_STYLE = {
    "background": "#172554",
    "borderRadius": "16px",
    "padding": "22px",
    "boxShadow": "0 8px 25px rgba(0,0,0,0.25)",
    "border": "1px solid rgba(255,255,255,0.05)"
}

SECTION_STYLE = {
    "background": "#111827",
    "borderRadius": "16px",
    "padding": "22px",
    "marginTop": "22px",
    "boxShadow": "0 8px 25px rgba(0,0,0,0.20)"
}


# ============================================================
# DADOS
# ============================================================

def carregar_dados():

    try:

        conn = get_connection()

        df = pd.read_sql_query(
            "SELECT * FROM historico",
            conn
        )

        conn.close()

        return df

    except Exception as erro:

        print(
            f"Erro ao carregar dados: {erro}"
        )

        return pd.DataFrame()


df = carregar_dados()


# ============================================================
# DADOS DEMONSTRATIVOS
# ============================================================

if df.empty:

    df = pd.DataFrame({

        "data": [
            "2026-01-01",
            "2026-01-02",
            "2026-01-03",
            "2026-01-04",
            "2026-01-05",
            "2026-01-06",
            "2026-01-07"
        ],

        "plataforma": [
            "Instagram",
            "Instagram",
            "Instagram",
            "Instagram",
            "Instagram",
            "Instagram",
            "Instagram"
        ],

        "seguidores": [
            1200,
            1400,
            1650,
            1900,
            2150,
            2500,
            2900
        ],

        "engajamento": [
            3.5,
            4.2,
            4.8,
            5.2,
            5.9,
            6.4,
            8.1
        ],

        "alcance": [
            10000,
            12500,
            14500,
            18000,
            23000,
            28000,
            35000
        ]
    })


# ============================================================
# PREPARAÇÃO
# ============================================================

df["data"] = pd.to_datetime(
    df["data"],
    errors="coerce"
)

df = df.dropna(
    subset=["data"]
)

for coluna in [
    "seguidores",
    "engajamento",
    "alcance"
]:

    if coluna in df.columns:

        df[coluna] = pd.to_numeric(
            df[coluna],
            errors="coerce"
        )

df = df.dropna(
    subset=[
        "seguidores",
        "engajamento",
        "alcance"
    ]
)

df = df.sort_values(
    "data"
).reset_index(
    drop=True
)


# ============================================================
# KPIs GLOBAIS
# ============================================================

seguidores_total = int(
    df["seguidores"].max()
)

alcance_total = int(
    df["alcance"].max()
)

engajamento_medio = round(
    df["engajamento"].mean(),
    2
)

if (
    not df.empty
    and df["seguidores"].iloc[0] > 0
):

    crescimento = round(
        (
            (
                df["seguidores"].iloc[-1]
                - df["seguidores"].iloc[0]
            )
            /
            df["seguidores"].iloc[0]
        )
        * 100,
        2
    )

else:

    crescimento = 0


# ============================================================
# IA GLOBAL
# ============================================================

try:

    score_ia = calcular_score(df)

except Exception as erro:

    print(
        f"Erro no Score IA: {erro}"
    )

    score_ia = 0


try:

    alerta_ia = gerar_alerta(df)

except Exception as erro:

    print(
        f"Erro no alerta IA: {erro}"
    )

    alerta_ia = (
        "Não foi possível gerar o alerta IA."
    )


try:

    recomendacao_ia = gerar_recomendacao(df)

except Exception as erro:

    print(
        f"Erro na recomendação IA: {erro}"
    )

    recomendacao_ia = (
        "Não foi possível gerar a recomendação IA."
    )


try:

    relatorio_ia = gerar_relatorio_executivo(df)

except Exception as erro:

    print(
        f"Erro no relatório IA: {erro}"
    )

    relatorio_ia = (
        "Não foi possível gerar o relatório executivo."
    )


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def formatar_numero(valor):

    try:

        return f"{int(valor):,}".replace(
            ",",
            "."
        )

    except Exception:

        return "0"


def cor_score(score):

    if score >= 75:
        return "#22c55e"

    if score >= 50:
        return "#f59e0b"

    return "#ef4444"


def criar_kpi(
    valor,
    titulo,
    cor,
    icone
):

    return html.Div(

        [

            html.Div(
                icone,
                style={
                    "fontSize": "24px",
                    "marginBottom": "8px"
                }
            ),

            html.H2(
                valor,
                style={
                    "color": cor,
                    "fontSize": "30px",
                    "fontWeight": "700",
                    "margin": "0 0 8px 0"
                }
            ),

            html.P(
                titulo,
                style={
                    "color": "#e2e8f0",
                    "fontSize": "15px",
                    "margin": "0"
                }
            )

        ],

        style={
            **CARD_STYLE,
            "textAlign": "center",
            "flex": "1 1 180px",
            "minWidth": "160px"
        }
    )


def criar_gauge(score):

    fig = go.Figure(

        go.Indicator(

            mode="gauge+number",

            value=score,

            number={
                "suffix": "/100",
                "font": {
                    "size": 30,
                    "color": "white"
                }
            },

            gauge={

                "axis": {
                    "range": [0, 100],
                    "tickcolor": "#94a3b8"
                },

                "bar": {
                    "color": cor_score(score)
                },

                "bgcolor": "#020617",

                "borderwidth": 0,

                "steps": [

                    {
                        "range": [0, 40],
                        "color": "#450a0a"
                    },

                    {
                        "range": [40, 70],
                        "color": "#422006"
                    },

                    {
                        "range": [70, 100],
                        "color": "#052e16"
                    }

                ]
            }
        )
    )

    fig.update_layout(

        height=190,

        margin={
            "l": 20,
            "r": 20,
            "t": 20,
            "b": 10
        },

        paper_bgcolor="#111827",

        font={
            "color": "white"
        }
    )

    return fig


# ============================================================
# LAYOUT
# ============================================================

app.layout = html.Div(

    style=PAGE_STYLE,

    children=[

        # ====================================================
        # CABEÇALHO
        # ====================================================

        html.Div(

            [

                html.H1(
                    "📊 Social Media Dashboard com IA",
                    style={
                        "textAlign": "center",
                        "color": "white",
                        "fontSize": "clamp(28px, 4vw, 42px)",
                        "margin": "5px 0 10px 0",
                        "fontWeight": "700"
                    }
                ),

                html.P(
                    "Análise inteligente de desempenho, crescimento e recomendações",
                    style={
                        "textAlign": "center",
                        "color": "#94a3b8",
                        "fontSize": "17px",
                        "marginBottom": "28px"
                    }
                )

            ]
        ),


        # ====================================================
        # KPI CARDS
        # ====================================================

        html.Div(

            [

                criar_kpi(
                    formatar_numero(
                        seguidores_total
                    ),
                    "Seguidores",
                    "#38bdf8",
                    "👥"
                ),

                criar_kpi(
                    formatar_numero(
                        alcance_total
                    ),
                    "Alcance Máximo",
                    "#22c55e",
                    "📢"
                ),

                criar_kpi(
                    f"{engajamento_medio}%",
                    "Engajamento Médio",
                    "#f59e0b",
                    "💬"
                ),

                criar_kpi(
                    f"{crescimento}%",
                    "Crescimento",
                    "#a855f7",
                    "📈"
                ),

                criar_kpi(
                    f"{score_ia}/100",
                    "Score IA",
                    cor_score(score_ia),
                    "🤖"
                )

            ],

            style={
                "display": "flex",
                "gap": "18px",
                "flexWrap": "wrap"
            }
        ),


        # ====================================================
        # FILTROS
        # ====================================================

        html.Div(

            [

                html.H3(
                    "🔎 Filtros de Análise",
                    style={
                        "color": "white",
                        "marginTop": "0"
                    }
                ),

                html.Label(
                    "Plataforma",
                    style={
                        "color": "#94a3b8",
                        "fontSize": "14px"
                    }
                ),

                dcc.Dropdown(

                    id="plataforma",

                    options=[

                        {
                            "label": plataforma,
                            "value": plataforma
                        }

                        for plataforma
                        in sorted(
                            df[
                                "plataforma"
                            ].dropna().unique()
                        )
                    ],

                    value=df[
                        "plataforma"
                    ].dropna().unique()[0],

                    clearable=False,

                    style={
                        "marginTop": "8px",
                        "color": "#111827"
                    }
                )

            ],

            style=SECTION_STYLE
        ),


        # ====================================================
        # ASSISTENTE IA
        # ====================================================

        html.Div(

            [

                html.H2(
                    "🤖 Assistente IA",
                    style={
                        "color": "#38bdf8",
                        "marginTop": "0"
                    }
                ),

                html.Div(

                    [

                        html.Div(

                            [

                                html.H3(
                                    "🚨 Alertas",
                                    style={
                                        "color": "#facc15"
                                    }
                                ),

                                html.Div(
                                    id="alerta-ia",
                                    children=alerta_ia,
                                    style={
                                        "color": "#facc15",
                                        "lineHeight": "1.7",
                                        "whiteSpace": "pre-wrap"
                                    }
                                )

                            ],

                            style={
                                "background": "#0f172a",
                                "borderRadius": "12px",
                                "padding": "18px",
                                "flex": "1 1 350px"
                            }
                        ),

                        html.Div(

                            [

                                html.H3(
                                    "💡 Recomendações",
                                    style={
                                        "color": "#22c55e"
                                    }
                                ),

                                html.Div(
                                    id="recomendacao-ia",
                                    children=recomendacao_ia,
                                    style={
                                        "color": "#22c55e",
                                        "lineHeight": "1.7",
                                        "whiteSpace": "pre-wrap"
                                    }
                                )

                            ],

                            style={
                                "background": "#0f172a",
                                "borderRadius": "12px",
                                "padding": "18px",
                                "flex": "1 1 350px"
                            }
                        )

                    ],

                    style={
                        "display": "flex",
                        "gap": "18px",
                        "flexWrap": "wrap"
                    }
                )

            ],

            style={
                **SECTION_STYLE,
                "background": "#172554"
            }
        ),


        # ====================================================
        # GRÁFICOS
        # ====================================================

        html.Div(

            [

                html.H2(
                    "📈 Análise de Desempenho",
                    style={
                        "color": "white",
                        "marginTop": "0"
                    }
                ),

                html.Div(

                    [

                        dcc.Graph(
                            id="grafico_seguidores",
                            style={
                                "flex": "1 1 480px"
                            }
                        ),

                        dcc.Graph(
                            id="grafico_alcance",
                            style={
                                "flex": "1 1 480px"
                            }
                        )

                    ],

                    style={
                        "display": "flex",
                        "gap": "18px",
                        "flexWrap": "wrap"
                    }
                ),

                html.Div(

                    [

                        dcc.Graph(
                            id="grafico_engajamento",
                            style={
                                "flex": "1 1 480px"
                            }
                        ),

                        dcc.Graph(
                            id="grafico_previsao",
                            style={
                                "flex": "1 1 480px"
                            }
                        )

                    ],

                    style={
                        "display": "flex",
                        "gap": "18px",
                        "flexWrap": "wrap"
                    }
                )

            ],

            style=SECTION_STYLE
        ),


        # ====================================================
        # SCORE IA
        # ====================================================

        html.Div(

            [

                html.H2(
                    "🧠 Inteligência de Desempenho",
                    style={
                        "color": "white",
                        "marginTop": "0"
                    }
                ),

                html.Div(

                    [

                        html.Div(

                            [

                                html.H3(
                                    "Score IA",
                                    style={
                                        "color": "#38bdf8"
                                    }
                                ),

                                dcc.Graph(
                                    id="score-gauge",
                                    figure=criar_gauge(
                                        score_ia
                                    ),
                                    config={
                                        "displayModeBar": False
                                    }
                                )

                            ],

                            style={
                                "flex": "1 1 300px",
                                "background": "#111827",
                                "borderRadius": "12px",
                                "padding": "15px"
                            }
                        ),

                        html.Div(

                            [

                                html.H3(
                                    "📊 Interpretação",
                                    style={
                                        "color": "white"
                                    }
                                ),

                                html.P(
                                    id="score-descricao",
                                    children=(
                                        "O Score IA combina "
                                        "crescimento, alcance "
                                        "e engajamento para "
                                        "produzir um indicador "
                                        "global de desempenho."
                                    ),
                                    style={
                                        "color": "#cbd5e1",
                                        "lineHeight": "1.8"
                                    }
                                )

                            ],

                            style={
                                "flex": "1 1 300px",
                                "background": "#111827",
                                "borderRadius": "12px",
                                "padding": "20px"
                            }
                        )

                    ],

                    style={
                        "display": "flex",
                        "gap": "18px",
                        "flexWrap": "wrap"
                    }
                )

            ],

            style=SECTION_STYLE
        ),


        # ====================================================
        # RELATÓRIO EXECUTIVO
        # ====================================================

        html.Div(

            [

                html.H2(
                    "📋 Relatório Executivo IA",
                    style={
                        "color": "white",
                        "marginTop": "0"
                    }
                ),

                html.Div(
                    id="relatorio-ia",
                    children=relatorio_ia,
                    style={
                        "background": "#020617",
                        "borderRadius": "12px",
                        "padding": "20px",
                        "color": "#cbd5e1",
                        "whiteSpace": "pre-wrap",
                        "lineHeight": "1.7",
                        "fontFamily": "Arial, sans-serif",
                        "fontSize": "14px",
                        "overflowX": "auto"
                    }
                )

            ],

            style=SECTION_STYLE
        ),


        # ====================================================
        # FOOTER
        # ====================================================

        html.Div(

            [

                html.Hr(
                    style={
                        "borderColor": "#1e293b"
                    }
                ),

                html.P(
                    "© 2026 PMW Consultoria & Tecnologia",
                    style={
                        "color": "#64748b",
                        "textAlign": "center",
                        "fontSize": "13px",
                        "margin": "20px 0 5px 0"
                    }
                ),

                html.P(
                    "Social Media Analytics • AI Insights • Predictive Analysis",
                    style={
                        "color": "#475569",
                        "textAlign": "center",
                        "fontSize": "12px"
                    }
                )

            ],

            style={
                "marginTop": "30px"
            }
        )

    ]
)


# ============================================================
# CALLBACK
# ============================================================

@app.callback(

    [

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
            "score-gauge",
            "figure"
        ),

        Output(
            "score-descricao",
            "children"
        )

    ],

    [

        Input(
            "plataforma",
            "value"
        )

    ]

)
def atualizar(plataforma):

    # ========================================================
    # FILTRAR DADOS
    # ========================================================

    dados = df[
        df["plataforma"] == plataforma
    ].copy()

    dados = dados.sort_values(
        "data"
    )


    # ========================================================
    # FALLBACK
    # ========================================================

    if dados.empty:

        figura_vazia = go.Figure()

        figura_vazia.update_layout(
            template="plotly_dark",
            title="Sem dados disponíveis",
            paper_bgcolor="#111827",
            plot_bgcolor="#111827"
        )

        return (
            figura_vazia,
            figura_vazia,
            figura_vazia,
            figura_vazia,
            "⚠️ Não existem dados para esta plataforma.",
            "💡 Adicione dados para obter recomendações.",
            "Não existem dados suficientes para gerar o relatório.",
            criar_gauge(0),
            "Não existem dados suficientes para calcular o Score IA."
        )


    # ========================================================
    # IA
    # ========================================================

    try:

        score = calcular_score(
            dados
        )

    except Exception:

        score = 0


    try:

        alerta = gerar_alerta(
            dados
        )

    except Exception:

        alerta = (
            "Não foi possível gerar o alerta."
        )


    try:

        recomendacao = gerar_recomendacao(
            dados
        )

    except Exception:

        recomendacao = (
            "Não foi possível gerar a recomendação."
        )


    try:

        relatorio = gerar_relatorio_executivo(
            dados
        )

    except Exception:

        relatorio = (
            "Não foi possível gerar o relatório."
        )


    # ========================================================
    # GRÁFICO 1 - SEGUIDORES
    # ========================================================

    fig1 = px.line(

        dados,

        x="data",

        y="seguidores",

        markers=True,

        title=(
            f"👥 Evolução de Seguidores — "
            f"{plataforma}"
        )
    )

    fig1.update_traces(
        line_width=3
    )

    fig1.update_layout(
        template="plotly_dark",
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        hovermode="x unified",
        margin={
            "l": 40,
            "r": 20,
            "t": 60,
            "b": 40
        }
    )


    # ========================================================
    # GRÁFICO 2 - ALCANCE
    # ========================================================

    fig2 = px.bar(

        dados,

        x="data",

        y="alcance",

        title=(
            f"📢 Alcance — "
            f"{plataforma}"
        )
    )

    fig2.update_layout(
        template="plotly_dark",
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        hovermode="x unified",
        margin={
            "l": 40,
            "r": 20,
            "t": 60,
            "b": 40
        }
    )


    # ========================================================
    # GRÁFICO 3 - ENGAJAMENTO
    # ========================================================

    fig3 = px.area(

        dados,

        x="data",

        y="engajamento",

        markers=True,

        title=(
            f"💬 Engajamento — "
            f"{plataforma}"
        )
    )

    fig3.update_layout(
        template="plotly_dark",
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        hovermode="x unified",
        margin={
            "l": 40,
            "r": 20,
            "t": 60,
            "b": 40
        }
    )


    # ========================================================
    # GRÁFICO 4 - PREVISÃO IA
    # ========================================================

    try:

        previsao = prever_crescimento(
            dados,
            dias=7
        )

    except TypeError:

        previsao = prever_crescimento(
            dados
        )

    except Exception:

        previsao = []


    if previsao:

        ultima_data = dados[
            "data"
        ].max()

        datas_futuras = pd.date_range(

            start=ultima_data
            + pd.Timedelta(days=1),

            periods=len(previsao),

            freq="D"
        )

        fig4 = go.Figure()

        fig4.add_trace(

            go.Scatter(

                x=dados["data"],

                y=dados["seguidores"],

                mode="lines+markers",

                name="Histórico",

                line={
                    "width": 3
                }
            )
        )

        fig4.add_trace(

            go.Scatter(

                x=datas_futuras,

                y=previsao,

                mode="lines+markers",

                name="Previsão IA",

                line={
                    "width": 3,
                    "dash": "dash"
                }
            )
        )

        fig4.update_layout(

            title=(
                "🤖 Previsão IA — "
                "Próximos 7 Dias"
            ),

            template="plotly_dark",

            paper_bgcolor="#111827",

            plot_bgcolor="#111827",

            hovermode="x unified",

            margin={
                "l": 40,
                "r": 20,
                "t": 60,
                "b": 40
            },

            legend={
                "orientation": "h",
                "y": 1.1
            }
        )

    else:

        fig4 = go.Figure()

        fig4.update_layout(

            title=(
                "🤖 Previsão IA "
                "indisponível"
            ),

            template="plotly_dark",

            paper_bgcolor="#111827",

            plot_bgcolor="#111827"
        )


    # ========================================================
    # DESCRIÇÃO DO SCORE
    # ========================================================

    if score >= 75:

        score_descricao = (
            f"🟢 Score {score}/100 — "
            "Excelente desempenho. "
            "Os principais indicadores apresentam "
            "uma tendência positiva."
        )

    elif score >= 50:

        score_descricao = (
            f"🟡 Score {score}/100 — "
            "Desempenho moderado. "
            "Existem oportunidades para melhorar "
            "o alcance e o envolvimento."
        )

    else:

        score_descricao = (
            f"🔴 Score {score}/100 — "
            "Desempenho abaixo do ideal. "
            "Recomenda-se rever a estratégia "
            "de conteúdo e distribuição."
        )


    # ========================================================
    # GAUGE
    # ========================================================

    gauge = criar_gauge(
        score
    )


    # ========================================================
    # RETURN
    # ========================================================

    return (

        fig1,

        fig2,

        fig3,

        fig4,

        alerta,

        recomendacao,

        relatorio,

        gauge,

        score_descricao
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get(
                "PORT",
                8050
            )
        ),

        debug=False
    )
