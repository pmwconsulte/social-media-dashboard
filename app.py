

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


# ============================================================
# 1. CONFIGURAÇÃO
# ============================================================

load_dotenv()
create_tables()

app = dash.Dash(
    __name__,
    title="Social Media Dashboard IA",
    update_title="A atualizar dashboard..."
)

server = app.server

COR_FUNDO = "#020617"
COR_CARTAO = "#111827"
COR_CARTAO_KPI = "#172554"
COR_TEXTO = "#f8fafc"
COR_SECUNDARIA = "#94a3b8"
COR_AZUL = "#38bdf8"
COR_VERDE = "#22c55e"
COR_AMARELO = "#f59e0b"
COR_ROXO = "#a855f7"
COR_VERMELHO = "#ef4444"


# ============================================================
# 2. CARREGAR DADOS
# ============================================================

def carregar_dados():
    """Carrega os registos da tabela historico."""

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


def preparar_dados(dados):
    """Valida, converte e ordena os dados."""

    colunas = [
        "data",
        "plataforma",
        "seguidores",
        "engajamento",
        "alcance"
    ]

    if dados.empty:
        return pd.DataFrame(columns=colunas)

    dados = dados.copy()

    faltantes = [
        coluna for coluna in colunas
        if coluna not in dados.columns
    ]

    if faltantes:
        raise ValueError(
            "Colunas em falta na tabela historico: "
            + ", ".join(faltantes)
        )

    dados["data"] = pd.to_datetime(
        dados["data"],
        errors="coerce"
    )

    for coluna in [
        "seguidores",
        "engajamento",
        "alcance"
    ]:
        dados[coluna] = pd.to_numeric(
            dados[coluna],
            errors="coerce"
        )

    dados["plataforma"] = (
        dados["plataforma"].astype("string").str.strip()
    )

    dados = dados.dropna(
        subset=colunas
    )

    dados = dados[
        dados["plataforma"] != ""
    ]

    return dados.sort_values(
        "data"
    ).reset_index(drop=True)


df = preparar_dados(carregar_dados())


# ============================================================
# 3. DADOS DEMONSTRATIVOS
# ============================================================

# Utilizados apenas quando a base de dados não contém
# registos válidos. Não representam dados reais.

if df.empty:
    df = pd.DataFrame({
        "data": pd.date_range(
            start="2026-01-01",
            periods=7,
            freq="D"
        ),
        "plataforma": ["Instagram"] * 7,
        "seguidores": [
            1200, 1400, 1650, 1900,
            2150, 2500, 2900
        ],
        "engajamento": [
            3.5, 4.2, 4.8, 5.2,
            5.9, 6.4, 8.1
        ],
        "alcance": [
            10000, 12500, 14500, 18000,
            23000, 28000, 35000
        ]
    })


# ============================================================
# 4. FUNÇÕES AUXILIARES
# ============================================================

def calcular_kpis(dados):
    """Calcula os indicadores para o conjunto recebido."""

    if dados.empty:
        return {
            "seguidores": 0,
            "alcance": 0,
            "engajamento": 0,
            "crescimento": 0,
            "score": 0
        }

    dados = dados.sort_values("data")

    primeiro = float(dados["seguidores"].iloc[0])
    ultimo = float(dados["seguidores"].iloc[-1])

    if primeiro > 0:
        crescimento = (
            (ultimo - primeiro) / primeiro
        ) * 100
    else:
        crescimento = 0

    return {
        "seguidores": int(dados["seguidores"].max()),
        "alcance": int(dados["alcance"].max()),
        "engajamento": round(
            float(dados["engajamento"].mean()), 2
        ),
        "crescimento": round(crescimento, 2),
        "score": calcular_score(dados)
    }


def criar_kpi(valor, titulo, cor, identificador=None):
    """Cria um cartão KPI responsivo."""

    return html.Div(
        [
            html.H2(
                str(valor),
                id=identificador,
                style={
                    "color": cor,
                    "fontSize": "clamp(22px, 2.2vw, 30px)",
                    "fontWeight": "700",
                    "margin": "0 0 10px 0",
                    "overflowWrap": "anywhere"
                }
            ),

            html.P(
                titulo,
                style={
                    "color": COR_TEXTO,
                    "fontSize": "15px",
                    "margin": "0"
                }
            )
        ],
        style={
            "background": COR_CARTAO_KPI,
            "padding": "24px 12px",
            "borderRadius": "16px",
            "width": "100%",
            "minWidth": "0",
            "boxSizing": "border-box",
            "textAlign": "center",
            "boxShadow": "0 5px 18px rgba(0,0,0,0.22)",
            "border": "1px solid rgba(148,163,184,0.10)"
        }
    )


