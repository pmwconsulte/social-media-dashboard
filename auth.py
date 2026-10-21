import os
import re
import uuid
from functools import wraps

import bcrypt
import base64
import hashlib
import secrets
import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

from billing import PLANS, evaluate_workspace_access
from database import get_connection


def _oauth_configured():
    required = (
        "INSTAGRAM_APP_ID",
        "INSTAGRAM_APP_SECRET",
        "INSTAGRAM_OAUTH_REDIRECT_URI",
        "OAUTH_TOKEN_ENCRYPTION_KEY",
    )
    return all(os.getenv(name, "").strip() for name in required)


def _token_cipher():
    raw = os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY", "")
    if not raw:
        raise RuntimeError("OAUTH_TOKEN_ENCRYPTION_KEY não está definida.")
    try:
        key = raw.encode("utf-8")
        Fernet(key)
        return Fernet(key)
    except Exception:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_token(token):
    return _token_cipher().encrypt(token.encode("utf-8")).decode("utf-8")


def _instagram_authorize_url(state):
    from urllib.parse import urlencode

    client_id = os.getenv("INSTAGRAM_APP_ID", "").strip()
    redirect_uri = os.getenv("INSTAGRAM_OAUTH_REDIRECT_URI", "").strip()

    if not client_id or not redirect_uri:
        raise RuntimeError("INSTAGRAM_APP_ID ou INSTAGRAM_OAUTH_REDIRECT_URI não configurado.")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "instagram_business_basic,instagram_business_manage_insights",
        "state": state,
    }

    return "https://www.instagram.com/oauth/authorize?" + urlencode(params)




def _read_instagram_insight(ig_id, access_token, metric, metric_type="total_value", period=None, since=None, until=None):
    """Consulta uma métrica da Instagram Login API, com intervalo opcional explícito."""
    api_version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
    params = {
        "metric": metric,
        "metric_type": metric_type,
        "access_token": access_token,
    }
    # A API de Insights exige period também nas consultas total_value.
    params["period"] = period or "day"
    # Para relatórios históricos, delimitar explicitamente a janela evita depender
    # do intervalo implícito da API (que pode ser apenas o período recente).
    if since is not None:
        params["since"] = int(since)
    if until is not None:
        params["until"] = int(until)
    try:
        response = requests.get(
            f"https://graph.instagram.com/{api_version}/{ig_id}/insights",
            params=params,
            timeout=15,
        )
    except requests.RequestException as erro:
        print(
            f"[INSTAGRAM INSIGHTS] Falha ao consultar {metric}: "
            f"{type(erro).__name__}."
        )
        return None
    if not response.ok:
        # Regista o motivo devolvido pela API sem expor o token de acesso.
        error_code = None
        error_subcode = None
        error_message = ""
        try:
            error_payload = response.json().get("error", {})
            error_code = error_payload.get("code")
            error_subcode = error_payload.get("error_subcode")
            error_message = str(error_payload.get("message", ""))[:180]
        except (ValueError, AttributeError):
            error_message = ""
        details = []
        if error_code is not None:
            details.append(f"code={error_code}")
        if error_subcode is not None:
            details.append(f"subcode={error_subcode}")
        if error_message:
            details.append(f"motivo={error_message}")
        suffix = f" ({'; '.join(details)})" if details else ""
        print(
            f"[INSTAGRAM INSIGHTS] Métrica {metric} indisponível "
            f"(HTTP {response.status_code}){suffix}."
        )
        return None
    try:
        payload = response.json()
    except ValueError:
        print(
            f"[INSTAGRAM INSIGHTS] Resposta inválida para {metric} "
            f"(HTTP {response.status_code})."
        )
        return None

    items = payload.get("data", [])
    if not isinstance(items, list):
        print(
            f"[INSTAGRAM INSIGHTS DEBUG] metric={metric} period={params.get('period')} "
            f"metric_type={metric_type} HTTP={response.status_code} "
            "estrutura_data_inválida."
        )
        return None

    for item in items:
        values = item.get("values") or []
        total_obj = item.get("total_value") or {}
        last_value = values[-1].get("value") if values and isinstance(values[-1], dict) else None
        # Diagnóstico temporário: registar datas/valores devolvidos pela série,
        # sem incluir tokens, URLs de pedido ou outros dados de autenticação.
        if values:
            points_debug = [
                {
                    "end_time": point.get("end_time"),
                    "value": point.get("value"),
                }
                for point in values[-5:]
                if isinstance(point, dict)
            ]
            print(
                f"[INSTAGRAM INSIGHTS DEBUG] metric={item.get('name', metric)} "
                f"period={params.get('period')} últimos_pontos={points_debug!r}."
            )
        total_value = total_obj.get("value") if isinstance(total_obj, dict) else None
        print(
            f"[INSTAGRAM INSIGHTS DEBUG] metric={item.get('name', metric)} "
            f"period={params.get('period')} metric_type={metric_type} "
            f"HTTP={response.status_code} values_count={len(values)} "
            f"last_value={last_value!r} total_value={total_value!r}."
        )

        # Para consultas agregadas, usar primeiro o total agregado; para séries,
        # usar o último ponto temporal. Evita confundir os dois formatos.
        # Nunca usar o último ponto diário como substituto de um total agregado.
        candidates = (
            (total_value,)
            if metric_type == "total_value"
            else (last_value,)
        )
        for value in candidates:
            if isinstance(value, (int, float)):
                return value
    print(
        f"[INSTAGRAM INSIGHTS DEBUG] metric={metric} "
        f"period={params.get('period')} metric_type={metric_type} "
        f"HTTP={response.status_code} data_items={len(items)} valor_numérico_ausente."
    )
    return None

