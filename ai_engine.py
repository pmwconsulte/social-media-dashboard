
import numpy as np
import pandas as pd


# ==================================================
# PREVISÃO DE CRESCIMENTO - PRÓXIMOS 7 DIAS
# ==================================================

def prever_crescimento(df, dias=7):
    """
    Estima a evolução do número de seguidores
    com base numa tendência linear histórica.
    """

    if df.empty or "seguidores" not in df.columns:
        return [0] * dias

    dados = df.copy()

    dados["seguidores"] = pd.to_numeric(
        dados["seguidores"],
        errors="coerce"
    )

    dados = dados.dropna(
        subset=["seguidores"]
    ).sort_values("data")

    if dados.empty:
        return [0] * dias

    seguidores = dados["seguidores"].to_numpy(dtype=float)

    # Com apenas um registo, mantém o último valor
    if len(seguidores) == 1:
        return [round(max(0, seguidores[0]), 2)] * dias

    # Ajuste de uma tendência linear
    x = np.arange(len(seguidores))

    inclinacao, intercepto = np.polyfit(
        x,
        seguidores,
        1
    )

    x_futuro = np.arange(
        len(seguidores),
        len(seguidores) + dias
    )

    previsoes = inclinacao * x_futuro + intercepto

    # Evita previsões negativas
    previsoes = np.maximum(previsoes, 0)

    return [
        round(float(valor), 2)
        for valor in previsoes
    ]


# ==================================================
# SCORE IA
# ==================================================

def calcular_score(df):
    """
    Calcula um indicador heurístico de desempenho
    entre 0 e 100.

    Não representa um modelo de IA treinado.
    """

    if df.empty:
        return 0

    dados = df.copy()

    for coluna in [
        "seguidores",
        "engajamento",
        "alcance"
    ]:
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
    ).sort_values("data")

    if dados.empty:
        return 0

    engajamento = max(
        0,
        float(dados["engajamento"].mean())
    )

    alcance = max(
        0,
        float(dados["alcance"].mean())
    )

    primeiro = float(dados["seguidores"].iloc[0])
    ultimo = float(dados["seguidores"].iloc[-1])

    if primeiro > 0:
        crescimento = (
            (ultimo - primeiro) / primeiro
        ) * 100
    else:
        crescimento = 0

    # Componentes normalizados do indicador
    pontos_engajamento = min(
        engajamento / 10 * 40,
        40
    )

    pontos_crescimento = min(
        max(crescimento, 0) / 20 * 30,
        30
    )

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
        min(max(score, 0), 100)
    )


# ==================================================
# ALERTA AUTOMÁTICO
# ==================================================

def gerar_alerta(df):
    if df.empty:
        return "⚠️ Não existem dados suficientes para análise."

    dados = df.sort_values("data")

    engajamento = dados["engajamento"].dropna()
    seguidores = dados["seguidores"].dropna()
    alcance = dados["alcance"].dropna()

    mensagens = []

    if len(engajamento) >= 2:
        atual = float(engajamento.iloc[-1])
        anterior = float(engajamento.iloc[-2])

        if atual < anterior:
            mensagens.append(
                "⚠️ O engajamento diminuiu no último registo."
            )
        elif atual > anterior:
            mensagens.append(
                "✅ O engajamento aumentou no último registo."
            )

    if len(seguidores) >= 2:
        if seguidores.iloc[-1] < seguidores.iloc[-2]:
            mensagens.append(
                "⚠️ Foi observada uma redução de seguidores."
            )

    if len(alcance) >= 2:
        if alcance.iloc[-1] < alcance.iloc[-2]:
            mensagens.append(
                "⚠️ O alcance diminuiu no último registo."
            )

    if not mensagens:
        return (
            "ℹ️ Dados insuficientes para identificar "
            "uma alteração recente. Continua a monitorizar "
            "os indicadores."
        )

    return "\n".join(mensagens)


# ==================================================
# RECOMENDAÇÕES AUTOMÁTICAS
# ==================================================

