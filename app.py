import os
import pandas as pd
import plotly.express as px
import dash

from dash import html, dcc
from dash.dependencies import Input, Output
from dotenv import load_dotenv

from database import get_connection, create_tables

# ==================================================
# CONFIGURAÇÃO
# ==================================================

load_dotenv()

create_tables()

app = dash.Dash(__name__)
app.title = "Social Media Dashboard"

server = app.server

# ==================================================
# DADOS
# ==================================================

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

        print(f"Erro ao carregar dados: {erro}")

        return pd.DataFrame()


df = carregar_dados()

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

df["data"] = pd.to_datetime(df["data"])

# ==================================================
# KPIS
# ==================================================

seguidores_total = int(df["seguidores"].max())

alcance_total = int(df["alcance"].max())

engajamento_medio = round(
    df["engajamento"].mean(),
    2
)

crescimento = round(
    (
        (
            df["seguidores"].iloc[-1]
            - df["seguidores"].iloc[0]
        )
        /
        df["seguidores"].iloc[0]
    ) * 100,
    2
)

# ==================================================
# COMPONENTE KPI
# ==================================================

def criar_kpi(valor, titulo, cor):

    return html.Div(

        [

            html.H2(
                valor,
                style={
                    "color": cor,
                    "marginBottom": "10px"
                }
            ),

            html.P(
                titulo,
                style={
                    "color": "white",
                    "fontSize": "18px"
                }
            )

        ],

        style={
            "background": "#172554",
            "padding": "25px",
            "borderRadius": "15px",
            "width": "23%",
            "textAlign": "center",
            "boxShadow": "0px 5px 15px rgba(0,0,0,0.3)"
        }
    )

# ==================================================
# LAYOUT
# ==================================================

app.layout = html.Div(

    style={

        "backgroundColor": "#020617",
        "minHeight": "100vh",
        "padding": "20px",
        "fontFamily": "Arial"

    },

    children=[

        html.Div(

            [

                html.H1(
                    "📊 Social Media Dashboard",
                    style={
                        "textAlign": "center",
                        "color": "white"
                    }
                )

            ]

        ),

        html.Br(),

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
                )

            ],

            style={
                "display": "flex",
                "justifyContent": "space-between"
            }
        ),

        html.Br(),

        dcc.Dropdown(

            id="plataforma",

            options=[
                {
                    "label": p,
                    "value": p
                }
                for p in df["plataforma"].unique()
            ],

            value=df["plataforma"].unique()[0],

            clearable=False

        ),

        html.Br(),

        dcc.Graph(
            id="grafico_seguidores"
        ),

        dcc.Graph(
            id="grafico_alcance"
        ),

        dcc.Graph(
            id="grafico_engajamento"
        ),

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

    dados = df[
        df["plataforma"] == plataforma
    ]

    fig1 = px.line(

        dados,

        x="data",
        y="seguidores",

        markers=True,

        title=f"Evolução de Seguidores - {plataforma}"

    )

    fig1.update_layout(
        template="plotly_dark"
    )

    fig2 = px.bar(

        dados,

        x="data",
        y="alcance",

        title=f"Alcance - {plataforma}"

    )

    fig2.update_layout(
        template="plotly_dark"
    )

    fig3 = px.area(

        dados,

        x="data",
        y="engajamento",

        title=f"Engajamento - {plataforma}"

    )

    fig3.update_layout(
        template="plotly_dark"
    )

    return fig1, fig2, fig3


# ==================================================
# START
# ==================================================

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