def _sync_instagram_period_snapshots(social_account_id, ig_id, access_token):
    """Guarda totais agregados oficiais para janelas móveis de 7/14/30/60 dias."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    until = int(now.timestamp())
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            for days in (7, 14, 30, 60):
                since = int((now - timedelta(days=days)).timestamp())
                metrics = {
                    metric: _read_instagram_insight(
                        ig_id, access_token, metric,
                        metric_type="total_value", period="total_over_range",
                        since=since, until=until,
                    )
                    for metric in ("reach", "views", "total_interactions")
                }
                if not any(value is not None for value in metrics.values()):
                    print(f"[INSTAGRAM PERÍODO] Sem totais agregados disponíveis para {days} dias.")
                    continue
                cur.execute(
                    """
                    INSERT INTO instagram_period_snapshots
                        (social_account_id, period_days, snapshot_date, reach, views, interactions)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (social_account_id, period_days, snapshot_date) DO UPDATE SET
                        reach = EXCLUDED.reach,
                        views = EXCLUDED.views,
                        interactions = EXCLUDED.interactions,
                        created_at = NOW()
                    """,
                    (social_account_id, days, now.date(), metrics["reach"], metrics["views"], metrics["total_interactions"]),
                )
                print(
                    f"[INSTAGRAM PERÍODO] janela={days} dias; "
                    f"reach={metrics['reach']!r}; views={metrics['views']!r}; "
                    f"interactions={metrics['total_interactions']!r}."
                )
        conn.commit()
        return True
    except Exception as erro:
        if conn:
            conn.rollback()
        print(f"[INSTAGRAM PERÍODO ERROR] Falha ao guardar totais oficiais: {type(erro).__name__}: {erro}")
        return False
    finally:
        if conn:
            conn.close()


def _read_instagram_reels_breakdown(ig_id, access_token, metric, since, until):
    """Lê o total oficial da métrica apenas para media_product_type=REELS."""
    api_version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
    try:
        response = requests.get(
            f"https://graph.instagram.com/{api_version}/{ig_id}/insights",
            params={
                "metric": metric, "period": "total_over_range", "metric_type": "total_value",
                "breakdown": "media_product_type", "since": int(since),
                "until": int(until), "access_token": access_token,
            },
            timeout=20,
        )
        if not response.ok:
            try:
                error = response.json().get("error", {})
                print(f"[INSTAGRAM REELS] {metric} indisponível HTTP={response.status_code} code={error.get('code')} motivo={str(error.get('message', ''))[:140]}")
            except (ValueError, AttributeError):
                print(f"[INSTAGRAM REELS] {metric} indisponível HTTP={response.status_code}.")
            return None
        payload = response.json()
    except (requests.RequestException, ValueError) as erro:
        print(f"[INSTAGRAM REELS] Falha em {metric}: {type(erro).__name__}.")
        return None

    def find_reels_value(node, reels_context=False):
        if isinstance(node, dict):
            dims = node.get("dimension_values") or node.get("dimension_keys") or []
            dim_value = str(node.get("dimension_value", "")).upper()
            is_reels = reels_context or dim_value in {"REELS", "REEL"} or (
                isinstance(dims, list) and any(str(dim).upper() in {"REELS", "REEL"} for dim in dims)
            )
            value = node.get("value")
            if is_reels and isinstance(value, (int, float)):
                return value
            total = node.get("total_value")
            if is_reels and isinstance(total, dict) and isinstance(total.get("value"), (int, float)):
                return total["value"]
            for key, child in node.items():
                found = find_reels_value(child, is_reels or str(key).upper() in {"REELS", "REEL"})
                if found is not None:
                    return found
        elif isinstance(node, list):
            for child in node:
                found = find_reels_value(child, reels_context)
                if found is not None:
                    return found
        return None

    value = find_reels_value(payload.get("data", []))
    if value is None:
        print(f"[INSTAGRAM REELS] A API não devolveu valor REELS para {metric}.")
        return None
    return int(value)


def _sync_instagram_reels_periods(social_account_id, ig_id, access_token):
    """Guarda totais oficiais de Reels para janelas móveis de 7/14/30/60 dias."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    until = int(now.timestamp())
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            for days in (7, 14, 30, 60):
                since = int((now - timedelta(days=days)).timestamp())
                metrics = {
                    metric: _read_instagram_reels_breakdown(ig_id, access_token, metric, since, until)
                    for metric in ("views", "reach", "total_interactions")
                }
                if not any(value is not None for value in metrics.values()):
                    continue
                cur.execute(
                    """
                    INSERT INTO instagram_reels_period_snapshots
                        (social_account_id, period_days, snapshot_date, views, reach, interactions)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (social_account_id, period_days, snapshot_date) DO UPDATE SET
                        views = COALESCE(EXCLUDED.views, instagram_reels_period_snapshots.views),
                        reach = COALESCE(EXCLUDED.reach, instagram_reels_period_snapshots.reach),
                        interactions = COALESCE(EXCLUDED.interactions, instagram_reels_period_snapshots.interactions),
                        created_at = NOW()
                    """,
                    (social_account_id, days, now.date(), metrics["views"], metrics["reach"], metrics["total_interactions"]),
                )
        conn.commit()
        print(f"[INSTAGRAM REELS] Snapshots por período atualizados para a conta {social_account_id}.")
        return True
    except Exception as erro:
        if conn:
            conn.rollback()
        print(f"[INSTAGRAM REELS ERROR] Não foi possível guardar snapshots: {type(erro).__name__}: {erro}")
        return False
    finally:
        if conn:
            conn.close()


def _sync_instagram_metrics(social_account_id, ig_id, access_token):
    """Guarda métricas válidas da conta, sem inventar valores indisponíveis."""
    from datetime import datetime, timedelta, timezone

    # Janela explícita de 28 dias, em timestamps Unix UTC. O fim é o momento
    # da sincronização; o início recua exatamente 28 dias.
    range_end = datetime.now(timezone.utc)
    range_start = range_end - timedelta(days=28)
    since_28d = int(range_start.timestamp())
    until_28d = int(range_end.timestamp())

    # Na API atual, impressions/profile_views/website_clicks não são métricas
    # de conta suportadas de forma geral no Instagram Login. 'views' substitui
    # impressions; fica na coluna legada impressions até uma migração de esquema.
    # Métricas diárias: reach é série temporal; views/interações usam total_value diário.
    reach_value = _read_instagram_insight(
        ig_id, access_token, "reach", metric_type="time_series", period="day"
    )
    views_value = _read_instagram_insight(
        ig_id, access_token, "views", metric_type="time_series", period="day"
    )
    interactions_value = _read_instagram_insight(
        ig_id, access_token, "total_interactions",
        metric_type="time_series", period="day"
    )

    # Totais oficiais de 28 dias. Não somar reach diário: a mesma pessoa pode
    # ser alcançada em dias diferentes.
    reach_28d = _read_instagram_insight(
        ig_id, access_token, "reach", metric_type="total_value", period="days_28",
        since=since_28d, until=until_28d,
    )
    views_28d = _read_instagram_insight(
        ig_id, access_token, "views", metric_type="total_value", period="days_28",
        since=since_28d, until=until_28d,
    )
    interactions_28d = _read_instagram_insight(
        ig_id, access_token, "total_interactions",
        metric_type="total_value", period="days_28",
        since=since_28d, until=until_28d,
    )
    if interactions_value is None:
        # Compatibilidade: tenta compor interações a partir das métricas
        # oficiais individuais. Só calcula a soma se TODAS estiverem disponíveis;
        # nunca trata uma métrica em falta como zero.
        interaction_parts = {
            metric: _read_instagram_insight(
                ig_id, access_token, metric, metric_type="total_value"
            )
            for metric in ("likes", "comments", "shares", "saves")
        }
        if all(value is not None for value in interaction_parts.values()):
            interactions_value = sum(interaction_parts.values())
            print(
                "[INSTAGRAM INSIGHTS] Interações calculadas pela soma "
                "de likes, comments, shares e saves."
            )
        else:
            missing = [
                metric for metric, value in interaction_parts.items()
                if value is None
            ]
            print(
                "[INSTAGRAM INSIGHTS] Taxa de engajamento continua indisponível; "
                f"métricas individuais ausentes: {', '.join(missing)}."
            )

    followers = None
    api_version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
    try:
        profile_response = requests.get(
            f"https://graph.instagram.com/{api_version}/me",
            params={"fields": "followers_count", "access_token": access_token},
            timeout=15,
        )
        if profile_response.ok:
            try:
                follower_value = profile_response.json().get("followers_count")
                if isinstance(follower_value, (int, float)):
                    followers = int(follower_value)
            except ValueError:
                print("[INSTAGRAM INSIGHTS] Resposta inválida ao consultar seguidores.")
    except requests.RequestException as erro:
        print(f"[INSTAGRAM INSIGHTS] Não foi possível obter seguidores: {type(erro).__name__}.")

    # Diagnóstico temporário: valores devolvidos pela API, sem tokens nem segredos.
    from datetime import datetime, timezone
    print(
        "[INSTAGRAM INSIGHTS] Diagnóstico diário: "
        f"reach={reach_value!r}, views={views_value!r}, "
        f"total_interactions={interactions_value!r}, followers={followers!r}, "
        f"data_registo_utc={datetime.now(timezone.utc).date().isoformat()}."
    )
    print(
        "[INSTAGRAM INSIGHTS] Diagnóstico 28 dias: "
        f"intervalo_utc={range_start.isoformat()}..{range_end.isoformat()}, "
        f"reach={reach_28d!r}, views={views_28d!r}, "
        f"total_interactions={interactions_28d!r}."
    )

    # Guardar os totais oficiais de cada janela, sem somar amostras diárias.
    _sync_instagram_period_snapshots(social_account_id, ig_id, access_token)

    # Consultar métricas de Reels separadamente dos totais gerais da conta.
    _sync_instagram_reels_periods(social_account_id, ig_id, access_token)

    available_values = (
        reach_value,
        views_value,
        interactions_value,
        followers,
    )
    if all(value is None for value in available_values):
        print(
            f"[INSTAGRAM INSIGHTS] Nenhuma métrica disponível para a conta "
            f"{social_account_id}; não será criado um registo vazio."
        )
        return False

    report_date = datetime.now(timezone.utc).date()
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT followers, reach, impressions, engagement_rate,
                          profile_views, website_clicks, interactions_count
                   FROM daily_metrics
                   WHERE social_account_id=%s AND report_date=%s
                   ORDER BY created_at DESC LIMIT 1""",
                (social_account_id, report_date),
            )
            row = cur.fetchone()
            # Não converter métricas indisponíveis em zero: zero é um valor
            # real, enquanto NULL significa que a API não forneceu o indicador.
            current = {
                "followers": int(row[0]) if row and row[0] is not None else None,
                "reach": int(row[1]) if row and row[1] is not None else None,
                "impressions": int(row[2]) if row and row[2] is not None else None,
                "engagement_rate": float(row[3]) if row and row[3] is not None else None,
                "profile_views": int(row[4]) if row and row[4] is not None else None,
                "website_clicks": int(row[5]) if row and row[5] is not None else None,
                "interactions_count": int(row[6]) if row and row[6] is not None else None,
            }
            if followers is not None:
                current["followers"] = int(followers)
            elif current["followers"] == 0:
                current["followers"] = None
            if reach_value is not None:
                current["reach"] = int(reach_value)
            elif current["reach"] == 0:
                current["reach"] = None
            if views_value is not None:
                # Compatibilidade com a coluna histórica 'impressions'.
                current["impressions"] = int(views_value)
            elif current["impressions"] == 0:
                current["impressions"] = None
            if interactions_value is not None:
                current["interactions_count"] = int(interactions_value)
            if interactions_value is not None and current["reach"] is not None and current["reach"] > 0:
                current["engagement_rate"] = round(
                    float(interactions_value) / current["reach"] * 100, 2
                )
            elif current["engagement_rate"] == 0:
                current["engagement_rate"] = None

            if row:
                cur.execute(
                    """UPDATE daily_metrics
                       SET followers=%s, reach=%s, impressions=%s,
                           engagement_rate=%s, profile_views=%s, website_clicks=%s,
                           interactions_count=%s
                       WHERE social_account_id=%s AND report_date=%s""",
                    (
                        current["followers"], current["reach"], current["impressions"],
                        current["engagement_rate"], current["profile_views"],
                        current["website_clicks"], current["interactions_count"],
                        social_account_id, report_date,
                    ),
                )
            else:
                cur.execute(
                    """INSERT INTO daily_metrics
                       (social_account_id, report_date, followers, reach, impressions,
                        engagement_rate, profile_views, website_clicks, interactions_count)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        social_account_id, report_date, current["followers"],
                        current["reach"], current["impressions"], current["engagement_rate"],
                        current["profile_views"], current["website_clicks"],
                        current["interactions_count"],
                    ),
                )
            # Guardar snapshots de 28 dias enquanto o cursor ainda está aberto.
            if any(value is not None for value in (reach_28d, views_28d, interactions_28d)):
                cur.execute(
                    """
                    INSERT INTO instagram_insights_28d
                        (social_account_id, snapshot_date, reach, views, interactions)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (social_account_id, snapshot_date) DO UPDATE SET
                        reach = COALESCE(EXCLUDED.reach, instagram_insights_28d.reach),
                        views = COALESCE(EXCLUDED.views, instagram_insights_28d.views),
                        interactions = COALESCE(EXCLUDED.interactions, instagram_insights_28d.interactions),
                        created_at = NOW()
                    """,
                    (social_account_id, report_date, reach_28d, views_28d, interactions_28d),
                )
        conn.commit()
        available = [
            name for name, value in (
                ("reach", reach_value),
                ("views", views_value),
                ("total_interactions", interactions_value),
                ("followers", followers),
            ) if value is not None
        ]
        print(
            f"[INSTAGRAM INSIGHTS] Registo diário guardado para a conta "
            f"{social_account_id}; métricas recebidas: {', '.join(available) or 'nenhuma'}."
        )
        return True
    except Exception as erro:
        if conn:
            conn.rollback()
        print(f"[INSTAGRAM INSIGHTS ERROR] Falha ao guardar métricas: {type(erro).__name__}: {erro}")
        return False
    finally:
        if conn:
            conn.close()


