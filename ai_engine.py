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

    if dias <= 0:
        return []

    dados = _preparar(df)

    if dados.empty or "seguidores" not in dados.columns:
        return [0] * dias

    dados = dados.dropna(subset=["seguidores"])

    if len(dados) < 2:
        ultimo = (
            float(dados["seguidores"].iloc[-1])
            if not dados.empty
            else 0
        )

        return [round(ultimo, 2)] * dias

    y = dados["seguidores"].astype(float).values

    X = np.arange(len(y)).reshape(-1, 1)

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
        round(float(valor), 2)
        for valor in previsao
    ]


# ============================================================
# CRESCIMENTO
# ============================================================

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

    if dados.empty or not {"seguidores", "engajamento"}.issubset(dados.columns):
        return None

    # Usar a janela mais recente disponível, sem exigir 14 dias completos.
    if "data" in dados.columns and dados["data"].notna().any():
        data_fim = dados["data"].max()
        data_inicio = data_fim - pd.Timedelta(days=27)
        dados = dados.loc[dados["data"].between(data_inicio, data_fim)].copy()

    # Sete dias válidos permitem uma análise preliminar.
    base = dados.dropna(subset=["seguidores", "engajamento"]).copy()
    if "data" in base.columns and base["data"].notna().any():
        dias_validos = base.loc[base["data"].notna(), "data"].dt.date.nunique()
    else:
        dias_validos = len(base)

    if dias_validos < 7:
        return None

    componentes = []
    pesos_disponiveis = 0

    engajamento = _numero(base["engajamento"]).dropna()
    if not engajamento.empty:
        componentes.append((min(max(float(engajamento.mean()), 0) * 4, 40), 40))
        pesos_disponiveis += 40

    historico_seguidores = base[["seguidores"]].copy()
    if "data" in base.columns:
        historico_seguidores["data"] = base["data"]
        historico_seguidores = (
            historico_seguidores.dropna(subset=["data", "seguidores"])
            .sort_values("data")
            .drop_duplicates(subset=["data"], keep="last")
        )
    serie_seguidores = _numero(historico_seguidores["seguidores"]).dropna()
    if len(serie_seguidores) >= 2 and serie_seguidores.iloc[0] > 0:
        crescimento = ((float(serie_seguidores.iloc[-1]) - float(serie_seguidores.iloc[0])) /
                       float(serie_seguidores.iloc[0])) * 100
        componentes.append((min(max(crescimento, 0) * 0.8, 20), 20))
        pesos_disponiveis += 20

    alcance = _numero(base["alcance"]).dropna() if "alcance" in base.columns else pd.Series(dtype=float)
    seguidores_atual = float(serie_seguidores.iloc[-1]) if not serie_seguidores.empty else 0
    alcance_positivo = alcance[alcance > 0]
    if not alcance_positivo.empty and seguidores_atual > 0:
        alcance_ratio = float(alcance_positivo.mean()) / seguidores_atual
        componentes.append((min(alcance_ratio * 10, 20), 20))
        pesos_disponiveis += 20

    for coluna, peso, divisor in (
        ("cliques_site", 10, 10),
        ("visualizacoes_perfil", 10, 50),
    ):
        if coluna in base.columns:
            valores = _numero(base[coluna]).dropna()
            if not valores.empty:
                componentes.append((min(max(float(valores.mean()), 0) / divisor, peso), peso))
                pesos_disponiveis += peso

    if pesos_disponiveis == 0:
        return None

    # Manter a ponderação original sobre 100 pontos. Indicadores ausentes
    # não são inventados nem ampliados artificialmente para preencher a escala.
    score = sum(valor for valor, _ in componentes)
    return max(0, min(round(score), 100))


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

    dados = _preparar(df)

    if dados.empty:
        return "⚠️ Dados insuficientes para análise."

    mensagens = []

    indicadores = [
        ("engajamento", "engajamento"),
        ("seguidores", "seguidores"),
        ("alcance", "alcance"),
    ]

    for coluna, nome in indicadores:

        if coluna not in dados.columns:
            continue

        serie = (
            _numero(dados[coluna])
            .dropna()
        )

        if len(serie) < 3:
            continue

        atual = float(serie.iloc[-1])
        media = float(serie.mean())

        if media <= 0:
            continue

        variacao = (
            (atual - media)
            / media
        ) * 100

        if variacao <= -20:

            mensagens.append(
                f"🚨 {nome.capitalize()} "
                f"{abs(variacao):.1f}% abaixo da média."
            )

        elif variacao >= 20:

            mensagens.append(
                f"🚀 {nome.capitalize()} "
                f"{variacao:.1f}% acima da média."
            )

    crescimento = calcular_crescimento(
        dados
    )

    if crescimento < -5:

        mensagens.append(
            f"⚠️ Crescimento negativo: "
            f"{crescimento:.2f}%."
        )

    elif crescimento > 10:

        mensagens.append(
            f"📈 Crescimento forte: "
            f"{crescimento:.2f}%."
        )

    if not mensagens:
        return "✅ Indicadores estáveis."

    return "\n".join(mensagens)


