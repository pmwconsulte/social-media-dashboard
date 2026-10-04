import os
import pandas as pd
import plotly.express as px
import dash
 
from dash import html, dcc
from dash.dependencies import Input, Output
from dotenv import load_dotenv
 
from database import get_connection, create_tables
 
# Carregar variáveis de ambiente
load_dotenv()
 
# Criar tabelas
create_tables()
 
# Inicializar aplicação
app = dash.Dash(__name__)
server = app.server
 
 
def carregar_dados():
try:
conn = get_connection()
 
query = """
SELECT *
FROM historico
"""
 
df = pd.read_sql_query(query, conn)
 
conn.close()
 
return df
 
except Exception as erro:
print(f"Erro ao carregar dados: {erro}")
 
return pd.DataFrame({
"data": ["2026-01-01"],
"plataforma": ["Instagram"],
"seguidores": [0],
"engajamento": [0],
"alcance": [0]
})
 
 
# Carregar dados
df = carregar_dados()
 
# Criar dados mínimos se não existirem
if df.empty:
df = pd.DataFrame({
"data": ["2026-01-01"],
"plataforma": ["Instagram"],
"seguidores": [0],
"engajamento": [0],
"alcance": [0]
})
 
# Converter data
df["data"] = pd.to_datetime(df["data"])
 
# Layout
app.layout = html.Div([
html.H1(
"Social Media Dashboard",
style={
"textAlign": "center",
"color": "#2c3e50"
}
),
 
html.H3(
"Instagram | Facebook | YouTube Analytics",
style={"textAlign": "center"}
),
 
html.Br(),
 
dcc.Dropdown(
id="plataforma",
options=[
{
"label": plataforma,
"value": plataforma
}
for plataforma in df["plataforma"].unique()
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
)
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
 
fig_seguidores = px.line(
dados,
x="data",
y="seguidores",
title=f"Seguidores - {plataforma}",
markers=True
)
 
fig_alcance = px.line(
dados,
x="data",
y="alcance",
title=f"Alcance - {plataforma}",
markers=True
)
 
return fig_seguidores, fig_alcance
 
 
if __name__ == "__main__":
app.run(
host="0.0.0.0",
port=int(os.environ.get("PORT", 8050)),
debug=False
)