def criar_cartao(titulo, conteudo, cor_borda=None):
    """Cria um contentor visual consistente."""

    estilo = {
        "background": COR_CARTAO,
        "padding": "22px",
        "borderRadius": "16px",
        "minWidth": "0",
        "boxSizing": "border-box",
        "boxShadow": "0 5px 18px rgba(0,0,0,0.18)",
        "marginBottom": "20px"
    }

    if cor_borda:
        estilo["borderLeft"] = f"4px solid {cor_borda}"

    return html.Div(
        [
            html.H3(
                titulo,
                style={
                    "color": COR_TEXTO,
                    "fontSize": "20px",
                    "marginTop": "0",
                    "marginBottom": "18px"
                }
            ),
            conteudo
        ],
        style=estilo
    )


def figura_vazia(titulo, mensagem):
    """Cria um gráfico informativo quando faltam dados."""

    fig = px.scatter(
        title=titulo
    )

    fig.add_annotation(
        text=mensagem,
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font={"color": COR_SECUNDARIA, "size": 14}
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COR_CARTAO,
        plot_bgcolor=COR_CARTAO,
        margin={"l": 30, "r": 20, "t": 65, "b": 35}
    )

    return fig


def formatar_numero(valor):
    return f"{valor:,}"


# ============================================================
# 5. INDICADORES INICIAIS
# ============================================================

kpis_iniciais = calcular_kpis(df)

plataformas = sorted(
    df["plataforma"].astype(str).unique().tolist()
)

plataforma_inicial = plataformas[0]


# ============================================================
# 6. ESTILOS DOS GRÁFICOS
# ============================================================

def estilizar_figura(fig):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=COR_CARTAO,
        plot_bgcolor=COR_CARTAO,
        font={
            "family": "Arial, sans-serif",
            "color": COR_TEXTO,
            "size": 12
        },
        title={
            "x": 0.02,
            "xanchor": "left",
            "font": {"size": 18}
        },
        margin={
            "l": 45,
            "r": 20,
            "t": 70,
            "b": 45
        },
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1
        }
    )

    fig.update_xaxes(
        showgrid=False,
        automargin=True
    )

    fig.update_yaxes(
        gridcolor="rgba(148,163,184,0.12)",
        automargin=True
    )

    return fig


# ============================================================
# 7. LAYOUT PRINCIPAL
# ============================================================