# ============================================================
# RECOMENDAÇÕES IA
# ============================================================

def gerar_recomendacao(df):

    dados = _preparar(df)

    if dados.empty:
        return "Ainda estamos a reunir dados para identificar oportunidades para a sua conta."

    score = calcular_score(dados)
    crescimento = calcular_crescimento(dados)
    recomendacoes = []

    engagement = (
        _numero(dados["engajamento"]).dropna()
        if "engajamento" in dados.columns
        else pd.Series(dtype=float)
    )
    if not engagement.empty:
        taxa = float(engagement.mean())
        if taxa >= 10:
            recomendacoes.append(
                "O envolvimento observado é um sinal positivo. Continue a testar conteúdos que incentivem comentários, partilhas e respostas."
            )
        elif taxa >= 3:
            recomendacoes.append(
                "Reforce chamadas à ação claras e conteúdos que convidem o público a participar, guardar e partilhar."
            )
        else:
            recomendacoes.append(
                "Experimente publicações com perguntas simples, dicas úteis e chamadas à ação para estimular a participação do público."
            )

    alcance = (
        _numero(dados["alcance"]).dropna()
        if "alcance" in dados.columns
        else pd.Series(dtype=float)
    )
    if not alcance.empty and alcance.max() > 0:
        recomendacoes.append(
            "Identifique as publicações dos dias de maior alcance e teste novamente os temas e formatos que tiveram melhor resposta."
        )
    else:
        recomendacoes.append(
            "Continue a acompanhar o alcance nos próximos dias para identificar os conteúdos que conseguem chegar a mais pessoas."
        )

    if crescimento > 0:
        recomendacoes.append(
            "A audiência está a crescer. Mantenha uma frequência de publicação consistente e convide novos visitantes a seguir a página."
        )
    elif crescimento < 0:
        recomendacoes.append(
            "Reforce conteúdos de descoberta e valor prático para recuperar o interesse de novos visitantes e seguidores."
        )
    else:
        recomendacoes.append(
            "Teste horários e temas diferentes durante a próxima semana e compare os resultados antes de ajustar a estratégia."
        )

    if score is None:
        prefixo = "Com os dados disponíveis, já é possível observar tendências preliminares. "
    else:
        prefixo = f"A análise atual apresenta um Score de {score}/100. "

    return "💡 " + prefixo + " ".join(recomendacoes[:3])


# ============================================================
# RELATÓRIO EXECUTIVO
# ============================================================

