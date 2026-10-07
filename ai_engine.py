```python
import numpy as np
import pandas as pd

from sklearn.linear_model import LinearRegression


# ============================================================
# PREVISÃO IA
# ============================================================

def prever_crescimento(df, dias=30):

    if df is None or df.empty:
        return [0] * dias

    if "seguidores" not in df.columns:
        return [0] * dias

    dados = df.copy()

    dados["seguidores"] = pd.to_numeric(
        dados["seguidores"],
        errors="coerce"
    )

    dados = dados.dropna(
        subset=["seguidores"]
    )

    if len(dados) < 2:
        return [0] * dias

    y = dados["seguidores"].values

    X = np.arange(
        len(y)
    ).reshape(-1, 1)

    modelo = LinearRegression()

    modelo.fit(X, y)

    futuro = np.arange(
        len(y),
        len(y) + dias
    ).reshape(-1, 1)

    previsao = modelo.predict(futuro)

    previsao = np.maximum(
        previsao,
        0
    )

    return [
        round(float(v), 2)
        for v in previsao
    ]


# ============================================================
# SCORE IA
# ============================================================

def calcular_score(df):

    if df is None or df.empty:
        return 0

    dados = df.copy()

    colunas = [
        "seguidores",
        "engajamento",
        "alcance"
    ]

    for coluna in colunas:

        if coluna not in dados.columns:
            return 0

        dados[coluna] = pd.to_numeric(
            dados[coluna],
            errors="coerce"
        )

    dados = dados.dropna(
        subset=colunas
    )

    if dados.empty:
        return 0

    seguidores = dados["seguidores"]

    crescimento = 0

    if seguidores.iloc[0] > 0:

        crescimento = (
            (
                seguidores.iloc[-1]
                - seguidores.iloc[0]
            )
            / seguidores.iloc[0]
        ) * 100

    engajamento = float(
        dados["engajamento"].mean()
    )

    alcance = float(
        dados["alcance"].mean()
    )

    likes = dados.get(
        "likes",
        pd.Series(0, index=dados.index)
    ).mean()

    comentarios = dados.get(
        "comentarios",
        pd.Series(0, index=dados.index)
    ).mean()

    partilhas = dados.get(
        "partilhas",
        pd.Series(0, index=dados.index)
    ).mean()

    score = (
        min(engajamento * 3.5, 35)
        +
        min(crescimento / 4, 20)
        +
        min(alcance / 5000, 15)
        +
        min(likes / 200, 10)
        +
        min(comentarios / 50, 10)
        +
        min(partilhas / 20, 10)
    )

    return min(
        round(score),
        100
    )


# ============================================================
# CLASSIFICAÇÃO
# ============================================================

def classificar_score(score):

    if score >= 90:
        return "🏆 Excelente"

    if score >= 75:
        return "🟢 Muito Bom"

    if score >= 50:
        return "🟡 Regular"

    return "🔴 Crítico"


# ============================================================
# ALERTAS IA
# ============================================================

def gerar_alerta(df):

    if df is None or df.empty:

        return "⚠️ Dados insuficientes."

    dados = df.copy()

    mensagens = []

    for coluna in [
        "engajamento",
        "seguidores",
        "alcance"
    ]:

        if coluna not in dados.columns:
            continue

        serie = pd.to_numeric(
            dados[coluna],
            errors="coerce"
        ).dropna()

        if len(serie) < 3:
            continue

        atual = serie.iloc[-1]

        media = serie.mean()

        if atual < media * 0.8:

            mensagens.append(
                f"🚨 Queda crítica no {coluna}"
            )

        elif atual > media:

            mensagens.append(
                f"✅ {coluna} acima da média"
            )

    if not mensagens:

        mensagens.append(
            "✅ Indicadores estáveis."
        )

    return "\n".join(mensagens)


# ============================================================
# RECOMENDAÇÕES IA
# ============================================================

def gerar_recomendacao(df):

    if df is None or df.empty:

        return "Adicionar mais dados."

    score = calcular_score(df)

    if score >= 90:

        return (
            "💡 Desempenho excelente. "
            "Escalar campanhas pagas e "
            "replicar conteúdos de maior sucesso."
        )

    if score >= 75:

        return (
            "💡 Crescimento saudável. "
            "Aumentar frequência de conteúdos "
            "e testar novos formatos."
        )

    if score >= 50:

        return (
            "💡 Melhorar engagement. "
            "Publicar vídeos curtos e "
            "usar chamadas para ação."
        )

    return (
        "💡 Rever estratégia de conteúdo, "
        "horários de publicação e segmentação."
    )


# ============================================================
# RELATÓRIO EXECUTIVO
# ============================================================

def gerar_relatorio_executivo(df):

    if df is None or df.empty:

        return "Sem dados disponíveis."

    score = calcular_score(df)

    classificacao = classificar_score(
        score
    )

    seguidores = int(
        pd.to_numeric(
            df["seguidores"],
            errors="coerce"
        ).dropna().iloc[-1]
    )

    alcance = int(
        pd.to_numeric(
            df["alcance"],
            errors="coerce"
        ).max()
    )

    engajamento = round(
        pd.to_numeric(
            df["engajamento"],
            errors="coerce"
        ).mean(),
        2
    )

    previsao = prever_crescimento(
        df,
        dias=30
    )

    seguidores_30 = int(
        previsao[-1]
    )

    return f"""
RELATÓRIO EXECUTIVO IA

=================================================

Seguidores atuais: {seguidores:,}

Alcance máximo: {alcance:,}

Engajamento médio: {engajamento:.2f}%

Score IA: {score}/100

Classificação: {classificacao}

=================================================

PREVISÃO IA 30 DIAS

Seguidores estimados:
{seguidores_30:,}

=================================================

RECOMENDAÇÃO

{gerar_recomendacao(df)}

=================================================

PMW Consultoria & Tecnologia
Social Media Analytics SaaS
"""
```