BILLING_PAGE = """
<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Planos e Pagamentos — PMW Dashboard AI</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#020617;color:#f8fafc;font-family:Arial,sans-serif;line-height:1.55}
.wrap{max-width:1100px;margin:40px auto;padding:20px}.card{background:#111827;border:1px solid #1e293b;border-radius:18px;padding:26px;margin-bottom:22px}
h1{margin-top:0}.muted{color:#94a3b8}.status{padding:12px;border-radius:10px;background:#172554;margin:18px 0}
.plans{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}
.plan{background:#0f172a;border:1px solid #334155;border-radius:14px;padding:20px;min-width:0}
.price{font-size:24px;font-weight:700;margin:10px 0 18px}ul{padding-left:20px;line-height:1.8}
button,a.btn{display:inline-block;padding:11px 16px;border-radius:9px;text-decoration:none;font-weight:700;border:0;background:#38bdf8;color:#020617;cursor:pointer}
a.secondary{background:#1e293b;color:#f8fafc;border:1px solid #475569}
a.logout{background:#ef4444;color:white;margin-left:8px}
.payment{border-top:1px solid #334155;margin-top:18px;padding-top:14px}.payment p{margin:8px 0;overflow-wrap:anywhere}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.actions a{font-size:13px}
.notice{font-size:13px;color:#cbd5e1;background:#111827;border:1px solid #334155;border-radius:10px;padding:13px;margin-top:18px}
</style>
</head>
<body>
<div class="wrap">
<div class="card">
<h1>💳 Planos e Pagamentos</h1>
<p class="muted">PMW Social Media Dashboard AI</p>
<div class="status">
<strong>Estado:</strong> {{ status }}<br>
<strong>Plano atual:</strong> {{ plan_name }}
{% if days_left > 0 %}<br><strong>Dias restantes do trial:</strong> {{ days_left }}{% endif %}
</div>
{% if expired %}
<p>O período gratuito de 30 dias terminou. Os seus dados permanecem guardados, mas o acesso ao dashboard está bloqueado até a subscrição ser ativada.</p>
{% else %}
<p>O período de teste está ativo. Escolha um plano para consultar as opções de pagamento quando estiver pronto para continuar.</p>
{% endif %}
<a class="btn" href="#plans">Ver planos e opções de pagamento</a>
<a class="btn logout" href="/logout">Sair</a>
</div>
<div class="plans" id="plans">
{% for key, item in plans.items() %}
<div class="plan">
<h2>{{ item.name }}</h2>
<div class="price">{{ item.price }}</div>
<ul>
<li>Contas sociais: {{ item.accounts }}</li>
<li>Utilizadores: {{ item.users }}</li>
<li>Analytics e IA</li>
</ul>
<div class="payment">
<strong>Escolha um método de pagamento</strong>
{% if paypal_ready and key != "trial" %}<div class="actions"><a class="btn" href="/billing/paypal/subscribe/{{ key }}">Subscrever com PayPal (Sandbox)</a></div>{% elif key != "trial" %}<p class="muted">Checkout PayPal automático desativado enquanto decorrem os testes de segurança.</p>{% endif %}
<p><strong>PayPal:</strong> pmwconsulte@gmail.com</p>
<div class="actions">
<a class="btn" href="https://www.paypal.com/" target="_blank" rel="noopener noreferrer">Abrir PayPal</a>
<a class="btn secondary" href="mailto:pmwconsulte@gmail.com?subject=Pagamento%20{{ item.name|urlencode }}%20-%20PMW%20Dashboard">Contactar por email</a>
</div>
<p><strong>M-Pesa:</strong> 84 955 0202</p>
<p><strong>mKesh:</strong> 82 300 2555</p>
<div class="actions">
<a class="btn secondary" href="https://wa.me/258823002555?text=Olá%2C%20pretendo%20pagar%20o%20plano%20{{ item.name|urlencode }}%20({{ item.price|urlencode }}).%20Preciso%20de%20confirmar%20os%20dados%20de%20pagamento." target="_blank" rel="noopener noreferrer">Pedir instruções / confirmar</a>
</div>
</div>
</div>
{% endfor %}
</div>
<div class="notice">
<strong>Importante:</strong> estes botões abrem o PayPal ou o contacto da PMW para confirmação. Os pagamentos por M-Pesa e mKesh devem ser enviados para os números indicados. A ativação do plano não é automática: envie o comprovativo à PMW Consultoria e aguarde a confirmação da equipa. Não envie palavras-passe nem códigos PIN.
</div>
</div>
</body>
</html>
"""