def gerar_recomendacao(df):
    if df.empty:
        return "Adiciona dados para receber recomendações."

    dados = df.sort_values("data")

    engajamento = dados["engajamento"].dropna()
    seguidores = dados["seguidores"].dropna()
    alcance = dados["alcance"].dropna()

    recomendacoes = []

    if not engajamento.empty:
        media = float(engajamento.mean())

        if media < 3:
            recomendacoes.append(
                "Melhora a relevância das publicações, "
                "testa novos formatos e incentiva "
                "a interação com o público."
            )
        elif media < 6:
            recomendacoes.append(
                "Testa horários, formatos e temas diferentes "
                "para aumentar o engajamento."
            )
        else:
            recomendacoes.append(
                "Mantém os formatos com melhor desempenho "
                "e analisa quais conteúdos geram mais interação."
            )

    if len(seguidores) >= 2:
        if seguidores.iloc[-1] <= seguidores.iloc[-2]:
            recomendacoes.append(
                "Analisa a evolução dos seguidores e "
                "reforça a consistência das publicações."
            )

    if len(alcance) >= 2:
        if alcance.iloc[-1] < alcance.iloc[-2]:
            recomendacoes.append(
                "Experimenta conteúdos partilháveis e "
                "acompanha o alcance de cada publicação."
            )

    if not recomendacoes:
        recomendacoes.append(
            "Continua a acompanhar os KPIs e compara "
            "os resultados ao longo do tempo."
        )

    return "💡 " + " ".join(recomendacoes)


# ==================================================
# RELATÓRIO EXECUTIVO
# ==================================================

def gerar_relatorio_executivo(df):
    if df.empty:
        return (
            "RELATÓRIO EXECUTIVO\n\n"
            "Não existem dados disponíveis para análise."
        )

    dados = df.sort_values("data")

    seguidores = dados["seguidores"].dropna()
    alcance = dados["alcance"].dropna()
    engajamento = dados["engajamento"].dropna()

    total_registos = len(dados)

    primeiro = (
        float(seguidores.iloc[0])
        if not seguidores.empty else 0
    )

    ultimo = (
        float(seguidores.iloc[-1])
        if not seguidores.empty else 0
    )

    if primeiro > 0:
        crescimento = (
            (ultimo - primeiro) / primeiro
        ) * 100
    else:
        crescimento = 0

    media_engajamento = (
        float(engajamento.mean())
        if not engajamento.empty else 0
    )

    alcance_maximo = (
        float(alcance.max())
        if not alcance.empty else 0
    )

    score = calcular_score(dados)

    if score >= 75:
        avaliacao = "Desempenho elevado"
    elif score >= 50:
        avaliacao = "Desempenho moderado"
    else:
        avaliacao = "Desempenho a melhorar"

    return f"""
RELATÓRIO EXECUTIVO DE REDES SOCIAIS
====================================

PERÍODO ANALISADO
Início: {dados["data"].min().strftime("%d/%m/%Y")}
Fim: {dados["data"].max().strftime("%d/%m/%Y")}

INDICADORES PRINCIPAIS
----------------------
Seguidores no primeiro registo: {primeiro:,.0f}
Seguidores no último registo: {ultimo:,.0f}
Variação de seguidores: {crescimento:.2f}%
Engajamento médio: {media_engajamento:.2f}%
Alcance máximo registado: {alcance_maximo:,.0f}
Número de registos analisados: {total_registos}

AVALIAÇÃO AUTOMÁTICA
--------------------
Score de desempenho: {score}/100
Classificação indicativa: {avaliacao}

CONCLUSÃO
---------
O relatório resume os indicadores disponíveis no período.
O score é um indicador heurístico de desempenho, não uma
avaliação estatística validada nem uma previsão garantida.

RECOMENDAÇÃO
------------
{gerar_recomendacao(dados)}

Nota: os resultados dependem da qualidade e da
atualização dos dados registados na base de dados.
""".strip()