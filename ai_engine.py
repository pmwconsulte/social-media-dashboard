
import numpy as np
import pandas as pd


# ============================================================
# PREVISÃO DE CRESCIMENTO
# ============================================================

def prever_crescimento(df, dias=7):

    if dias < 1:
        return []

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

    if dados.empty:
        return [0] * dias

    if "data" in dados.columns:
        dados = dados.sort_values("data")

    valores = dados["seguidores"].to_numpy(
        dtype=float
    )

    # Apenas um registo
    if len(valores) == 1:
        valor = max(0, float(valores[0]))

        return [
            round(valor, 2)
            for _ in range(dias)
        ]

    # Tendência linear
    x = np.arange(
        len(valores),
        dtype=float
    )

    inclinacao, intercepto = np.polyfit(
        x,
        valores,
        1
    )

    x_futuro = np.arange(
        len(valores),
        len(valores) + dias,
        dtype=float
    )

    previsoes = (
        inclinacao * x_futuro
        + intercepto
    )

    # Nunca permitir previsão negativa
    previsoes = np.maximum(
        previsoes,
        0
    )

    return [
        round(float(valor), 2)
        for valor in previsoes
    ]


# ============================================================
# SCORE IA
# ============================================================

def calcular_score(df):

    if df is None or df.empty:
        return 0

    dados = df.copy()

    for coluna in [
        "seguidores",
        "engajamento",
        "alcance"
    ]:

        if coluna not in dados.columns:
            return 0

        dados[coluna] = pd.to_numeric(
            dados[coluna],
            errors="coerce"
        )

    dados = dados.dropna(
        subset=[
            "seguidores",
            "engajamento",
            "alcance"
        ]
    )

    if dados.empty:
        return 0

    if "data" in dados.columns:
        dados = dados.sort_values("data")

    engajamento = max(
        0,
        float(
            dados["engajamento"].mean()
        )
    )

    alcance = max(
        0,
        float(
            dados["alcance"].mean()
        )
    )

    primeiro = float(
        dados["seguidores"].iloc[0]
    )

    ultimo = float(
        dados["seguidores"].iloc[-1]
    )

    if primeiro > 0:

        crescimento = (
            (ultimo - primeiro)
            / primeiro
        ) * 100

    else:

        crescimento = 0

    # Até 40 pontos
    pontos_engajamento = min(
        (engajamento / 10) * 40,
        40
    )

    # Até 30 pontos
    pontos_crescimento = min(
        max(crescimento, 0) / 20 * 30,
        30
    )

    # Até 30 pontos
    pontos_alcance = min(
        alcance / 100000 * 30,
        30
    )

    score = (
        pontos_engajamento
        + pontos_crescimento
        + pontos_alcance
    )

    return round(
        min(
            max(score, 0),
            100
        )
    )


# ============================================================
# ALERTA IA
# ============================================================

def gerar_alerta(df):

    if df is None or df.empty:
        return (
            "⚠️ Não existem dados suficientes "
            "para análise."
        )

    dados = df.copy()

    if "data" in dados.columns:
        dados = dados.sort_values("data")

    mensagens = []

    indicadores = [
        ("engajamento", "engajamento"),
        ("seguidores", "seguidores"),
        ("alcance", "alcance")
    ]

    for coluna, nome in indicadores:

        if coluna not in dados.columns:
            continue

        serie = pd.to_numeric(
            dados[coluna],
            errors="coerce"
        ).dropna()

        if len(serie) < 2:
            continue

        atual = float(
            serie.iloc[-1]
        )

        anterior = float(
            serie.iloc[-2]
        )

        if atual > anterior:

            mensagens.append(
                f"✅ O {nome} aumentou "
                "no último registo."
            )

        elif atual < anterior:

            mensagens.append(
                f"⚠️ O {nome} diminuiu "
                "no último registo."
            )

        else:

            mensagens.append(
                f"ℹ️ O {nome} manteve-se "
                "estável no último registo."
            )

    if not mensagens:

        return (
            "ℹ️ Não existem dados suficientes "
            "para identificar alterações recentes."
        )

    return "\n".join(mensagens)


# ============================================================
# RECOMENDAÇÕES IA
# ============================================================