SIGNUP_PAGE = """
<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Criar conta — PMW Social Media Dashboard AI</title>
<meta name="robots" content="noindex, nofollow, noarchive">
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:Arial,sans-serif;background:#020617;color:#f8fafc}
.card{width:min(92%,460px);padding:34px;border-radius:18px;background:#111827;border:1px solid #1e293b;box-shadow:0 20px 60px rgba(0,0,0,.35)}
.brand-logo{display:block;width:104px;height:104px;object-fit:contain;margin:0 auto 16px;padding:2px;border-radius:16px;background:#fff}
h1{margin:0 0 8px;text-align:center}p{color:#94a3b8;text-align:center;margin-bottom:24px}
label{display:block;margin:14px 0 7px;font-weight:600}input{width:100%;padding:13px;border-radius:10px;border:1px solid #334155;background:#020617;color:#fff;outline:none}
input:focus{border-color:#38bdf8}button{width:100%;margin-top:22px;padding:13px;border:0;border-radius:10px;background:#38bdf8;color:#020617;font-weight:700;cursor:pointer}
.error{margin-top:16px;padding:10px;border-radius:8px;background:rgba(239,68,68,.12);color:#fca5a5;text-align:center}
.link{display:block;text-align:center;margin-top:18px;color:#38bdf8;text-decoration:none}.footer{margin-top:22px;font-size:12px;color:#64748b;text-align:center}
</style></head><body><main class="card">
<img class="brand-logo" src="/logo.png" alt="Logótipo PMW Consultoria">
<h1>🚀 Criar conta</h1><p>Comece o seu trial gratuito de 30 dias</p>
<form method="post" action="/signup">
<label for="name">Nome / Empresa</label><input id="name" name="name" type="text" autocomplete="name" required>
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required>
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required>
<label for="confirm_password">Confirmar password</label><input id="confirm_password" name="confirm_password" type="password" autocomplete="new-password" minlength="8" required>
<button type="submit">Criar conta</button></form>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<a class="link" href="/login">Já tenho uma conta — Entrar</a><div class="footer">PMW Consultoria & Tecnologia</div>
</main></body></html>
"""

LOGIN_PAGE = """
<!doctype html>
<html lang="pt">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Entrar — PMW Social Media Dashboard AI</title>
    <meta name="robots" content="noindex, nofollow, noarchive">
    <meta name="theme-color" content="#07111f">
    <style>
        :root {
            color-scheme: dark;
            --bg: #07111f;
            --panel: #101d30;
            --panel-soft: #13243a;
            --line: #2a3b52;
            --text: #f4f8ff;
            --muted: #a6b6ca;
            --blue: #52d1f5;
            --blue-strong: #19b8e5;
            --violet: #9b8cff;
            --danger-bg: rgba(248, 113, 113, .12);
            --danger: #fecaca;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            color: var(--text);
            background:
                radial-gradient(ellipse at 12% 10%, rgba(25,184,229,.16), transparent 38%),
                radial-gradient(ellipse at 88% 85%, rgba(155,140,255,.14), transparent 35%),
                var(--bg);
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
            line-height: 1.5;
        }
        a { color: inherit; }
        .page {
            width: min(1180px, calc(100% - 40px));
            min-height: 100vh;
            margin: 0 auto;
            display: grid;
            grid-template-columns: minmax(0, 1.08fr) minmax(340px, .92fr);
            align-items: center;
            gap: clamp(36px, 7vw, 92px);
            padding: 42px 0;
        }
        .brand {
            display: inline-flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 56px;
            text-decoration: none;
        }
        .brand-mark {
            width: 72px;
            height: 72px;
            display: block;
            object-fit: contain;
            padding: 2px;
            border-radius: 14px;
            background: #fff;
            box-shadow: 0 8px 28px rgba(82,209,245,.2);
        }
        .brand-name { display: block; font-size: 16px; font-weight: 800; letter-spacing: -.02em; }
        .brand-sub { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
        .eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 7px 11px;
            border: 1px solid rgba(82,209,245,.25);
            border-radius: 999px;
            background: rgba(82,209,245,.07);
            color: #a6ecff;
            font-size: 12px;
            font-weight: 750;
            letter-spacing: .06em;
            text-transform: uppercase;
        }
        .eyebrow-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--blue); }
        h1 {
            max-width: 650px;
            margin: 22px 0 18px;
            font-size: clamp(36px, 4.6vw, 60px);
            line-height: 1.06;
            letter-spacing: -.055em;
        }
        .gradient-text {
            color: var(--blue);
            background: linear-gradient(90deg, var(--blue), #b6aaff);
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .lead { max-width: 540px; color: var(--muted); font-size: 17px; }
        .feature-list { display: grid; gap: 14px; margin: 32px 0 0; padding: 0; list-style: none; }
        .feature { display: flex; align-items: flex-start; gap: 12px; color: #dbe8f7; font-size: 14px; }
        .feature-icon {
            flex: 0 0 34px;
            width: 34px;
            height: 34px;
            display: grid;
            place-items: center;
            border: 1px solid var(--line);
            border-radius: 11px;
            background: rgba(19,36,58,.8);
        }
        .feature strong { display: block; margin-bottom: 2px; font-size: 14px; }
        .feature span:last-child { color: var(--muted); }
        .login-wrap { width: 100%; max-width: 440px; justify-self: end; }
        .login-card {
            padding: clamp(25px, 4vw, 38px);
            border: 1px solid rgba(151,174,202,.2);
            border-radius: 24px;
            background: linear-gradient(160deg, rgba(19,36,58,.97), rgba(12,25,42,.98));
            box-shadow: 0 28px 90px rgba(0,0,0,.32);
        }
        .mobile-brand { display: none; }
        .form-kicker { margin: 0 0 7px; color: var(--blue); font-size: 12px; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
        h2 { margin: 0; font-size: 29px; line-height: 1.2; letter-spacing: -.035em; }
        .form-intro { margin: 10px 0 26px; color: var(--muted); font-size: 14px; }
        .field { margin-top: 18px; }
        label { display: block; margin-bottom: 8px; color: #e6eef9; font-size: 13px; font-weight: 700; }
        input {
            display: block;
            width: 100%;
            min-height: 49px;
            padding: 13px 14px;
            border: 1px solid #354960;
            border-radius: 11px;
            outline: none;
            background: #081423;
            color: var(--text);
            font: inherit;
            font-size: 14px;
            transition: border-color .15s ease, box-shadow .15s ease;
        }
        input::placeholder { color: #71849b; }
        input:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(82,209,245,.13); }
        .submit {
            width: 100%;
            min-height: 50px;
            display: inline-flex;
            justify-content: center;
            align-items: center;
            gap: 9px;
            margin-top: 25px;
            padding: 13px 16px;
            border: 0;
            border-radius: 11px;
            background: linear-gradient(100deg, var(--blue), #8ce7ff);
            color: #04121e;
            font: inherit;
            font-size: 14px;
            font-weight: 850;
            cursor: pointer;
            box-shadow: 0 10px 26px rgba(25,184,229,.15);
        }
        .submit:hover { filter: brightness(1.04); }
        .divider { display: flex; align-items: center; gap: 12px; margin: 23px 0 18px; color: #8294aa; font-size: 11px; }
        .divider::before, .divider::after { content: ""; height: 1px; flex: 1; background: var(--line); }
        .signup { text-align: center; color: var(--muted); font-size: 13px; }
        .signup a { color: #a7edff; font-weight: 800; text-decoration: none; }
        .signup a:hover { text-decoration: underline; }
        .security-note { display: flex; justify-content: center; align-items: center; gap: 7px; margin-top: 22px; color: #93a6bc; font-size: 11px; text-align: center; }
        .error {
            margin-top: 18px;
            padding: 12px 13px;
            border: 1px solid rgba(248,113,113,.25);
            border-radius: 10px;
            background: var(--danger-bg);
            color: var(--danger);
            font-size: 13px;
            overflow-wrap: anywhere;
        }
        .footer { margin-top: 22px; color: #71849b; font-size: 11px; text-align: center; }
        @media (max-width: 800px) {
            .page { width: min(520px, calc(100% - 32px)); grid-template-columns: 1fr; gap: 26px; padding: 24px 0 32px; }
            .intro-panel { display: none; }
            .login-wrap { max-width: none; }
            .mobile-brand { display: flex; margin: 0 0 22px; }
            .login-card { padding: clamp(24px, 7vw, 34px); border-radius: 20px; }
        }
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after { transition: none !important; scroll-behavior: auto !important; }
        }
    </style>
</head>
<body>
    <main class="page">
        <section class="intro-panel" aria-label="Apresentação da plataforma">
            <a class="brand" href="/login" aria-label="PMW Social Media Dashboard AI">
                <img class="brand-mark" src="/logo.png" alt="Logótipo PMW Consultoria">
                <span>
                    <span class="brand-name">PMW Dashboard AI</span>
                    <span class="brand-sub">Social Media Intelligence</span>
                </span>
            </a>
            <div class="eyebrow"><span class="eyebrow-dot" aria-hidden="true"></span> Analytics · Métricas · IA</div>
            <h1>As suas redes sociais.<br><span class="gradient-text">Uma visão mais inteligente.</span></h1>
            <p class="lead">Acompanhe o desempenho, compreenda as tendências e transforme os dados disponíveis em decisões mais informadas.</p>
            <ul class="feature-list">
                <li class="feature">
                    <span class="feature-icon" aria-hidden="true">↗</span>
                    <span><strong>Métricas organizadas</strong><span>Acompanhe seguidores, alcance e envolvimento num só espaço.</span></span>
                </li>
                <li class="feature">
                    <span class="feature-icon" aria-hidden="true">✦</span>
                    <span><strong>Apoio de inteligência artificial</strong><span>Consulte alertas, recomendações e relatórios com base nos dados disponíveis.</span></span>
                </li>
                <li class="feature">
                    <span class="feature-icon" aria-hidden="true">⌁</span>
                    <span><strong>Área de trabalho protegida</strong><span>Entre na sua conta para consultar o dashboard e os dados autorizados.</span></span>
                </li>
            </ul>
        </section>

        <section class="login-wrap" aria-labelledby="login-title">
            <a class="brand mobile-brand" href="/login" aria-label="PMW Social Media Dashboard AI">
                <span class="brand-mark" aria-hidden="true">↗</span>
                <span>
                    <span class="brand-name">PMW Dashboard AI</span>
                    <span class="brand-sub">Social Media Intelligence</span>
                </span>
            </a>
            <div class="login-card">
                <p class="form-kicker">Bem-vindo de volta</p>
                <h2 id="login-title">Entrar na plataforma</h2>
                <p class="form-intro">Introduza as suas credenciais para continuar para o seu dashboard.</p>
                <form method="post" action="/login">
                    <div class="field">
                        <label for="username">Email ou nome de utilizador</label>
                        <input id="username" name="username" type="text" autocomplete="username"
                               placeholder="O seu email ou utilizador" required autofocus>
                    </div>
                    <div class="field">
                        <label for="password">Palavra-passe</label>
                        <input id="password" name="password" type="password" autocomplete="current-password"
                               placeholder="Introduza a sua palavra-passe" required>
                    </div>
                    <button class="submit" type="submit">Entrar com segurança <span aria-hidden="true">→</span></button>
                </form>
                {% if error %}
                <div class="error" role="alert">{{ error }}</div>
                {% endif %}
                <div class="divider">NOVO NA PMW?</div>
                <div class="signup">Crie a sua conta e conheça o <a href="/signup">trial de 30 dias</a>.</div>
                <div class="security-note"><span aria-hidden="true">🔒</span> Acesso reservado a utilizadores autenticados</div>
            </div>
            <div class="footer">© 2026 PMW Consultoria &amp; Tecnologia</div>
        </section>
    </main>
</body>
</html>
"""