def gerar_relatorio_executivo(df):

    dados = _preparar(df)

    if dados.empty:
        return "Ainda não existem dados oficiais suficientes para gerar este relatório."

    score = calcular_score(dados)
    classificacao = classificar_score(score)

    seguidores_series = (
        _numero(dados["seguidores"]).dropna()
        if "seguidores" in dados.columns
        else pd.Series(dtype=float)
    )
    # O alcance atual deve corresponder ao registo mais recente, mesmo
    # quando esse registo não contém valor. Não reutilizar um valor antigo
    # como se fosse atual.
    alcance_atual = None
    if "alcance" in dados.columns:
        if "data" in dados.columns and dados["data"].notna().any():
            registo_mais_recente = dados.loc[dados["data"].notna()].sort_values("data").iloc[-1]
        else:
            registo_mais_recente = dados.iloc[-1]
        valor_alcance_recente = _numero(pd.Series([registo_mais_recente.get("alcance")])).iloc[0]
        if pd.notna(valor_alcance_recente):
            alcance_atual = int(valor_alcance_recente)

    alcance_series = (
        _numero(dados["alcance"]).dropna()
        if "alcance" in dados.columns
        else pd.Series(dtype=float)
    )
    engagement_series = (
        _numero(dados["engajamento"]).dropna()
        if "engajamento" in dados.columns
        else pd.Series(dtype=float)
    )

    seguidores = int(seguidores_series.iloc[-1]) if not seguidores_series.empty else None
    alcance_maximo = int(alcance_series.max()) if not alcance_series.empty else None
    engajamento = float(engagement_series.mean()) if not engagement_series.empty else None
    crescimento = calcular_crescimento(dados) if len(seguidores_series) >= 2 else None

    if "data" in dados.columns and dados["data"].notna().any():
        data_fim = dados["data"].max()
        data_inicio = dados["data"].min()
        periodo = f"{data_inicio:%d/%m/%Y} a {data_fim:%d/%m/%Y}"
        dias_disponiveis = dados.loc[dados["data"].notna(), "data"].dt.date.nunique()
    else:
        data_fim = None
        periodo = "Período disponível na base de dados"
        dias_disponiveis = len(dados)

    # Contar apenas dias com seguidores e taxa de interação válidos,
    # que são os requisitos mínimos efetivos do Score IA.
    if {"seguidores", "engajamento"}.issubset(dados.columns):
        base_score = dados.dropna(subset=["seguidores", "engajamento"])
        if "data" in base_score.columns and base_score["data"].notna().any():
            dias_validos_score = base_score.loc[base_score["data"].notna(), "data"].dt.date.nunique()
        else:
            dias_validos_score = len(base_score)
    else:
        dias_validos_score = 0

    previsao = prever_crescimento(dados, dias=30)
    seguidores_30 = int(previsao[-1]) if previsao else seguidores

    linhas = [
        "RELATÓRIO EXECUTIVO",
        f"Período analisado: {periodo}",
        f"Dias com registos disponíveis: {dias_disponiveis}",
        "",
        "DESEMPENHO DA CONTA",
        f"Seguidores atuais: {seguidores:,}" if seguidores is not None else "Seguidores atuais: Dados indisponíveis",
        (
            f"Alcance mais recente: {alcance_atual:,}"
            if alcance_atual is not None
            else "Alcance em atualização"
        ),
        f"Maior alcance diário no período: {alcance_maximo:,}" if alcance_maximo is not None else "Maior alcance diário: Dados indisponíveis",
        f"Taxa média de interação: {engajamento:.2f}%" if engajamento is not None else "Taxa média de interação: Dados indisponíveis",
        f"Crescimento de seguidores no período: {crescimento:.2f}%" if crescimento is not None else "Crescimento de seguidores: A reunir histórico",
        "",
        "ANÁLISE INTELIGENTE",
        (
            f"Score IA preliminar: {score}/100 — {classificacao}"
            if score is not None and dias_validos_score < 14
            else (f"Score IA: {score}/100 — {classificacao}" if score is not None
                  else f"Análise preliminar: a reunir pelo menos 7 dias válidos (atualmente {dias_validos_score}).")
        ),
        "Nota do Score: a pontuação considera apenas os indicadores disponíveis; métricas sem dados não são pontuadas.",
        f"Projeção indicativa de seguidores em 30 dias: {seguidores_30:,}" if seguidores_30 is not None else "Projeção de seguidores: indisponível",
        "",
        "RECOMENDAÇÕES PARA A SUA ESTRATÉGIA",
        gerar_recomendacao(dados),
        "",
        "Nota: as conclusões refletem apenas os dados oficiais disponíveis para o período indicado. Projeções são estimativas, não garantias.",
        "",
        "PMW Consultoria & Tecnologia | Social Media Analytics AI",
    ]
    return "\n".join(linhas)
