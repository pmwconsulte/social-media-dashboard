import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression


# ============================================================
# UTILITÁRIOS
# ============================================================

def _numero(serie):
    return pd.to_numeric(serie, errors="coerce")


def _preparar(df):
    if df is None or df.empty:
        return pd.DataFrame()

    dados = df.copy()

    if "data" in dados.columns:
        dados["data"] = pd.to_datetime(
            dados["data"],
            errors="coerce"
        )

    colunas = [
        "seguidores",
        "alcance",
        "impressoes",
        "engajamento",
        "visualizacoes_perfil",
        "cliques_site",
    ]

    for coluna in colunas:
        if coluna in dados.columns:
            dados[coluna] = _numero(dados[coluna])

    if "data" in dados.columns:
        dados = dados.sort_values("data")

    return dados.reset_index(drop=True)


# ============================================================
# PREVISÃO IA
# ============================================================


def prever_crescimento(df, dias=30):
    """Prevê seguidores apenas quando existe uma amostra temporal mínima útil."""
    if dias <= 0:
        return []
    dados = _preparar(df)
    if dados.empty or not {"data", "seguidores"}.issubset(dados.columns):
        return []
    historico = dados[["data", "seguidores"]].copy()
    historico["seguidores"] = _numero(historico["seguidores"])
    historico = (
        historico.dropna(subset=["data", "seguidores"])
        .sort_values("data")
        .drop_duplicates(subset=["data"], keep="last")
    )
    # Nunca repetir o último valor ou zeros como se fossem uma previsão.
    if historico["data"].dt.date.nunique() < 7:
        return []
    y = historico["seguidores"].astype(float).to_numpy()
    if len(y) < 7 or not np.isfinite(y).all():
        return []
    X = np.arange(len(y), dtype=float).reshape(-1, 1)
    modelo = LinearRegression().fit(X, y)
    futuro = np.arange(len(y), len(y) + dias, dtype=float).reshape(-1, 1)
    previsao = np.maximum(modelo.predict(futuro), 0)
    return [round(float(valor), 2) for valor in previsao]

def calcular_crescimento(df):

    dados = _preparar(df)

    if dados.empty or "seguidores" not in dados.columns:
        return 0.0

    seguidores = (
        dados["seguidores"]
        .dropna()
        .astype(float)
    )

    if "data" in dados.columns:
        historico = dados[["data", "seguidores"]].copy()
        historico["seguidores"] = _numero(historico["seguidores"])
        historico = (
            historico.dropna(subset=["data", "seguidores"])
            .sort_values("data")
            .drop_duplicates(subset=["data"], keep="last")
        )
        seguidores = historico["seguidores"].astype(float)

    if len(seguidores) < 2:
        return 0.0

    inicial = seguidores.iloc[0]
    final = seguidores.iloc[-1]

    if inicial <= 0:
        return 0.0

    return (
        (final - inicial)
        / inicial
    ) * 100


# ============================================================
# SCORE IA
# ============================================================