app.layout = html.Div(
    style={
        "backgroundColor": COR_FUNDO,
        "minHeight": "100vh",
        "width": "100%",
        "maxWidth": "100%",
        "boxSizing": "border-box",
        "padding": "clamp(12px, 2vw, 28px)",
        "fontFamily": "Arial, sans-serif",
        "color": COR_TEXTO,
        "overflowX": "hidden"
    },

    children=[

        # CABEÇALHO
        html.Div(
            [
                html.H1(
                    "📊 Social Media Dashboard com IA",
                    style={
                        "textAlign": "center",
                        "fontSize": "clamp(25px, 3vw, 38px)",
                        "color": "white",
                        "margin": "8px 0 14px 0",
                        "overflowWrap": "anywhere"
                    }
                ),

                html.P(
                    "Análise de desempenho, crescimento e recomendações inteligentes",
                    style={
                        "textAlign": "center",
                        "color": "#94b8d8",
                        "fontSize": "16px",
                        "lineHeight": "1.6",
                        "margin": "0 0 28px 0"
                    }
                )
            ]
        ),

        # CINCO KPIs
        html.Div(
            [
                criar_kpi(
                    formatar_numero(kpis_iniciais["seguidores"]),
                    "Seguidores",
                    COR_AZUL,
                    "kpi-seguidores"
                ),

                criar_kpi(
                    formatar_numero(kpis_iniciais["alcance"]),
                    "Alcance máximo",
                    COR_VERDE,
                    "kpi-alcance"
                ),

                criar_kpi(
                    f'{kpis_iniciais["engajamento"]:.2f}%',
                    "Engajamento Médio",
                    COR_AMARELO,
                    "kpi-engajamento"
                ),

                criar_kpi(
                    f'{kpis_iniciais["crescimento"]:.2f}%',
                    "Crescimento",
                    COR_ROXO,
                    "kpi-crescimento"
                ),

                criar_kpi(
                    f'{kpis_iniciais["score"]}/100',
                    "Score IA",
                    COR_VERMELHO,
                    "kpi-score"
                )
            ],
            style={
                "display": "grid",
                "gridTemplateColumns": (
                    "repeat(auto-fit, minmax(min(100%, 180px), 1fr))"
                ),
                "gap": "16px",
                "width": "100%",
                "boxSizing": "border-box",
                "marginBottom": "24px"
            }
        ),

        # FILTROS
        criar_cartao(
            "🔎 Filtros de análise",

            html.Div(
                [
                    html.Label(
                        "Plataforma",
                        style={
                            "display": "block",
                            "color": COR_SECUNDARIA,
                            "marginBottom": "8px"
                        }
                    ),

                    dcc.Dropdown(
                        id="plataforma",
                        options=[
                            {
                                "label": p,
                                "value": p
                            }
                            for p in plataformas
                        ],
                        value=plataforma_inicial,
                        clearable=False,
                        searchable=True,
                        style={
                            "color": "#111827",
                            "width": "100%"
                        }
                    )
                ]
            )
        ),

        # ASSISTENTE IA
        html.Div(
            id="painel-assistente",
            style={
                "background": "#172554",
                "padding": "22px",
                "borderRadius": "16px",
                "marginBottom": "20px",
                "boxSizing": "border-box",
                "border": "1px solid rgba(56,189,248,0.18)"
            },
            children=[
                html.H2(
                    "🤖 Assistente IA",
                    style={
                        "color": COR_AZUL,
                        "fontSize": "23px",
                        "marginTop": "0"
                    }
                ),

                html.Div(
                    id="alerta-ia",
                    style={
                        "color": "#facc15",
                        "fontSize": "15px",
                        "lineHeight": "1.7",
                        "whiteSpace": "pre-wrap",
                        "overflowWrap": "anywhere"
                    }
                ),

                html.Div(
                    id="recomendacao-ia",
                    style={
                        "color": COR_VERDE,
                        "fontSize": "15px",
                        "lineHeight": "1.7",
                        "whiteSpace": "pre-wrap",
                        "overflowWrap": "anywhere",
                        "marginTop": "12px"
                    }
                )
            ]
        ),

        # RELATÓRIO EXECUTIVO
        criar_cartao(
            "📋 Relatório Executivo IA",

            html.Pre(
                id="relatorio-ia",
                children="A preparar relatório...",
                style={
                    "whiteSpace": "pre-wrap",
                    "overflowWrap": "anywhere",
                    "wordBreak": "normal",
                    "color": "#e2e8f0",
                    "fontFamily": "Arial, sans-serif",
                    "fontSize": "14px",
                    "lineHeight": "1.65",
                    "margin": "0",
                    "padding": "0",
                    "maxWidth": "100%"
                }
            )
        ),

        # GRÁFICOS: DUAS COLUNAS EM ECRÃS LARGOS
        html.Div(
            [
                html.Div(
                    dcc.Graph(
                        id="grafico_seguidores",
                        config={"responsive": True, "displaylogo": False},
                        style={"height": "380px"}
                    ),
                    style={"minWidth": "0"}
                ),

                html.Div(
                    dcc.Graph(
                        id="grafico_alcance",
                        config={"responsive": True, "displaylogo": False},
                        style={"height": "380px"}
                    ),
                    style={"minWidth": "0"}
                ),

                html.Div(
                    dcc.Graph(
                        id="grafico_engajamento",
                        config={"responsive": True, "displaylogo": False},
                        style={"height": "380px"}
                    ),
                    style={"minWidth": "0"}
                ),

                html.Div(
                    dcc.Graph(
                        id="grafico_previsao",
                        config={"responsive": True, "displaylogo": False},
                        style={"height": "380px"}
                    ),
                    style={"minWidth": "0"}
                )
            ],
            style={
                "display": "grid",
                "gridTemplateColumns": (
                    "repeat(auto-fit, minmax(min(100%, 420px), 1fr))"
                ),
                "gap": "20px",
                "width": "100%",
                "minWidth": "0"
            }
        ),

        # RODAPÉ
        html.Hr(
            style={
                "borderColor": "#1e293b",
                "marginTop": "30px"
            }
        ),

        html.Footer(
            [
                html.P(
                    "© 2026 PMW Consultoria & Tecnologia",
                    style={
                        "color": COR_SECUNDARIA,
                        "textAlign": "center",
                        "fontSize": "13px",
                        "margin": "18px 0 5px 0"
                    }
                ),

                html.P(
                    "Social Media Analytics • Inteligência Artificial",
                    style={
                        "color": "#64748b",
                        "textAlign": "center",
                        "fontSize": "12px",
                        "margin": "0 0 10px 0"
                    }
                )
            ]
        )
    ]
)


