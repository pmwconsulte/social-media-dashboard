import os, sqlite3, requests, smtplib
import pandas as pd
import plotly.express as px
import dash
from dash import dcc, html
from dotenv import load_dotenv
from email.message import EmailMessage
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

load_dotenv()
INSTAGRAM_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_DESTINO = os.getenv("EMAIL_DESTINO")

# Funções de coleta (seguidores, engajamento e alcance simulados)
def coletar_instagram():
    url = f"https://graph.instagram.com/me?fields=id,username,followers_count&access_token={INSTAGRAM_TOKEN}"
    data = requests.get(url).json()
    return {"data": pd.Timestamp.now().strftime("%Y-%m-%d"), "plataforma": "Instagram",
            "seguidores": data.get("followers_count", 0), "engajamento": 4.5, "alcance": 50000}

def coletar_youtube():
    url = f"https://www.googleapis.com/youtube/v3/channels?part=statistics&mine=true&key={YOUTUBE_API_KEY}"
    data = requests.get(url).json()
    stats = data["items"][0]["statistics"]
    return {"data": pd.Timestamp.now().strftime("%Y-%m-%d"), "plataforma": "YouTube",
            "seguidores": int(stats["subscriberCount"]), "engajamento": 5.0, "alcance": 70000}

def salvar_metricas(metricas):
    conn = sqlite3.connect("social_media.db")
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS historico (
        data TEXT, plataforma TEXT, seguidores INTEGER, engajamento REAL, alcance INTEGER)""")
    cursor.execute("INSERT INTO historico VALUES (?, ?, ?, ?, ?)",
                   (metricas["data"], metricas["plataforma"], metricas["seguidores"],
                    metricas["engajamento"], metricas["alcance"]))
    conn.commit()
    conn.close()

# Geração de múltiplos gráficos no PDF
def gerar_pdf(df):
    pdf_file = "relatorio.pdf"
    c = canvas.Canvas(pdf_file, pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(100, 750, "Relatório de Social Media")
    c.setFont("Helvetica", 10)
    y = 720
    for _, row in df.iterrows():
        c.drawString(100, y, f"{row['data']} - {row['plataforma']}: {row['seguidores']} seguidores, "
                             f"{row['engajamento']}% engajamento, alcance {row['alcance']}")
        y -= 20

    # Gráficos
    fig1 = px.bar(df, x="plataforma", y="seguidores", title="Seguidores por Plataforma")
    fig2 = px.bar(df, x="plataforma", y="engajamento", title="Engajamento (%)")
    fig3 = px.bar(df, x="plataforma", y="alcance", title="Alcance")

    fig1.write_image("grafico_seguidores.png")
    fig2.write_image("grafico_engajamento.png")
    fig3.write_image("grafico_alcance.png")

    # Inserir gráficos no PDF
    c.drawImage("grafico_seguidores.png", 100, 400, width=400, height=250)
    c.drawImage("grafico_engajamento.png", 100, 150, width=250, height=200)
    c.drawImage("grafico_alcance.png", 360, 150, width=250, height=200)

    c.save()
    return pdf_file

def enviar_pdf(pdf_file):
    msg = EmailMessage()
    msg["Subject"] = "Relatório Social Media"
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_DESTINO
    msg.set_content("Segue em anexo o relatório em PDF com métricas e gráficos.")
    with open(pdf_file, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=pdf_file)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_USER, EMAIL_PASS)
        smtp.send_message(msg)

# Fluxo principal
for m in [coletar_instagram(), coletar_youtube()]:
    salvar_metricas(m)

conn = sqlite3.connect("social_media.db")
df = pd.read_sql_query("SELECT * FROM historico", conn)
conn.close()

pdf_file = gerar_pdf(df)
enviar_pdf(pdf_file)

# Dashboard
app = dash.Dash(__name__)
app.layout = html.Div([
    html.H1("Dashboard Social Media"),
    dcc.Dropdown(
        id="plataforma",
        options=[{"label": p, "value": p} for p in df["plataforma"].unique()],
        value=df["plataforma"].unique()[0]
    ),
    dcc.Graph(id="grafico_seguidores")
])

@app.callback(
    dash.dependencies.Output("grafico_seguidores", "figure"),
    [dash.dependencies.Input("plataforma", "value")]
)
def atualizar_grafico(plataforma):
    dados = df[df["plataforma"] == plataforma]
    fig = px.line(dados, x="data", y="seguidores", title=f"Seguidores - {plataforma}", markers=True)
    return fig

server = app.server

if __name__ == "__main__":
    app.run_server(debug=True)
