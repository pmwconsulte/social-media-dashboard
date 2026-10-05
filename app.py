import os
import pandas as pd
import plotly.express as px
import dash

from dash import html, dcc
from dash.dependencies import Input, Output
from dotenv import load_dotenv

from database import get_connection, create_tables

load_dotenv()
create_tables()

app = dash.Dash(__name__)
server = app.server


def carregar_dados():
    try:
        conn = get_connection()

        df = pd.read_sql_query(
            "SELECT * FROM historico",
            conn
        )

        conn.close()

        return df

    except Exception:
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
            5.5,
            6.0,
            6.8,
            7.3
        ],
        "alcance": [
            10000,
            12000,
            14500,
            18000,
            22000,
            28000,
            35000
        ]
    })

df["data"] = pd.to_datetime(df["data"])

total_seguidores = int(df["seguidores"].max())
total_alcance = int(df["alcance"].max())
engajamento_medio = round(df["engajamento"].mean(), 2)

app.layout = html.Div(

    style={
        "backgroundColor": "#0f172a",
        "minHeight": "100vh",
        "padding": "20px",
        "fontFamily": "Arial"
    },

    children=[

        html.H1(
            "📊 Social Media Dashboard",
            style={
                "textAlign": "center",
                "color": "white"
            }
        ),

        html.Br(),

        html.Div([

            html.Div([
                html.H2(
                    f"{total_seguidores:,}",
                    style={"color": "#38bdf8"}
                ),
                html.P(
                    "Seguidores",
                    style={"color": "white"}
                )
            ],
            style={
                "background": "#1e293b",
                "padding": "20px",
                "borderRadius": "10px",
                "width": "30%",
                "textAlign": "center"
            }),

            html.Div([
                html.H2(
                    f"{total_alcance:,}",
                    style={"color": "#22c55e"}
                ),
                html.P(
                    "Alcance",
                    style={"color": "white"}
                )
            ],
            style={
                "background": "#1e293b",
                "padding": "20px",
                "borderRadius": "10px",
                "width": "30%",
                "textAlign": "center"
            }),

            html.Div([
                html.H2(
                    f"{engajamento_medio}%",
                    style={"color": "#f59e0b"}
                ),
                html.P(
                    "Engajamento Médio",
                    style={"color": "white"}
                )
            ],
            style={
                "background": "#1e293b",
                "padding": "20px",
                "borderRadius": "10px",
                "width": "30%",
                "textAlign": "center"
            })

        ],
        style={
            "display": "flex",
            "justifyContent": "space-between"
        }),

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
            value=df["plataforma"].unique()[0]
        ),

        html.Br(),

        dcc.Graph(id="grafico_seguidores"),

        dcc.Graph(id="grafico_alcance")
    ]
)


@app.callback(
    [
        Output("grafico_seguidores", "figure"),
        Output("grafico_alcance", "figure")
    ],
    [
        Input("plataforma", "value")
    ]
)
def atualizar_graficos(plataforma):

    dados = df[df["plataforma"] == plataforma]

    fig1 = px.line(
        dados,
        x="data",
        y="seguidores",
        title=f"Evolução de Seguidores - {plataforma}",
        markers=True
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

    return fig1, fig2


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8050)),
        debug=False
    )