def calcular_score(df):

    dados = _preparar(df)

    if dados.empty:
        return 0

    obrigatorias = [
        "seguidores",
        "alcance",
        "engajamento",
    ]

    for coluna in obrigatorias:
        if coluna not in dados.columns:
            return 0

    dados = dados.dropna(
        subset=obrigatorias
    )

    if dados.empty:
        return 0

    # Manter o Score IA no mesmo horizonte temporal do KPI de interação:
    # últimos 28 dias em relação à data mais recente disponível.
    if "data" in dados.columns and dados["data"].notna().any():
        data_fim = dados["data"].max()
        data_inicio = data_fim - pd.Timedelta(days=27)
        dados = dados.loc[dados["data"].between(data_inicio, data_fim)].copy()

    dados_validos = dados.loc[
        (dados["alcance"] > 0) & dados["engajamento"].notna()
    ].copy()
    dias_validos = (
        dados_validos["data"].dt.date.nunique()
        if "data" in dados_validos.columns
        else len(dados_validos)
    )

    # Não publicar um score com amostra diária curta ou alcance inválido.
    # Exigimos 14 dias distintos com alcance e taxa de engajamento válidos.
    if dados_validos.empty or dias_validos < 14:
        return None

    dados = dados_validos

    crescimento = calcular_crescimento(
        dados
    )

    engajamento = float(
        dados["engajamento"].mean()
    )

    alcance = float(
        dados["alcance"].mean()
    )

    seguidores = float(
        dados["seguidores"].iloc[-1]
    )

    # --------------------------------------------------------
    # Normalização
    # --------------------------------------------------------

    score_engajamento = min(
        max(engajamento, 0) * 4,
        40
    )

    score_crescimento = min(
        max(crescimento, 0) * 0.8,
        20
    )

    # Alcance relativo aos seguidores
    if seguidores > 0:
        alcance_ratio = (
            alcance / seguidores
        )
    else:
        alcance_ratio = 0

    score_alcance = min(
        alcance_ratio * 10,
        20
    )

    # Crescimento de tráfego
    score_trafego = 0

    if "cliques_site" in dados.columns:
        cliques = _numero(
            dados["cliques_site"]
        ).fillna(0)

        if cliques.mean() > 0:
            score_trafego = min(
                cliques.mean() / 10,
                10
            )

    # Visualizações de perfil
    score_perfil = 0

    if "visualizacoes_perfil" in dados.columns:
        views = _numero(
            dados["visualizacoes_perfil"]
        ).fillna(0)

        if views.mean() > 0:
            score_perfil = min(
                views.mean() / 50,
                10
            )

    score = (
        score_engajamento
        + score_crescimento
        + score_alcance
        + score_trafego
        + score_perfil
    )

    return max(
        0,
        min(
            round(score),
            100
        )
    )


# ============================================================
# CLASSIFICAÇÃO
# ============================================================

def classificar_score(score):

    if score is None or pd.isna(score):
        return "⚪ Histórico insuficiente"

    score = float(score)

    if score >= 90:
        return "🏆 Excelente"

    if score >= 75:
        return "🟢 Muito Bom"

    if score >= 50:
        return "🟡 Regular"

    if score >= 25:
        return "🟠 Necessita Atenção"

    return "🔴 Crítico"


# ============================================================
# ALERTAS IA
# ============================================================


def gerar_alerta(df):
    """Compara dias recentes com uma linha de base, evitando alertas espúrios."""
    dados = _preparar(df)
    if dados.empty or not {"data", "alcance", "engajamento"}.issubset(dados.columns):
        return "⚠️ Dados insuficientes para análise."
    dados = dados.dropna(subset=["data"]).copy()
    for coluna in ("alcance", "engajamento", "seguidores"):
        if coluna in dados.columns:
            dados[coluna] = _numero(dados[coluna])
    dados = (
        dados.sort_values("data")
        .drop_duplicates(subset=["data"], keep="last")
        .tail(14)
    )
    if dados["data"].dt.date.nunique() < 7:
        return "⚠️ Histórico insuficiente para comparar tendências; são necessários pelo menos 7 dias distintos."
    mensagens = []
    recentes = dados.tail(3)
    base = dados.iloc[:-3].tail(7)
    if len(base) >= 4:
        for coluna, nome in (("engajamento", "engajamento"), ("alcance", "alcance")):
            atual = _numero(recentes[coluna]).dropna()
            anterior = _numero(base[coluna]).dropna()
            if len(atual) < 2 or len(anterior) < 4:
                continue
            media_atual = float(atual.mean())
            media_base = float(anterior.mean())
            if not np.isfinite(media_base) or media_base <= 0:
                continue
            variacao = ((media_atual - media_base) / media_base) * 100
            if variacao <= -20:
                mensagens.append(f"🚨 {nome.capitalize()} {abs(variacao):.1f}% abaixo da média da linha de base.")
            elif variacao >= 20:
                mensagens.append(f"🚀 {nome.capitalize()} {variacao:.1f}% acima da média da linha de base.")
    if "seguidores" in dados.columns:
        seguidores = dados[["data", "seguidores"]].dropna().drop_duplicates("data").sort_values("data")
        if len(seguidores) >= 7 and float(seguidores["seguidores"].iloc[0]) > 0:
            crescimento = ((float(seguidores["seguidores"].iloc[-1]) - float(seguidores["seguidores"].iloc[0])) / float(seguidores["seguidores"].iloc[0])) * 100
            if crescimento < -5:
                mensagens.append(f"⚠️ Crescimento negativo no histórico observado: {crescimento:.2f}%.")
            elif crescimento > 10:
                mensagens.append(f"📈 Crescimento forte no histórico observado: {crescimento:.2f}%.")
    return "\n".join(mensagens) if mensagens else "ℹ️ Sem alteração relevante demonstrada nos dados disponíveis."