def _env_bool(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


ACCOUNT_PAGE = """<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Minha Conta — PMW Social Media Dashboard AI</title>
<style>body{margin:0;background:#020617;color:#f8fafc;font-family:Arial,sans-serif}.wrap{max-width:1050px;margin:35px auto;padding:20px}.card{background:#111827;border:1px solid #1e293b;border-radius:18px;padding:24px;margin-bottom:20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}label{display:block;margin:12px 0 7px;font-weight:600}input,select{width:100%;padding:12px;border-radius:9px;border:1px solid #334155;background:#020617;color:#fff;box-sizing:border-box}button,.btn{display:inline-block;padding:11px 16px;border:0;border-radius:9px;background:#38bdf8;color:#020617;font-weight:700;text-decoration:none;cursor:pointer;margin-top:12px}.danger{background:#ef4444;color:#fff}.muted{color:#94a3b8}.ok{color:#86efac}.err{color:#fca5a5}.tag{display:inline-block;padding:5px 9px;border-radius:999px;background:#172554;color:#7dd3fc;font-size:12px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px;border-bottom:1px solid #1e293b}</style></head><body><div class="wrap">
<div class="card"><a class="btn" href="/">← Dashboard</a> <a class="btn" href="/account#social-connections" style="background:#a855f7;color:#fff">🔗 Conectar Redes Sociais</a> <a class="btn" href="/billing">💳 Plano</a> <a class="btn danger" href="/logout">Sair</a><h1>👤 Minha Conta</h1><p class="muted">Gerencie o seu perfil, password e contas sociais.</p>{% if message %}<p class="ok">{{ message }}</p>{% endif %}{% if error %}<p class="err">{{ error }}</p>{% endif %}</div><div id="social-connections" class="card" style="border:2px solid #38bdf8;background:linear-gradient(135deg,#0f172a,#082f49)"><h2 style="margin-top:0">🔗 Conectar Redes Sociais</h2><p class="muted">Ligue as suas redes sociais através da autenticação oficial. O cliente será redirecionado para cada plataforma, fará login e autorizará o acesso. Nunca introduza password, token, App ID ou App Secret neste dashboard.</p><div class="grid"><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>📷 Instagram</h3><p class="muted">Instagram Business / Creator</p><a class="btn" href="/oauth/instagram">📷 Conectar Instagram</a></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>📘 Facebook</h3><p class="muted">Facebook Pages</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>💬 WhatsApp</h3><p class="muted">WhatsApp Business Platform</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>🎵 TikTok</h3><p class="muted">TikTok for Business</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>▶️ YouTube</h3><p class="muted">YouTube / Google</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>💼 LinkedIn</h3><p class="muted">LinkedIn Pages</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>🐦 X</h3><p class="muted">X / Twitter</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div></div></div>
<div class="grid"><div class="card"><h2>Perfil</h2><form method="post" action="/account"><label>Nome</label><input name="name" value="{{ user_name }}" required><label>Email</label><input value="{{ email }}" disabled><label>Workspace</label><input value="{{ workspace_name }}" disabled><label>Plano</label><p><span class="tag">{{ plan_name }}</span> {% if days_left %}<span class="muted">{{ days_left }} dias de trial</span>{% endif %}</p><button type="submit">Guardar perfil</button></form></div>
<div class="card"><h2>Alterar password</h2><form method="post" action="/account/password"><label>Password atual</label><input name="current_password" type="password" required><label>Nova password</label><input name="new_password" type="password" minlength="8" required><label>Confirmar nova password</label><input name="confirm_password" type="password" minlength="8" required><button type="submit">Alterar password</button></form></div></div>
<div class="card" style="border:2px solid #a855f7;background:#111827">
<h2>➕ Adicionar Rede Social</h2>
<p class="muted">Selecione a plataforma. A autenticação será feita na plataforma oficial; não introduza passwords ou tokens neste dashboard.</p>
<form method="post" action="/social-accounts/add" style="display:grid;grid-template-columns:1fr auto;gap:12px;align-items:end">
<div><label>Rede Social</label>
<select name="platform" required>
<option value="">— Selecionar plataforma —</option>
<option value="Instagram">📷 Instagram</option>
<option value="Facebook">📘 Facebook</option>
<option value="WhatsApp">💬 WhatsApp</option>
<option value="TikTok">🎵 TikTok</option>
<option value="YouTube">▶️ YouTube</option>
<option value="LinkedIn">💼 LinkedIn</option>
<option value="X">🐦 X (Twitter)</option>
</select></div>
<div><button type="submit">🔗 Continuar</button></div>
</form>
</div>
<div class="card"><h2>🔗 Contas Sociais</h2><p class="muted">Ligue as suas redes sociais através de autenticação oficial. Não é necessário inserir tokens ou credenciais no dashboard.</p>
<div style="background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:18px;margin:16px 0"><h3 style="margin-top:0">📷 Instagram</h3><p class="muted">O cliente será redirecionado para o Instagram, fará login e autorizará o acesso. O token é armazenado cifrado no servidor.</p><a class="btn" href="/oauth/instagram">📷 Conectar Instagram</a></div>
{% if social_accounts %}<table><thead><tr><th>Plataforma</th><th>Conta</th><th>Estado</th><th>Ação</th></tr></thead><tbody>{% for item in social_accounts %}<tr><td>{{ item.platform }}</td><td>{{ item.display_name or item.username }}</td><td>{{ "🟢 Conectada" if item.connection_status == "connected" and item.is_active else ("🟡 Ativa / Pendente" if item.is_active else "⚪ Desativada") }}</td><td><form method="post" action="/social-accounts/{{ item.id }}/toggle"><button type="submit" class="{{ 'danger' if item.is_active else '' }}">{{ "Desativar" if item.is_active else "Ativar" }}</button></form></td></tr>{% endfor %}</tbody></table>{% else %}<p class="muted">Ainda não existem contas sociais conectadas neste workspace.</p>{% endif %}</div></div></body></html>"""

def setup_auth(server: Flask):
    """Configure secure session authentication for the Dash server."""
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY não está configurada no ambiente.")

    server.secret_key = secret_key
    server.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=_env_bool("SESSION_COOKIE_SECURE", True),
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=3600,
    )

    @server.before_request
    def require_dashboard_login():
        """Protect the dashboard UI and Dash data/callback endpoints."""
        path = request.path
        is_dash_endpoint = path.startswith("/_dash-") and not path.startswith(
            "/_dash-component-suites/"
        )
        if (path == "/" or is_dash_endpoint) and not session.get("authenticated"):
            if path == "/":
                return redirect(url_for("login"))
            return "Authentication required", 401

    @server.get("/health")
    def health():
        return "OK", 200

    @server.get("/billing")
    def billing():
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        try:
            conn = get_connection()
            try:
                access = evaluate_workspace_access(conn, session.get("workspace_slug"))
            finally:
                conn.close()
        except Exception as erro:
            print(f"[BILLING ERROR] {erro}")
            return "Serviço de faturação temporariamente indisponível.", 503

        workspace = access.get("workspace") or {}
        plan_key = access.get("plan", "trial")
        plan_name = PLANS.get(plan_key, PLANS["trial"])["name"]

        return render_template_string(
            BILLING_PAGE,
            status=access.get("status", "unknown"),
            plan_name=plan_name,
            days_left=access.get("days_left", 0),
            expired=not access.get("allowed", False),
            plans=PLANS,
            paypal_ready=(
                all(os.getenv(k, "").strip() for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_PLAN_STARTER_ID", "PAYPAL_PLAN_PRO_ID", "PAYPAL_PLAN_BUSINESS_ID"))
                and (
                    os.getenv("PAYMENT_AUTOMATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
                    or (
                        os.getenv("PAYPAL_CHECKOUT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
                        and os.getenv("PAYPAL_MODE", "sandbox").strip().lower() == "sandbox"
                    )
                )
            ),
        )

    @server.after_request
    def security_headers(response):
        """Apply browser security headers without changing OAuth behavior."""
        # Páginas de autenticação e áreas privadas não devem aparecer nos resultados
        # de pesquisa; X-Robots-Tag também cobre respostas que não são HTML.
        noindex_paths = (
            "/login", "/signup", "/account", "/account/password", "/billing",
            "/logout", "/oauth/instagram", "/oauth/instagram/callback",
            "/health", "/diagnostico-dados",
        )
        if request.path in noindex_paths or request.path.startswith("/social-accounts/"):
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"

        # Páginas autenticadas nunca devem ser reutilizadas pelo cache do browser.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

        # Harden browser-side protections. These headers do not alter OAuth
        # redirects, callback parameters, cookies, or external API requests.
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        # Render terminates TLS before Flask. HSTS is therefore safe to send
        # only for HTTPS requests and will force future browser connections
        # to remain on HTTPS.
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # Keep CSP scoped to the authentication UI. Dash itself may require
        # additional browser resources, so do not impose this policy globally.
        auth_paths = {
            "/login",
            "/signup",
            "/account",
            "/account/password",
            "/billing",
            "/logout",
            "/oauth/instagram",
            "/oauth/instagram/callback",
            "/health",
        }
        if request.path in auth_paths:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "object-src 'none'; "
                "script-src 'none'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self' data:"
            )

        return response

    @server.get("/diagnostico-dados")
    def diagnostico_dados():
        """Diagnóstico privado e limitado ao workspace da sessão; nunca expõe tokens."""
        if not session.get("authenticated"):
            return jsonify({"erro": "autenticacao_necessaria"}), 401

        workspace_slug = session.get("workspace_slug") or os.getenv(
            "DEFAULT_WORKSPACE_SLUG", "pmw-default"
        )
        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sa.username, sa.platform, sa.connection_status,
                              COALESCE(sa.is_active, TRUE) AS ativa,
                              COUNT(dm.id) AS total_registos,
                              MIN(dm.report_date) AS primeira_data,
                              MAX(dm.report_date) AS ultima_data
                       FROM social_accounts sa
                       JOIN workspaces w ON w.id = sa.workspace_id
                       LEFT JOIN daily_metrics dm ON dm.social_account_id = sa.id
                       WHERE w.slug = %s
                       GROUP BY sa.id, sa.username, sa.platform,
                                sa.connection_status, sa.is_active
                       ORDER BY sa.platform, sa.username""",
                    (workspace_slug,),
                )
                account_rows = cur.fetchall()
                contas = [{
                    "conta": row[0],
                    "plataforma": row[1],
                    "estado_ligacao": row[2],
                    "ativa": row[3],
                    "registos_de_dados": row[4],
                    "primeira_data": row[5].isoformat() if row[5] else None,
                    "ultima_data": row[6].isoformat() if row[6] else None,
                } for row in account_rows]

                cur.execute(
                    """SELECT sa.username, sa.platform, dm.report_date,
                              dm.followers, dm.reach, dm.impressions,
                              dm.interactions_count, dm.engagement_rate
                       FROM social_accounts sa
                       JOIN workspaces w ON w.id = sa.workspace_id
                       LEFT JOIN daily_metrics dm ON dm.social_account_id = sa.id
                       WHERE w.slug = %s
                         AND COALESCE(sa.is_active, TRUE) = TRUE
                       ORDER BY sa.platform, sa.username, dm.report_date DESC
                       LIMIT 100""",
                    (workspace_slug,),
                )
                rows = cur.fetchall()
            records = []
            for row in rows:
                records.append({
                    "conta": row[0],
                    "plataforma": row[1],
                    "data": row[2].isoformat() if row[2] else None,
                    "seguidores": row[3],
                    "alcance": row[4],
                    "visualizacoes": row[5],
                    "interacoes": row[6],
                    "engajamento_percentual": float(row[7]) if row[7] is not None else None,
                })
            response = jsonify({
                "workspace": workspace_slug,
                "total_contas": len(contas),
                "total_contas_ativas": sum(1 for conta in contas if conta["ativa"]),
                "registos_de_dados": sum(conta["registos_de_dados"] for conta in contas),
                "contas": contas,
                "nota": "Resumo por conta e métricas limitados ao workspace autenticado; tokens e credenciais não são incluídos.",
                "metricas": records,
            })
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as erro:
            print(f"[DIAGNOSTICO DADOS ERROR] {type(erro).__name__}")
            return jsonify({"erro": "nao_foi_possivel_consultar_metricas"}), 500
        finally:
            if conn:
                conn.close()

    @server.get("/logout")
    def logout():
        # Invalida completamente a sessão atual e remove o cookie no browser.
        session.clear()
        response = redirect(url_for("login", logged_out="1"))
        response.delete_cookie(
            server.config.get("SESSION_COOKIE_NAME", "session"),
            path=server.config.get("SESSION_COOKIE_PATH", "/"),
            domain=server.config.get("SESSION_COOKIE_DOMAIN"),
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @server.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("authenticated"):
            try:
                conn = get_connection()
                try:
                    # Use o workspace da sessão, não o workspace global por defeito.
                    access = evaluate_workspace_access(
                        conn, session.get("workspace_slug")
                    )
                finally:
                    conn.close()
                if access["allowed"]:
                    return redirect("/")
                return redirect(url_for("billing"))
            except Exception as erro:
                print(f"[BILLING LOGIN ERROR] {erro}")
                return redirect(url_for("billing"))

        error = None

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            valid = False
            user_workspace_slug = None

            configured_username = os.getenv("ADMIN_USERNAME", "").strip()
            password_hash = os.getenv("ADMIN_PASSWORD_HASH", "").strip()

            if configured_username and password_hash:
                try:
                    valid = (
                        username == configured_username
                        and bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
                    )
                except (ValueError, TypeError):
                    valid = False

            if valid:
                user_workspace_slug = os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
            else:
                try:
                    conn = get_connection()
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                """SELECT email, password_hash, workspace_id
                                   FROM users
                                   WHERE LOWER(email) = LOWER(%s)
                                     AND COALESCE(is_active, TRUE) = TRUE
                                   LIMIT 1;""",
                                (username,),
                            )
                            row = cur.fetchone()
                            if row and row[1]:
                                valid = bcrypt.checkpw(password.encode("utf-8"), row[1].encode("utf-8"))
                                if valid and row[2]:
                                    cur.execute("SELECT slug FROM workspaces WHERE id = %s LIMIT 1;", (row[2],))
                                    workspace_row = cur.fetchone()
                                    if workspace_row:
                                        user_workspace_slug = workspace_row[0]
                    finally:
                        conn.close()
                except Exception as erro:
                    print(f"[LOGIN DATABASE ERROR] {erro}")
                    valid = False

            if valid:
                session.clear()
                session["authenticated"] = True
                session["username"] = username
                session["workspace_slug"] = user_workspace_slug or os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
                session.permanent = True
                return redirect("/")

            error = "Email/username ou password inválido."

        return render_template_string(LOGIN_PAGE, error=error)

    @server.route("/signup", methods=["GET", "POST"])
    def signup():
        if session.get("authenticated"):
            return redirect("/")

        error = None
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not name or not email or not password:
                error = "Preencha todos os campos obrigatórios."
            elif len(password) < 8:
                error = "A password deve ter pelo menos 8 caracteres."
            elif password != confirm_password:
                error = "As passwords não coincidem."
            else:
                conn = None
                try:
                    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                    base_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "workspace"
                    workspace_slug = f"{base_slug}-{uuid.uuid4().hex[:8]}"

                    conn = get_connection()
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1;", (email,))
                        if cur.fetchone():
                            error = "Este email já está registado."
                        else:
                            cur.execute(
                                """INSERT INTO workspaces
                                   (name, slug, trial_started_at, trial_ends_at, subscription_status, subscription_plan)
                                   VALUES (%s, %s, NOW(), NOW() + INTERVAL '30 days', 'trialing', 'trial')
                                   RETURNING id, slug;""",
                                (f"{name} Workspace", workspace_slug),
                            )
                            workspace_id, workspace_slug = cur.fetchone()
                            cur.execute(
                                """INSERT INTO users
                                   (name, email, password_hash, plan_type, workspace_id, role, is_active)
                                   VALUES (%s, %s, %s, 'free', %s, 'owner', TRUE);""",
                                (name, email, password_hash, workspace_id),
                            )
                            conn.commit()
                            session.clear()
                            session["authenticated"] = True
                            session["username"] = email
                            session["workspace_slug"] = workspace_slug
                            session.permanent = True
                            return redirect("/")
                except Exception as erro:
                    if conn:
                        conn.rollback()
                    print(f"[SIGNUP ERROR] {erro}")
                    error = "Não foi possível criar a conta. Tente novamente."
                finally:
                    if conn:
                        conn.close()

        return render_template_string(SIGNUP_PAGE, error=error)

    @server.get("/oauth/instagram")
    def instagram_oauth():
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        if not _oauth_configured():
            return redirect(
                url_for(
                    "account",
                    error="Instagram OAuth ainda não está configurado no servidor. Verifique as variáveis de ambiente.",
                )
            )

        try:
            state = secrets.token_urlsafe(32)
            session["instagram_oauth_state"] = state
            session["instagram_oauth_workspace"] = session.get("workspace_slug")
            session.modified = True

            authorize_url = _instagram_authorize_url(state)
            print(
                "[INSTAGRAM OAUTH START] "
                f"workspace={session.get('workspace_slug')} "
                f"redirect_uri={os.getenv('INSTAGRAM_OAUTH_REDIRECT_URI')}"
            )
            return redirect(authorize_url)

        except Exception as erro:
            print(f"[INSTAGRAM OAUTH START ERROR] {type(erro).__name__}: {erro}")
            return redirect(
                url_for(
                    "account",
                    error=f"Não foi possível iniciar o Instagram OAuth: {type(erro).__name__}.",
                )
            )

    @server.get("/oauth/instagram/callback")
    def instagram_oauth_callback():
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        if request.args.get("error"):
            return redirect(url_for("account", error="A autorização do Instagram foi cancelada ou recusada."))
        state = request.args.get("state", "")
        expected = session.pop("instagram_oauth_state", None)
        workspace_slug = session.pop("instagram_oauth_workspace", None)
        if not state or not expected or not secrets.compare_digest(state, expected):
            return redirect(url_for("account", error="Falha de segurança no estado OAuth. Tente novamente."))
        code = request.args.get("code", "")
        if not code or workspace_slug != session.get("workspace_slug"):
            return redirect(url_for("account", error="Código OAuth inválido ou sessão expirada."))
        conn = None
        try:
            token_response = requests.post(
                "https://api.instagram.com/oauth/access_token",
                data={
                    "client_id": os.getenv("INSTAGRAM_APP_ID"),
                    "client_secret": os.getenv("INSTAGRAM_APP_SECRET"),
                    "grant_type": "authorization_code",
                    "redirect_uri": os.getenv("INSTAGRAM_OAUTH_REDIRECT_URI"),
                    "code": code,
                },
                timeout=15,
            )
            token_response.raise_for_status()
            short_data = token_response.json()
            short_token = short_data.get("access_token")
            if not short_token:
                raise RuntimeError("Instagram não devolveu access_token.")

            long_response = requests.get(
                "https://graph.instagram.com/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": os.getenv("INSTAGRAM_APP_SECRET"),
                    "access_token": short_token,
                },
                timeout=15,
            )
            long_response.raise_for_status()
            long_data = long_response.json()
            access_token = long_data.get("access_token", short_token)

            profile_response = requests.get(
                "https://graph.instagram.com/v25.0/me",
                params={"fields": "id,username,account_type", "access_token": access_token},
                timeout=15,
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
            ig_id = str(profile.get("id", ""))
            username = profile.get("username") or ("instagram_" + ig_id)
            account_type = str(profile.get("account_type", "")).upper()
            if not ig_id:
                raise RuntimeError("Não foi possível obter o ID do Instagram.")
            if account_type and account_type not in {"BUSINESS", "CREATOR"}:
                raise RuntimeError(
                    "A conta Instagram precisa ser profissional (Business ou Creator)."
                )

            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT id, subscription_plan FROM workspaces WHERE slug=%s LIMIT 1", (workspace_slug,))
                workspace = cur.fetchone()
                if not workspace:
                    raise RuntimeError("Workspace não encontrado.")

                plan_key = workspace[1] or "trial"
                account_limit = PLANS.get(plan_key, PLANS["trial"])["accounts"]
                cur.execute(
                    """SELECT COUNT(*) FROM social_accounts
                       WHERE workspace_id=%s AND COALESCE(is_active, TRUE)=TRUE""",
                    (workspace[0],),
                )
                active_accounts = cur.fetchone()[0]
                cur.execute(
                    """SELECT id FROM social_accounts
                       WHERE workspace_id=%s AND platform='Instagram'
                         AND LOWER(username)=LOWER(%s) LIMIT 1""",
                    (workspace[0], username),
                )
                existing = cur.fetchone()

                # A renovação de uma conta já conectada não consome novo limite.
                # Uma nova conta só pode ser criada dentro do limite do plano.
                if not existing and isinstance(account_limit, int) and active_accounts >= account_limit:
                    raise RuntimeError(
                        f"O plano {PLANS.get(plan_key, PLANS['trial'])['name']} permite no máximo "
                        f"{account_limit} contas sociais ativas."
                    )

                encrypted = _encrypt_token(access_token)
                if existing:
                    social_account_id = existing[0]
                    cur.execute(
                        """UPDATE social_accounts
                           SET access_token=%s, display_name=%s,
                               connection_status='connected', is_active=TRUE,
                               last_sync_at=NOW()
                           WHERE id=%s AND workspace_id=%s""",
                        (encrypted, username, social_account_id, workspace[0]),
                    )
                else:
                    cur.execute(
                        """INSERT INTO social_accounts
                           (workspace_id, platform, username, display_name,
                            access_token, connection_status, is_active, last_sync_at)
                           VALUES (%s,'Instagram',%s,%s,%s,'connected',TRUE,NOW())
                           RETURNING id""",
                        (workspace[0], username, username, encrypted),
                    )
                    social_account_id = cur.fetchone()[0]
            conn.commit()
            synced = _sync_instagram_metrics(social_account_id, ig_id, access_token)
            if synced:
                return redirect(url_for("account", message="Instagram conectado e métricas sincronizadas."))
            return redirect(url_for("account", message="Instagram conectado. Algumas métricas ainda não estão disponíveis; verifique as permissões e os logs."))
        except (requests.RequestException, ValueError, InvalidToken) as erro:
            if conn: conn.rollback()
            print(f"[INSTAGRAM OAUTH ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível concluir a ligação ao Instagram."))
        except Exception as erro:
            if conn: conn.rollback()
            print(f"[INSTAGRAM OAUTH ERROR] {erro}")
            return redirect(url_for("account", error="Erro ao configurar a conta Instagram."))
        finally:
            if conn: conn.close()

    @server.route("/account", methods=["GET", "POST"])
    def account():
        if not session.get("authenticated"):
            return redirect("/login")

        message = request.args.get("message")
        error = request.args.get("error")
        conn = None

        try:
            conn = get_connection()
            slug = session.get("workspace_slug")

            with conn.cursor() as cur:
                if request.method == "POST":
                    name = request.form.get("name", "").strip()
                    if not name:
                        return redirect(url_for("account", error="O nome é obrigatório."))

                    cur.execute(
                        """UPDATE users
                           SET name=%s
                           WHERE LOWER(email)=LOWER(%s)
                             AND workspace_id=(SELECT id FROM workspaces WHERE slug=%s)""",
                        (name, session.get("username"), slug),
                    )
                    conn.commit()
                    return redirect(url_for("account", message="Perfil atualizado com sucesso."))

                cur.execute(
                    """SELECT u.name,u.email,w.name,w.subscription_plan,w.trial_ends_at
                       FROM users u
                       LEFT JOIN workspaces w ON w.id=u.workspace_id
                       WHERE LOWER(u.email)=LOWER(%s) AND w.slug=%s
                       LIMIT 1""",
                    (session.get("username"), slug),
                )
                user = cur.fetchone()

                cur.execute(
                    """SELECT id,platform,username,display_name,connection_status,is_active
                       FROM social_accounts
                       WHERE workspace_id=(SELECT id FROM workspaces WHERE slug=%s)
                       ORDER BY created_at DESC""",
                    (slug,),
                )
                accounts = [
                    {
                        "id": str(row[0]),
                        "platform": row[1],
                        "username": row[2],
                        "display_name": row[3],
                        "connection_status": row[4],
                        "is_active": row[5],
                    }
                    for row in cur.fetchall()
                ]

            if not user:
                session.clear()
                return redirect("/login")

            from datetime import datetime, timezone
            days_left = 0
            if user[4]:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                days_left = max(
                    0,
                    int(((user[4] - now).total_seconds() + 86399) // 86400),
                )

            return render_template_string(
                ACCOUNT_PAGE,
                user_name=user[0] or "",
                email=user[1] or session.get("username", ""),
                workspace_name=user[2] or slug,
                plan_name=PLANS.get(user[3] or "trial", PLANS["trial"])["name"],
                days_left=days_left,
                social_accounts=accounts,
                message=message,
                error=error,
            )

        except Exception as erro:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass

            import traceback
            print(f"[ACCOUNT ERROR] {type(erro).__name__}: {erro}")
            traceback.print_exc()

            return render_template_string(
                ACCOUNT_PAGE,
                user_name=session.get("username", ""),
                email=session.get("username", ""),
                workspace_name=session.get("workspace_slug", ""),
                plan_name="Trial",
                days_left=0,
                social_accounts=[],
                message=message,
                error="Não foi possível carregar os dados da conta. O erro foi registado no servidor.",
            ), 200

    @server.post("/account/password")
    def change_password():
        if not session.get("authenticated"): return redirect(url_for("login"))
        current=request.form.get("current_password",""); new=request.form.get("new_password",""); confirm=request.form.get("confirm_password","")
        if len(new)<8: return redirect(url_for("account",error="A nova password deve ter pelo menos 8 caracteres."))
        if new!=confirm: return redirect(url_for("account",error="As novas passwords não coincidem."))
        conn=None
        try:
            conn=get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE LOWER(email)=LOWER(%s) LIMIT 1",(session.get("username"),)); row=cur.fetchone()
                if not row or not row[0] or not bcrypt.checkpw(current.encode(),row[0].encode()): return redirect(url_for("account",error="A password atual está incorreta."))
                cur.execute("UPDATE users SET password_hash=%s WHERE LOWER(email)=LOWER(%s)",(bcrypt.hashpw(new.encode(),bcrypt.gensalt()).decode(),session.get("username")))
            conn.commit(); return redirect(url_for("account",message="Password alterada com sucesso."))
        except Exception as erro:
            if conn: conn.rollback()
            print(f"[PASSWORD ERROR] {erro}"); return redirect(url_for("account",error="Não foi possível alterar a password."))
        finally:
            if conn: conn.close()

    @server.post("/social-accounts/add")
    def add_social_account():
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        platform = request.form.get("platform", "").strip()
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip() or username

        if platform == "Instagram":
            return redirect(url_for("instagram_oauth"))

        # As integrações que ainda não têm OAuth implementado não devem
        # pedir username/token manualmente. O fluxo comercial deve sempre
        # encaminhar o cliente para a autenticação oficial da plataforma.
        coming_soon = {
            "Facebook": "Facebook OAuth",
            "WhatsApp": "WhatsApp Business",
            "TikTok": "TikTok OAuth",
            "YouTube": "YouTube / Google OAuth",
            "LinkedIn": "LinkedIn OAuth",
            "X": "X OAuth",
        }
        if platform in coming_soon:
            return redirect(
                url_for(
                    "account",
                    error=f"{coming_soon[platform]} ainda está em implementação. "
                          "Não introduza credenciais ou tokens manualmente.",
                )
            )

        return redirect(url_for("account", error="Plataforma social inválida."))

        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, subscription_plan FROM workspaces WHERE slug=%s LIMIT 1",
                    (session.get("workspace_slug"),),
                )
                workspace = cur.fetchone()
                if not workspace:
                    return redirect(url_for("account", error="Workspace não encontrado."))

                plan = workspace[1] or "trial"
                limit = PLANS.get(plan, PLANS["trial"])["accounts"]

                cur.execute(
                    """SELECT COUNT(*) FROM social_accounts
                       WHERE workspace_id=%s AND COALESCE(is_active, TRUE)""",
                    (workspace[0],),
                )
                count = cur.fetchone()[0]

                if isinstance(limit, int) and count >= limit:
                    plan_name = PLANS.get(plan, PLANS["trial"])["name"]
                    return redirect(
                        url_for(
                            "account",
                            error=f"O plano {plan_name} permite no máximo {limit} contas sociais ativas.",
                        )
                    )

                cur.execute(
                    """INSERT INTO social_accounts
                       (workspace_id, platform, username, display_name,
                        connection_status, is_active)
                       VALUES (%s, %s, %s, %s, 'pending', TRUE)""",
                    (workspace[0], platform, username, display_name),
                )

            conn.commit()
            return redirect(url_for("account", message=f"{platform} adicionada com sucesso."))
        except Exception as erro:
            if conn:
                conn.rollback()
            print(f"[SOCIAL ACCOUNT ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível adicionar a conta social."))
        finally:
            if conn:
                conn.close()

    @server.post("/social-accounts/<account_id>/toggle")
    def toggle_social_account(account_id):
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE social_accounts
                       SET is_active = NOT COALESCE(is_active, TRUE)
                       WHERE id=%s
                         AND workspace_id=(SELECT id FROM workspaces WHERE slug=%s)""",
                    (account_id, session.get("workspace_slug")),
                )
            conn.commit()
            return redirect(url_for("account"))
        except Exception as erro:
            if conn:
                conn.rollback()
            print(f"[SOCIAL TOGGLE ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível alterar o estado da conta."))
        finally:
            if conn:
                conn.close()

    # Rotas PayPal são registadas no fim da configuração, sem ativar pagamentos por omissão.
    from payments import register_paypal_routes
    register_paypal_routes(server)
