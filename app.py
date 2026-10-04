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

    except Exception as erro:
        print(f"Erro: {erro}")

        return pd.DataFrame({
            "data": ["2026-01-01"],
            "plataforma": ["Instagram"],
            "seguidores": [0],
            "engajamento": [0],
            "alcance": [0]
        })

df = carregar_dados()

if df.empty:
    df = pd.DataFrame({
        "data": ["2026-01-01"],
        "plataforma": ["Instagram"],
        "seguidores": [0],
        "engajamento": [0],
        "alcance": [0]
    })

df["data"] = pd.to_datetime(df["data"])

app.layout = html.Div([
    html.H1("Social Media Dashboard"),

    dcc.Dropdown(
        id="plataforma",
        options=[
            {"label": p, "value": p}
            for p in df["plataforma"].unique()
        ],
        value=df["plataforma"].unique()[0]
    ),

    dcc.Graph(id="grafico_seguidores"),
    dcc.Graph(id="grafico_alcance")
])

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
        y="seguidores"
    )

    fig2 = px.line(
        dados,
        x="data",
        y="alcance"
    )

    return fig1, fig2

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8050)),
        debug=False
    )