def gerar_recomendacao(df):

    if df is None or df.empty:
        return (
            "Adiciona dados para receber "
            "recomendações."
        )

    dados = df.copy()

    if "data" in dados.columns:
        dados = dados.sort_values("data")

    recomendacoes = []

    engajamento = pd.to_numeric(
        dados["engajamento"],
        errors="coerce"
    ).dropna()

    seguidores = pd.to_numeric(
        dados["seguidores"],
        errors="coerce"
    ).dropna()

    alcance = pd.to_numeric(
        dados["alcance"],
        errors="coerce"
    ).dropna()

    # Engajamento
    if not engajamento.empty:

        media = float(
            engajamento.mean()
        )

        if media < 3:

            recomendacoes.append(
                "Melhora a relevância dos conteúdos, "
                "testa novos formatos e utiliza "
                "chamadas à ação."
            )

        elif media < 6:

            recomendacoes.append(
                "Testa diferentes horários, formatos "
                "e temas para aumentar o engajamento."
            )

        else:

            recomendacoes.append(
                "O engajamento apresenta um bom nível. "
                "Identifica os conteúdos com melhor "
                "desempenho e replica os padrões."
            )

    # Seguidores
    if len(seguidores) >= 2:

        if seguidores.iloc[-1] < seguidores.iloc[-2]:

            recomendacoes.append(
                "Analisa as causas da redução de "
                "seguidores e revê a estratégia "
                "de conteúdo."
            )

        elif seguidores.iloc[-1] > seguidores.iloc[-2]:

            recomendacoes.append(
                "Mantém a consistência dos conteúdos "
                "que estão associados ao crescimento."
            )

    # Alcance
    if len(alcance) >= 2:

        if alcance.iloc[-1] < alcance.iloc[-2]:

            recomendacoes.append(
                "Reavalia a distribuição das publicações "
                "e testa conteúdos com maior potencial "
                "de partilha."
            )

    if not recomendacoes:

        recomendacoes.append(
            "Continua a monitorizar os KPIs "
            "e compara o desempenho ao longo do tempo."
        )

    return "💡 " + " ".join(
        recomendacoes
    )


# ============================================================
# RELATÓRIO EXECUTIVO
# ============================================================

def gerar_relatorio_executivo(df):

    if df is None or df.empty:

        return (
            "RELATÓRIO EXECUTIVO\n\n"
            "Não existem dados disponíveis "
            "para análise."
        )

    dados = df.copy()

    if "data" in dados.columns:
        dados = dados.sort_values("data")

    seguidores = pd.to_numeric(
        dados["seguidores"],
        errors="coerce"
    ).dropna()

    alcance = pd.to_numeric(
        dados["alcance"],
        errors="coerce"
    ).dropna()

    engajamento = pd.to_numeric(
        dados["engajamento"],
        errors="coerce"
    ).dropna()

    primeiro = (
        float(seguidores.iloc[0])
        if not seguidores.empty
        else 0
    )

    ultimo = (
        float(seguidores.iloc[-1])
        if not seguidores.empty
        else 0
    )

    if primeiro > 0:

        crescimento = (
            (ultimo - primeiro)
            / primeiro
        ) * 100

    else:

        crescimento = 0

    media_engajamento = (
        float(engajamento.mean())
        if not engajamento.empty
        else 0
    )

    alcance_maximo = (
        float(alcance.max())
        if not alcance.empty
        else 0
    )

    score = calcular_score(
        dados
    )

    if score >= 75:

        avaliacao = (
            "Desempenho elevado"
        )

    elif score >= 50:

        avaliacao = (
            "Desempenho moderado"
        )

    else:

        avaliacao = (
            "Desempenho a melhorar"
        )

    if "data" in dados.columns:

        data_inicio = (
            dados["data"]
            .min()
            .strftime("%d/%m/%Y")
        )

        data_fim = (
            dados["data"]
            .max()
            .strftime("%d/%m/%Y")
        )

    else:

        data_inicio = "N/D"
        data_fim = "N/D"

    recomendacao = gerar_recomendacao(
        dados
    )

    return f"""
RELATÓRIO EXECUTIVO DE REDES SOCIAIS
====================================

PERÍODO ANALISADO
-----------------
Início: {data_inicio}
Fim: {data_fim}
Registos analisados: {len(dados)}

INDICADORES PRINCIPAIS
----------------------
Seguidores inicial: {primeiro:,.0f}
Seguidores atual: {ultimo:,.0f}
Crescimento: {crescimento:.2f}%
Engajamento médio: {media_engajamento:.2f}%
Alcance máximo: {alcance_maximo:,.0f}

AVALIAÇÃO IA
------------
Score IA: {score}/100
Classificação: {avaliacao}

RECOMENDAÇÃO
------------
{recomendacao}

NOTA
----
O Score IA é um indicador heurístico baseado
nos dados disponíveis.

A previsão de crescimento utiliza uma tendência
linear histórica e não representa uma garantia
de resultados futuros.
""".strip()