def gerar_recomendacao(df):

    dados = _preparar(df)

    if dados.empty:
        return (
            "💡 Adicione mais dados históricos "
            "para gerar recomendações."
        )

    score = calcular_score(dados)
    crescimento = calcular_crescimento(dados)

    if score is None:
        return (
            "💡 Ainda não há pelo menos 14 dias distintos com alcance e "
            "engajamento válidos. Continue a sincronização diária para obter "
            "um Score IA mais estável antes de ajustar a estratégia."
        )

    recomendacoes = []

    if score >= 85:
        recomendacoes.append(
            "Escalar os conteúdos de melhor desempenho."
        )

    elif score >= 70:
        recomendacoes.append(
            "Manter a estratégia atual e testar "
            "novos formatos de conteúdo."
        )

    elif score >= 50:
        recomendacoes.append(
            "Melhorar engagement através de vídeos, "
            "CTAs e conteúdos interativos."
        )

    else:
        recomendacoes.append(
            "Rever estratégia, frequência, horários "
            "e segmentação."
        )

    if crescimento < 0:
        recomendacoes.append(
            "Investigar a queda de seguidores e "
            "identificar conteúdos com baixo desempenho."
        )

    if "cliques_site" in dados.columns:

        cliques = _numero(
            dados["cliques_site"]
        ).fillna(0)

        if cliques.mean() < 10:
            recomendacoes.append(
                "Aumentar CTAs direcionando o público "
                "para o website."
            )

    return "💡 " + " ".join(
        recomendacoes
    )


# ============================================================
# RELATÓRIO EXECUTIVO
# ============================================================

def gerar_relatorio_executivo(df):

    dados = _preparar(df)

    if dados.empty:
        return "Sem dados disponíveis."

    score = calcular_score(dados)

    classificacao = classificar_score(
        score
    )

    seguidores_series = (
        _numero(dados["seguidores"])
        .dropna()
    )

    alcance_series = (
        _numero(dados["alcance"])
        .dropna()
    )

    engagement_series = (
        _numero(dados["engajamento"])
        .dropna()
    )

    seguidores = (
        int(seguidores_series.iloc[-1])
        if not seguidores_series.empty
        else 0
    )

    # O KPI "Alcance" deve refletir o registo mais recente disponível,
    # tal como o cartão do dashboard. O máximo histórico é apresentado
    # separadamente para não ser confundido com o valor atual.
    alcance_atual = (
        int(alcance_series.iloc[-1])
        if not alcance_series.empty
        else None
    )
    alcance_maximo = (
        int(alcance_series.max())
        if not alcance_series.empty
        else None
    )

    engajamento = (
        float(engagement_series.mean())
        if not engagement_series.empty
        else 0
    )

    crescimento = (
        calcular_crescimento(dados)
        if len(seguidores_series) >= 2
        else None
    )

    previsao = prever_crescimento(dados, dias=30)
    seguidores_30 = int(round(previsao[-1])) if previsao else None
    crescimento_formatado = (
        f"{crescimento:.2f}%"
        if crescimento is not None
        else "N/D (histórico insuficiente)"
    )

    return f"""
RELATÓRIO EXECUTIVO IA
=================================================

Seguidores atuais: {seguidores:,}
Alcance mais recente registado: {f"{alcance_atual:,}" if alcance_atual is not None else "N/D"}
Maior alcance diário no histórico: {f"{alcance_maximo:,}" if alcance_maximo is not None else "N/D"}
Taxa média diária registada: {engajamento:.2f}% (não é o total agregado de 28 dias)
Crescimento histórico: {crescimento_formatado}

Score IA: {f"{score}/100" if score is not None else "N/D (histórico diário insuficiente)"}
Classificação: {classificacao}

=================================================
PREVISÃO IA — 30 DIAS

Seguidores estimados: {f"{seguidores_30:,}" if seguidores_30 is not None else "N/D (necessários pelo menos 7 dias distintos)"}

=================================================
RECOMENDAÇÃO

{gerar_recomendacao(dados)}

=================================================

PMW Consultoria & Tecnologia
Social Media Analytics AI
"""