# ============================================================
# 8. CALLBACK: ATUALIZA KPIs, IA E GRÁFICOS
# ============================================================

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

    dados = dados.sort_values("data").reset_index(drop=True)

    if dados.empty:
        fig = figura_vazia(
            "Sem dados",
            "Não existem registos para esta plataforma."
        )

        return (
            "0", "0", "0.00%", "0.00%", "0/100",
            "Não existem dados disponíveis.",
            "Adiciona registos para receber recomendações.",
            "Relatório indisponível: sem dados.",
            fig, fig, fig, fig
        )

    # KPIs da plataforma selecionada
    kpis = calcular_kpis(dados)

    # Assistente IA
    try:
        alerta = gerar_alerta(dados)
    except Exception as erro:
        print(f"Erro no alerta IA: {erro}")
        alerta = "Não foi possível gerar o alerta."

    try:
        recomendacao = gerar_recomendacao(dados)
    except Exception as erro:
        print(f"Erro na recomendação IA: {erro}")
        recomendacao = "Não foi possível gerar recomendações."

    try:
        relatorio = gerar_relatorio_executivo(dados)
    except Exception as erro:
        print(f"Erro no relatório IA: {erro}")
        relatorio = "Não foi possível gerar o relatório executivo."

    # Gráfico 1: Seguidores
    fig1 = px.line(
        dados,
        x="data",
        y="seguidores",
        markers=True,
        title=f"📈 Evolução de Seguidores — {plataforma}",
        labels={
            "data": "Data",
            "seguidores": "Seguidores"
        }
    )

    fig1.update_traces(
        line={"width": 3},
        marker={"size": 7}
    )

    estilizar_figura(fig1)

    # Gráfico 2: Alcance
    fig2 = px.bar(
        dados,
        x="data",
        y="alcance",
        title=f"📊 Alcance — {plataforma}",
        labels={
            "data": "Data",
            "alcance": "Alcance"
        }
    )

    estilizar_figura(fig2)

    # Gráfico 3: Engajamento
    fig3 = px.area(
        dados,
        x="data",
        y="engajamento",
        markers=True,
        title=f"💬 Evolução do Engajamento — {plataforma}",
        labels={
            "data": "Data",
            "engajamento": "Engajamento (%)"
        }
    )

    estilizar_figura(fig3)

    # Gráfico 4: Previsão para os próximos sete dias
    try:
        previsao = prever_crescimento(
            dados,
            dias=7
        )

        previsao = list(previsao)

        if not previsao:
            raise ValueError("A previsão não devolveu resultados.")

        datas_futuras = pd.date_range(
            start=dados["data"].max() + pd.Timedelta(days=1),
            periods=len(previsao),
            freq="D"
        )

        fig4 = px.line(
            x=datas_futuras,
            y=previsao,
            markers=True,
            title=f"🤖 Previsão de Seguidores — {plataforma}",
            labels={
                "x": "Data prevista",
                "y": "Seguidores previstos"
            }
        )

        fig4.update_traces(
            line={"width": 3, "dash": "dash"},
            marker={"size": 8}
        )

        estilizar_figura(fig4)

    except Exception as erro:
        print(f"Erro na previsão IA: {erro}")

        fig4 = figura_vazia(
            "🤖 Previsão para os próximos 7 dias",
            "Não foi possível calcular a previsão."
        )

    return (
        formatar_numero(kpis["seguidores"]),
        formatar_numero(kpis["alcance"]),
        f'{kpis["engajamento"]:.2f}%',
        f'{kpis["crescimento"]:.2f}%',
        f'{kpis["score"]}/100',

        str(alerta),
        str(recomendacao),
        str(relatorio),

        fig1,
        fig2,
        fig3,
        fig4
    )


# ============================================================
# 9. INICIAR A APLICAÇÃO
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8050)),
        debug=False
    )
