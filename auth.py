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
    g,
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


def _facebook_oauth_configured():
    """Facebook Login usa as credenciais da app Meta e callback próprios."""
    app_id = os.getenv("FACEBOOK_APP_ID", "").strip() or os.getenv("INSTAGRAM_APP_ID", "").strip()
    app_secret = os.getenv("FACEBOOK_APP_SECRET", "").strip() or os.getenv("INSTAGRAM_APP_SECRET", "").strip()
    redirect_uri = os.getenv("FACEBOOK_OAUTH_REDIRECT_URI", "").strip()
    return bool(app_id and app_secret and redirect_uri and os.getenv("OAUTH_TOKEN_ENCRYPTION_KEY", "").strip())


def _facebook_authorize_url(state):
    from urllib.parse import urlencode

    app_id = os.getenv("FACEBOOK_APP_ID", "").strip() or os.getenv("INSTAGRAM_APP_ID", "").strip()
    redirect_uri = os.getenv("FACEBOOK_OAUTH_REDIRECT_URI", "").strip()
    if not app_id or not redirect_uri:
        raise RuntimeError("Configure o App ID Meta e FACEBOOK_OAUTH_REDIRECT_URI.")
    # Facebook Login: permissões necessárias para descobrir a conta
    # Instagram profissional ligada à Página. Insights avançados serão
    # solicitados apenas quando a permissão correspondente estiver validada.
    scopes = ["pages_show_list", "pages_read_engagement", "instagram_basic", "instagram_manage_insights"]
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(scopes),
        "state": state,
    }
    return "https://www.facebook.com/v25.0/dialog/oauth?" + urlencode(params)


def _sync_facebook_metrics(social_account_id, page_id, page_token):
    """Sincroniza métricas reais da Facebook Pages API v25 sem misturar períodos."""
    from datetime import datetime, timezone

    version = os.getenv("FACEBOOK_GRAPH_API_VERSION", "v25.0").strip()
    values = {
        "followers": None,
        "day_views": None,
        "day_unique_viewers": None,
        "day_interactions": None,
        "views_28d": None,
        "unique_viewers_28d": None,
        "interactions_28d": None,
    }

    def metric_value(metric, period):
        try:
            response = requests.get(
                f"https://graph.facebook.com/{version}/{page_id}/insights",
                params={"metric": metric, "period": period, "access_token": page_token},
                timeout=15,
            )
            if not response.ok:
                try:
                    detail = response.json().get("error", {}).get("message", "")
                except (ValueError, AttributeError):
                    detail = ""
                print(
                    f"[FACEBOOK INSIGHTS] {metric} period={period} indisponível "
                    f"HTTP={response.status_code}: {str(detail)[:180]}"
                )
                return None
            payload = response.json()
            items = payload.get("data", [])
            item = next((x for x in items if x.get("name") == metric), None)
            points = (item or {}).get("values") or []
            for point in reversed(points):
                value = point.get("value")
                if isinstance(value, (int, float)):
                    return int(value)
                if isinstance(value, dict):
                    total = value.get("total_value")
                    if isinstance(total, (int, float)):
                        return int(total)
            # HTTP 200 does not guarantee that Meta returned usable metric values.
            # Log the response shape (never the access token) to distinguish an
            # empty result from an unsupported metric/period or changed API schema.
            names = [str(x.get("name", "?")) for x in items[:8] if isinstance(x, dict)]
            print(
                f"[FACEBOOK INSIGHTS DEBUG] metric={metric} period={period} "
                f"HTTP=200 data_items={len(items)} returned_names={names!r} "
                f"matched_metric={item is not None} values_count={len(points)} "
                f"item_keys={sorted(item.keys()) if isinstance(item, dict) else []} "
                f"error_payload={bool(payload.get('error'))}."
            )
            return None
        except (requests.RequestException, ValueError, AttributeError, TypeError) as erro:
            print(f"[FACEBOOK INSIGHTS] Falha em {metric} period={period}: {type(erro).__name__}.")
            return None

    try:
        # Confirma que o token guardado ainda consegue consultar a Página e
        # recolhe metadados oficiais (likes/fãs e seguidores) sem expor o token.
        response = requests.get(
            f"https://graph.facebook.com/{version}/{page_id}",
            params={
                "fields": "id,name,followers_count,fan_count",
                "access_token": page_token,
            },
            timeout=15,
        )
        try:
            page_payload = response.json()
        except ValueError:
            page_payload = {}
        if response.ok:
            count = page_payload.get("followers_count")
            if isinstance(count, (int, float)):
                values["followers"] = int(count)
            fan_count = page_payload.get("fan_count")
            print(
                "[FACEBOOK TOKEN DIAGNOSTIC] "
                f"page_id_match={str(page_payload.get('id', '')) == str(page_id)} "
                f"name_present={bool(page_payload.get('name'))} "
                f"followers_count={count!r} fan_count={fan_count!r} "
                f"HTTP={response.status_code} token_valid_for_page=True."
            )
        else:
            error = page_payload.get("error", {}) if isinstance(page_payload, dict) else {}
            print(
                "[FACEBOOK TOKEN DIAGNOSTIC] "
                f"HTTP={response.status_code} token_valid_for_page=False "
                f"error_code={error.get('code')!r} "
                f"error_subcode={error.get('error_subcode')!r} "
                f"error_type={error.get('type')!r}."
            )
    except (requests.RequestException, ValueError, TypeError) as erro:
        print(
            "[FACEBOOK TOKEN DIAGNOSTIC] "
            f"Falha ao consultar metadados da Página: {type(erro).__name__}."
        )

    # Métricas atuais da API v25: media views e viewers únicos substituem
    # os antigos nomes de impressões/alcance que a Meta retirou em 2026.
    day_metrics = {
        "page_media_view": "day_views",
        "page_total_media_view_unique": "day_unique_viewers",
        "page_post_engagements": "day_interactions",
    }
    for metric, key in day_metrics.items():
        values[key] = metric_value(metric, "day")

    period_metrics = {
        "page_media_view": "views_28d",
        "page_total_media_view_unique": "unique_viewers_28d",
        "page_post_engagements": "interactions_28d",
    }
    for metric, key in period_metrics.items():
        values[key] = metric_value(metric, "days_28")

    insights_available = any(
        values[key] is not None
        for key in (
            "day_views", "day_unique_viewers", "day_interactions",
            "views_28d", "unique_viewers_28d", "interactions_28d",
        )
    )
    if not insights_available:
        print(
            f"[FACEBOOK INSIGHTS] Insights indisponíveis para a página {page_id}; "
            f"followers_count={values['followers']!r} será preservado sem declarar métricas sincronizadas."
        )

    conn = None
    try:
        conn = get_connection()
        report_date = datetime.now(timezone.utc).date()
        day_rate = (
            round(values["day_interactions"] / values["day_unique_viewers"] * 100, 2)
            if values["day_interactions"] is not None
            and values["day_unique_viewers"] is not None
            and values["day_unique_viewers"] > 0
            else None
        )
        with conn.cursor() as cur:
            # Uma linha por conta e dia; não escrever totais de 28 dias no histórico diário.
            cur.execute(
                """INSERT INTO daily_metrics
                       (social_account_id, report_date, followers, impressions, interactions_count,
                        reach, engagement_rate, profile_views, website_clicks)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL)
                   ON CONFLICT (social_account_id, report_date) DO UPDATE SET
                       followers = COALESCE(EXCLUDED.followers, daily_metrics.followers),
                       impressions = COALESCE(EXCLUDED.impressions, daily_metrics.impressions),
                       interactions_count = COALESCE(EXCLUDED.interactions_count, daily_metrics.interactions_count),
                       reach = COALESCE(EXCLUDED.reach, daily_metrics.reach),
                       engagement_rate = COALESCE(EXCLUDED.engagement_rate, daily_metrics.engagement_rate),
                       created_at = NOW()""",
                (
                    social_account_id, report_date, values["followers"], values["day_views"],
                    values["day_interactions"], values["day_unique_viewers"], day_rate,
                ),
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS facebook_period_snapshots (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    social_account_id UUID NOT NULL REFERENCES social_accounts(id) ON DELETE CASCADE,
                    period_days INTEGER NOT NULL DEFAULT 28,
                    snapshot_date DATE NOT NULL,
                    views BIGINT,
                    unique_viewers BIGINT,
                    interactions BIGINT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE (social_account_id, period_days, snapshot_date)
                )
            """)
            cur.execute(
                """INSERT INTO facebook_period_snapshots
                       (social_account_id, period_days, snapshot_date, views, unique_viewers, interactions)
                   VALUES (%s, 28, %s, %s, %s, %s)
                   ON CONFLICT (social_account_id, period_days, snapshot_date) DO UPDATE SET
                       views = COALESCE(EXCLUDED.views, facebook_period_snapshots.views),
                       unique_viewers = COALESCE(EXCLUDED.unique_viewers, facebook_period_snapshots.unique_viewers),
                       interactions = COALESCE(EXCLUDED.interactions, facebook_period_snapshots.interactions),
                       created_at = NOW()""",
                (
                    social_account_id, report_date, values["views_28d"],
                    values["unique_viewers_28d"], values["interactions_28d"],
                ),
            )
            cur.execute(
                "UPDATE social_accounts SET last_sync_at=NOW() WHERE id=%s",
                (social_account_id,),
            )
        conn.commit()
        print(
            "[FACEBOOK INSIGHTS] Sincronização concluída: "
            f"followers={values['followers']!r}, day_views={values['day_views']!r}, "
            f"day_unique_viewers={values['day_unique_viewers']!r}, "
            f"day_interactions={values['day_interactions']!r}, "
            f"views_28d={values['views_28d']!r}, "
            f"unique_viewers_28d={values['unique_viewers_28d']!r}, "
            f"interactions_28d={values['interactions_28d']!r}."
        )
        return insights_available
    except Exception as erro:
        if conn:
            conn.rollback()
        print(f"[FACEBOOK INSIGHTS ERROR] {type(erro).__name__}: {erro}")
        return False
    finally:
        if conn:
            conn.close()


def _read_instagram_insight(ig_id, access_token, metric, metric_type="total_value", period=None, since=None, until=None, graph_host="graph.instagram.com"):
    """Consulta Insights Instagram; permite Instagram Login ou Facebook Login."""
    api_version = os.getenv("FACEBOOK_GRAPH_API_VERSION" if graph_host == "graph.facebook.com" else "INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
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
            f"https://{graph_host}/{api_version}/{ig_id}/insights",
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


def _sync_instagram_metrics(social_account_id, ig_id, access_token, graph_host="graph.instagram.com"):
    """Guarda métricas Instagram via Instagram Login ou Facebook Login."""
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
    # Compatibilidade com Instagram Insights: reach aceita série temporal;
    # views e total_interactions devem ser consultadas como valores agregados.
    reach_value = _read_instagram_insight(
        ig_id, access_token, "reach", metric_type="time_series", period="day", graph_host=graph_host
    )
    views_value = _read_instagram_insight(
        ig_id, access_token, "views", metric_type="total_value", period="day", graph_host=graph_host
    )
    interactions_value = _read_instagram_insight(
        ig_id, access_token, "total_interactions",
        metric_type="total_value", period="day", graph_host=graph_host
    )

    # Totais oficiais da janela móvel de 28 dias. A API rejeitou
    # total_over_range para estas métricas; consultar period=day com since/until.
    # Não somar reach diário, pois isso duplicaria pessoas.
    reach_28d = _read_instagram_insight(
        ig_id, access_token, "reach", metric_type="total_value", period="day",
        since=since_28d, until=until_28d, graph_host=graph_host,
    )
    views_28d = _read_instagram_insight(
        ig_id, access_token, "views", metric_type="total_value", period="day",
        since=since_28d, until=until_28d, graph_host=graph_host,
    )
    interactions_28d = _read_instagram_insight(
        ig_id, access_token, "total_interactions",
        metric_type="total_value", period="day",
        since=since_28d, until=until_28d, graph_host=graph_host,
    )
    if interactions_value is None:
        # Compatibilidade: tenta compor interações a partir das métricas
        # oficiais individuais. Só calcula a soma se TODAS estiverem disponíveis;
        # nunca trata uma métrica em falta como zero.
        interaction_parts = {
            metric: _read_instagram_insight(
                ig_id, access_token, metric, metric_type="total_value", graph_host=graph_host
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
    api_version = os.getenv("FACEBOOK_GRAPH_API_VERSION" if graph_host == "graph.facebook.com" else "INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
    try:
        profile_response = requests.get(
            f"https://{graph_host}/{api_version}/{ig_id}",
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
    if graph_host == "graph.instagram.com":
        _sync_instagram_period_snapshots(social_account_id, ig_id, access_token)
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
.brand-logo{display:block;width:104px;height:104px;object-fit:cover;object-position:center;margin:0 auto 10px;padding:0;border-radius:16px;background:#fff}.brand-slogan{display:block;max-width:360px;margin:0 auto 22px;text-align:center;font-size:14px;line-height:1.45;font-weight:700;color:#38bdf8}
h1{margin:0 0 8px;text-align:center}p{color:#94a3b8;text-align:center;margin-bottom:24px}
label{display:block;margin:14px 0 7px;font-weight:600}input{width:100%;padding:13px;border-radius:10px;border:1px solid #334155;background:#020617;color:#fff;outline:none}
input:focus{border-color:#38bdf8}button{width:100%;margin-top:22px;padding:13px;border:0;border-radius:10px;background:#38bdf8;color:#020617;font-weight:700;cursor:pointer}
.error{margin-top:16px;padding:10px;border-radius:8px;background:rgba(239,68,68,.12);color:#fca5a5;text-align:center}
.link{display:block;text-align:center;margin-top:18px;color:#38bdf8;text-decoration:none}.footer{margin-top:22px;font-size:12px;color:#64748b;text-align:center}
</style></head><body><main class="card">
<img class="brand-logo" src="/logo.png" alt="Símbolo PMW Consultoria">
<p class="brand-slogan">Transforme os Seus Dados em Oportunidades de Negócio</p>
<h1>🚀 Criar conta</h1><p>Comece o seu trial gratuito de 30 dias</p>
<form method="post" action="/signup">
<label for="name">Nome / Empresa</label><input id="name" name="name" type="text" autocomplete="name" required>
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required>
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="8" required>
<label for="confirm_password">Confirmar password</label><input id="confirm_password" name="confirm_password" type="password" autocomplete="new-password" minlength="8" required>
<button type="submit">Continuar → Criar conta</button></form>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<a class="link" href="/login">Já tenho uma conta — Continuar para entrar</a>
<a class="link" href="/home" style="margin-top:10px;color:#cbd5e1">← Voltar à página inicial</a>
<div class="footer">PMW Consultoria & Tecnologia</div>
</main></body></html>
"""

LOGIN_PAGE = """
<!doctype html>
<html lang="pt">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Entrar — PMW Dashboard AI</title>
    <meta name="robots" content="noindex, nofollow, noarchive">
    <meta name="theme-color" content="#07111f">
    <style>
        :root {
            color-scheme: dark;
            --bg: #07111f;
            --panel: #101d30;
            --line: #263b54;
            --text: #f4f8ff;
            --muted: #a6b6ca;
            --blue: #38bdf8;
            --danger-bg: rgba(248, 113, 113, .12);
            --danger: #fecaca;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0; min-height: 100vh; color: var(--text);
            background: radial-gradient(ellipse at 50% 0%, rgba(56,189,248,.12), transparent 42%), var(--bg);
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
            line-height: 1.5; display: flex; flex-direction: column;
        }
        .topbar {
            width: 100%; border-bottom: 1px solid rgba(148,163,184,.14);
            background: rgba(7,17,31,.82);
        }
        .topbar-inner {
            width: min(1120px, calc(100% - 40px)); min-height: 92px; margin: 0 auto;
            display: flex; align-items: center; justify-content: space-between; gap: 24px;
        }
        .brand { display: flex; align-items: center; gap: 13px; min-width: 0; text-decoration: none; }
        .brand-mark {
            width: 58px; height: 58px; flex: 0 0 58px; object-fit: cover; object-position: center;
            border-radius: 10px; background: #fff;
        }
        .brand-copy { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
        .brand-name { font-size: 16px; font-weight: 800; letter-spacing: -.02em; line-height: 1.25; }
        .brand-sub { color: var(--muted); font-size: 11px; font-weight: 600; }
        .brand-slogan { color: var(--blue); font-size: 11px; font-weight: 650; line-height: 1.35; }
        .language-control { display:flex; align-items:center; gap:7px; color:var(--muted); font-size:12px; font-weight:700; }
        .language-control select { min-height:40px; padding:8px 10px; border:1px solid #354960; border-radius:9px; background:#081423; color:var(--text); font:inherit; cursor:pointer; }
        .top-navigation { display:flex; align-items:center; justify-content:flex-end; gap:10px; flex-wrap:wrap; }
        .nav-link { display:inline-flex; align-items:center; justify-content:center; min-height:40px; padding:9px 13px; border:1px solid #354960; border-radius:9px; color:var(--text); text-decoration:none; font-size:13px; font-weight:750; }
        .nav-primary { background:var(--blue); border-color:var(--blue); color:#04121e; }
        .nav-link:hover { filter:brightness(1.08); }
        .nav-link:focus-visible { outline:3px solid #7dd3fc; outline-offset:3px; }
        main { flex: 1; display: grid; place-items: center; padding: 48px 20px 56px; }
        .login-wrap { width: 100%; max-width: 430px; }
        .login-card {
            padding: clamp(26px, 5vw, 38px); border: 1px solid rgba(148,163,184,.2);
            border-radius: 18px; background: linear-gradient(160deg, rgba(19,36,58,.98), rgba(12,25,42,.98));
            box-shadow: 0 24px 70px rgba(0,0,0,.28);
        }
        .form-kicker { margin: 0 0 7px; color: var(--blue); font-size: 11px; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }
        h1 { margin: 0; font-size: clamp(26px, 4vw, 31px); line-height: 1.2; letter-spacing: -.035em; }
        .form-intro { margin: 10px 0 26px; color: var(--muted); font-size: 14px; }
        .field { margin-top: 18px; }
        label { display: block; margin-bottom: 8px; color: #e6eef9; font-size: 13px; font-weight: 700; }
        input {
            display: block; width: 100%; min-height: 49px; padding: 13px 14px;
            border: 1px solid #354960; border-radius: 10px; outline: none;
            background: #081423; color: var(--text); font: inherit; font-size: 14px;
        }
        input::placeholder { color: #71849b; }
        input:focus { border-color: var(--blue); box-shadow: 0 0 0 3px rgba(56,189,248,.13); }
        .submit {
            width: 100%; min-height: 49px; display: inline-flex; justify-content: center; align-items: center; gap: 9px;
            margin-top: 25px; padding: 13px 16px; border: 0; border-radius: 10px;
            background: linear-gradient(100deg, #38bdf8, #8ce7ff); color: #04121e;
            font: inherit; font-size: 14px; font-weight: 800; cursor: pointer;
        }
        .submit:hover { filter: brightness(1.04); }
        .signup { margin-top: 22px; text-align: center; color: var(--muted); font-size: 13px; }
        @media (max-width: 640px) {
            .topbar-inner { width: calc(100% - 28px); padding:12px 0; align-items:flex-start; flex-direction:column; gap:12px; }
            .top-navigation { width:100%; justify-content:stretch; }
            .top-navigation .nav-link { flex:1 1 auto; text-align:center; }
        }
        .signup a { color: #a7edff; font-weight: 800; text-decoration: none; }
        .signup a:hover { text-decoration: underline; }
        .security-note { display: flex; justify-content: center; align-items: center; gap: 7px; margin-top: 22px; color: #93a6bc; font-size: 11px; text-align: center; }
        .login-content { width: 100%; max-width: 760px; }
        .value-section { margin-top: 26px; }
        .value-heading { margin: 0 0 7px; font-size: 19px; font-weight: 800; letter-spacing: -.025em; text-align: center; }
        .value-lead { max-width: 620px; margin: 0 auto 18px; color: var(--muted); font-size: 13px; text-align: center; }
        .feature-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
        .feature-card { min-width: 0; padding: 16px; border: 1px solid rgba(148,163,184,.18); border-radius: 13px; background: rgba(16,29,48,.72); }
        .feature-icon { display: grid; place-items: center; width: 34px; height: 34px; margin-bottom: 11px; border: 1px solid var(--line); border-radius: 10px; background: rgba(56,189,248,.08); color: var(--blue); font-size: 18px; }
        .feature-title { margin: 0 0 5px; color: var(--text); font-size: 13px; font-weight: 800; }
        .feature-copy { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
        @media (max-width: 620px) {
            .feature-grid { grid-template-columns: 1fr; gap: 9px; }
            .feature-card { display: grid; grid-template-columns: 34px minmax(0,1fr); column-gap: 12px; align-items: start; padding: 13px; }
            .feature-icon { grid-row: span 2; margin-bottom: 0; }
            .feature-title { margin-top: 1px; }
        }
        .error {
            margin-top: 18px; padding: 12px 13px; border: 1px solid rgba(248,113,113,.25);
            border-radius: 10px; background: var(--danger-bg); color: var(--danger); font-size: 13px; overflow-wrap: anywhere;
        }
        footer { padding: 0 20px 24px; color: #71849b; font-size: 11px; text-align: center; }
        @media (max-width: 560px) {
            .topbar-inner { width: calc(100% - 28px); min-height: 82px; gap: 12px; }
            .brand-mark { width: 48px; height: 48px; flex-basis: 48px; }
            .brand-name { font-size: 14px; }
            .brand-sub { font-size: 10px; }
            .brand-slogan { max-width: 230px; font-size: 10px; }
            .top-label { display: none; }
            main { padding: 32px 16px 40px; }
            .login-card { border-radius: 15px; }
        }
        @media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; scroll-behavior: auto !important; } }
    </style>
</head>
<body>
    <header class="topbar">
        <div class="topbar-inner">
            <a class="brand" href="/login" aria-label="PMW Dashboard AI">
                <img class="brand-mark" src="/logo.png" alt="Logótipo PMW Consultoria">
                <span class="brand-copy">
                    <span class="brand-name">PMW Dashboard AI</span>
                    <span class="brand-sub">Analytics · Métricas · IA</span>
                    <span class="brand-slogan" data-i18n="slogan">Transforme os Seus Dados em Oportunidades de Negócio</span>
                </span>
            </a>
            <nav class="top-navigation" aria-label="Navegação de acesso">
                <label class="language-control" for="login-language"><span data-i18n="languageLabel">Idioma / Language</span>
                    <select id="login-language" aria-label="Idioma / Language"><option value="pt">Português</option><option value="en">English</option></select>
                </label>
                <a href="/home" class="nav-link" data-i18n="backHome">← Voltar à página inicial</a>
                <a href="/signup" class="nav-link nav-primary" data-i18n="createAccount">Criar conta / Continuar</a>
            </nav>
        </div>
    </header>
    <main>
        <div class="login-content">
        <section class="login-wrap" aria-labelledby="login-title">
            <div class="login-card">
                <p class="form-kicker" data-i18n="welcome">Bem-vindo de volta</p>
                <h1 id="login-title" data-i18n="title">Entrar na plataforma</h1>
                <p class="form-intro" data-i18n="intro">Introduza as suas credenciais para aceder ao seu dashboard.</p>
                <form method="post" action="/login">
                    <div class="field">
                        <label for="username" data-i18n="usernameLabel">Email ou nome de utilizador</label>
                        <input id="username" name="username" type="text" autocomplete="username"
                               placeholder="O seu email ou utilizador" data-i18n-placeholder="usernamePlaceholder" required autofocus>
                    </div>
                    <div class="field">
                        <label for="password" data-i18n="passwordLabel">Palavra-passe</label>
                        <input id="password" name="password" type="password" autocomplete="current-password"
                               placeholder="Introduza a sua palavra-passe" data-i18n-placeholder="passwordPlaceholder" required>
                    </div>
                    <button class="submit" type="submit"><span data-i18n="submit">Entrar com segurança</span> <span aria-hidden="true">→</span></button>
                </form>
                {% if error %}
                <div class="error" role="alert">{{ error }}</div>
                {% endif %}
                <div class="signup"><span data-i18n="signupPrompt">Ainda não tem conta?</span> <a href="/signup" data-i18n="signupLink">Experimente gratuitamente durante 30 dias</a>.</div>
                <div class="security-note"><span aria-hidden="true">🔒</span> <span data-i18n="security">Acesso reservado a utilizadores autenticados</span></div>
            </div>
        </section>
        <section class="value-section" aria-label="Vantagens do PMW Dashboard AI">
            <h2 class="value-heading" data-i18n="valueHeading">Acompanhe as suas redes sociais. Decida com inteligência.</h2>
            <p class="value-lead" data-i18n="valueLead">Transforme métricas em decisões estratégicas. Analise o desempenho, acompanhe tendências e descubra oportunidades para impulsionar os resultados do seu negócio.</p>
            <div class="feature-grid">
                <article class="feature-card">
                    <span class="feature-icon" aria-hidden="true">↗</span>
                    <h3 class="feature-title" data-i18n="feature1Title">Métricas organizadas</h3>
                    <p class="feature-copy" data-i18n="feature1Copy">Acompanhe seguidores, alcance e envolvimento num só espaço.</p>
                </article>
                <article class="feature-card">
                    <span class="feature-icon" aria-hidden="true">✦</span>
                    <h3 class="feature-title" data-i18n="feature2Title">Inteligência artificial</h3>
                    <p class="feature-copy" data-i18n="feature2Copy">Consulte alertas, recomendações e relatórios com base nos dados disponíveis.</p>
                </article>
                <article class="feature-card">
                    <span class="feature-icon" aria-hidden="true">⌁</span>
                    <h3 class="feature-title" data-i18n="feature3Title">Área de trabalho protegida</h3>
                    <p class="feature-copy" data-i18n="feature3Copy">Aceda ao dashboard e aos dados autorizados através da sua conta.</p>
                </article>
            </div>
        </section>
        </div>
    </main>
    <footer>© 2026 PMW Consultoria &amp; Tecnologia</footer>
    <script nonce="{{ csp_nonce }}">
    (function () {
        const translations = {
            pt: { languageLabel:"Idioma / Language", backHome:"← Voltar à página inicial", createAccount:"Criar conta / Continuar", slogan:"Transforme os Seus Dados em Oportunidades de Negócio", welcome:"Bem-vindo de volta", title:"Entrar na plataforma", intro:"Introduza as suas credenciais para aceder ao seu dashboard.", usernameLabel:"Email ou nome de utilizador", usernamePlaceholder:"O seu email ou utilizador", passwordLabel:"Palavra-passe", passwordPlaceholder:"Introduza a sua palavra-passe", submit:"Entrar com segurança", signupPrompt:"Ainda não tem conta?", signupLink:"Experimente gratuitamente durante 30 dias", security:"Acesso reservado a utilizadores autenticados", valueHeading:"Acompanhe as suas redes sociais. Decida com inteligência.", valueLead:"Transforme métricas em decisões estratégicas. Analise o desempenho, acompanhe tendências e descubra oportunidades para impulsionar os resultados do seu negócio.", feature1Title:"Métricas organizadas", feature1Copy:"Acompanhe seguidores, alcance e envolvimento num só espaço.", feature2Title:"Inteligência artificial", feature2Copy:"Consulte alertas, recomendações e relatórios com base nos dados disponíveis.", feature3Title:"Área de trabalho protegida", feature3Copy:"Aceda ao dashboard e aos dados autorizados através da sua conta." },
            en: { languageLabel:"Language / Idioma", backHome:"← Back to home page", createAccount:"Create account / Continue", slogan:"Turn Your Data into Business Opportunities", welcome:"Welcome back", title:"Sign in to the platform", intro:"Enter your credentials to access your dashboard.", usernameLabel:"Email or username", usernamePlaceholder:"Your email or username", passwordLabel:"Password", passwordPlaceholder:"Enter your password", submit:"Sign in securely", signupPrompt:"Don’t have an account yet?", signupLink:"Try it free for 30 days", security:"Access reserved for authenticated users", valueHeading:"Track your social media. Make smarter decisions.", valueLead:"Turn metrics into strategic decisions. Analyse performance, follow trends, and discover opportunities to improve your business results.", feature1Title:"Organised metrics", feature1Copy:"Track followers, reach, and engagement in one place.", feature2Title:"Artificial intelligence", feature2Copy:"Review alerts, recommendations, and reports based on available data.", feature3Title:"Protected workspace", feature3Copy:"Access your dashboard and authorised data through your account." }
        };
        const selector = document.getElementById("login-language");
        function setLanguage(language) {
            const lang = translations[language] ? language : "pt";
            document.documentElement.lang = lang;
            document.querySelectorAll("[data-i18n]").forEach(function (element) {
                const key = element.getAttribute("data-i18n");
                if (translations[lang][key]) element.textContent = translations[lang][key];
            });
            document.querySelectorAll("[data-i18n-placeholder]").forEach(function (element) {
                const key = element.getAttribute("data-i18n-placeholder");
                if (translations[lang][key]) element.placeholder = translations[lang][key];
            });
            const error = document.querySelector(".error");
            if (error && lang === "en") {
                const messages = { "Email/username ou password inválido.":"Incorrect email/username or password." };
                if (messages[error.textContent.trim()]) error.textContent = messages[error.textContent.trim()];
            }
            try { localStorage.setItem("pmw-login-language", lang); } catch (e) {}
        }
        let saved = "pt";
        try { saved = localStorage.getItem("pmw-login-language") || "pt"; } catch (e) {}
        selector.value = saved;
        selector.addEventListener("change", function () { setLanguage(selector.value); });
        setLanguage(selector.value);
    })();
    </script>
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
<div class="card"><a class="btn" href="/">← Dashboard</a> <a class="btn" href="/account#social-connections" style="background:#a855f7;color:#fff">🔗 Conectar Redes Sociais</a> <a class="btn" href="/billing">💳 Plano</a> <a class="btn danger" href="/logout">Sair</a><h1>👤 Minha Conta</h1><p class="muted">Gerencie o seu perfil, password e contas sociais.</p><form method="post" action="/social-accounts/sync" style="display:inline-block;margin:8px 0 0"><button type="submit" style="background:#22c55e;color:#052e16">🔄 Sincronizar métricas agora</button></form>{% if message %}<p class="ok">{{ message }}</p>{% endif %}{% if error %}<p class="err">{{ error }}</p>{% endif %}</div><div id="social-connections" class="card" style="border:2px solid #38bdf8;background:linear-gradient(135deg,#0f172a,#082f49)"><h2 style="margin-top:0">🔗 Conectar Redes Sociais</h2><p class="muted">Ligue as suas redes sociais através da autenticação oficial. O cliente será redirecionado para cada plataforma, fará login e autorizará o acesso. Nunca introduza password, token, App ID ou App Secret neste dashboard.</p><div class="grid"><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>📷 Instagram</h3><p class="muted">Instagram Business / Creator</p><a class="btn" href="/oauth/instagram">📷 Conectar Instagram</a></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>📘 Facebook</h3><p class="muted">Facebook Pages</p><a class="btn" href="/oauth/facebook">📘 Conectar Facebook</a></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>💬 WhatsApp</h3><p class="muted">WhatsApp Business Platform</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>🎵 TikTok</h3><p class="muted">TikTok for Business</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>▶️ YouTube</h3><p class="muted">YouTube / Google</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>💼 LinkedIn</h3><p class="muted">LinkedIn Pages</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div><div style="background:#0f172a;border:1px solid #334155;border-radius:14px;padding:18px"><h3>🐦 X</h3><p class="muted">X / Twitter</p><button class="btn" disabled style="opacity:.55;cursor:not-allowed">Em breve</button></div></div></div>
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
                + (
                    "script-src 'nonce-" + getattr(g, "csp_nonce", "") + "'; "
                    if request.path == "/login"
                    else "script-src 'none'; "
                )
                + "style-src 'self' 'unsafe-inline'; "
                + "img-src 'self' data:; "
                + "font-src 'self' data:"
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
        # Permite apenas o script de tradução desta resposta, usando nonce CSP.
        g.csp_nonce = secrets.token_urlsafe(18)
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

        return render_template_string(LOGIN_PAGE, error=error, csp_nonce=g.csp_nonce)

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
                       WHERE workspace_id=%s AND COALESCE(is_active, TRUE)=TRUE
                         AND LOWER(COALESCE(connection_status, '')) <> 'linked'""",
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


    @server.get("/oauth/facebook")
    def facebook_oauth():
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        if not _facebook_oauth_configured():
            return redirect(url_for(
                "account",
                error="Facebook OAuth ainda não está configurado. Configure FACEBOOK_OAUTH_REDIRECT_URI no Render."
            ))
        try:
            # Use a signed, time-limited state so validation does not depend on
            # the session cookie surviving the round trip through Facebook.
            from itsdangerous import URLSafeTimedSerializer
            serializer = URLSafeTimedSerializer(server.secret_key, salt="pmw-facebook-oauth-state-v1")
            state = serializer.dumps({
                "nonce": secrets.token_urlsafe(32),
                "workspace_slug": session.get("workspace_slug"),
            })
            return redirect(_facebook_authorize_url(state))
        except Exception as erro:
            print(f"[FACEBOOK OAUTH START ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível iniciar a ligação ao Facebook."))

    @server.get("/oauth/facebook/callback")
    def facebook_oauth_callback():
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        if request.args.get("error"):
            return redirect(url_for("account", error="A autorização do Facebook foi cancelada ou recusada."))

        state = request.args.get("state", "")
        try:
            from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
            serializer = URLSafeTimedSerializer(server.secret_key, salt="pmw-facebook-oauth-state-v1")
            state_data = serializer.loads(state, max_age=600)
        except Exception as erro:
            # Do not log the state value; it is a temporary security token.
            print(f"[FACEBOOK OAUTH STATE INVALID] {type(erro).__name__}")
            return redirect(url_for("account", error="A sessão de autorização do Facebook expirou ou não pôde ser validada. Inicie a ligação novamente."))
        workspace_slug = state_data.get("workspace_slug")
        if not state_data.get("nonce") or workspace_slug != session.get("workspace_slug"):
            return redirect(url_for("account", error="A sessão de autorização do Facebook não corresponde à conta atual. Inicie a ligação novamente."))
        code = request.args.get("code", "")
        if not code:
            return redirect(url_for("account", error="Código OAuth inválido ou sessão expirada."))

        app_id = os.getenv("FACEBOOK_APP_ID", "").strip() or os.getenv("INSTAGRAM_APP_ID", "").strip()
        app_secret = os.getenv("FACEBOOK_APP_SECRET", "").strip() or os.getenv("INSTAGRAM_APP_SECRET", "").strip()
        redirect_uri = os.getenv("FACEBOOK_OAUTH_REDIRECT_URI", "").strip()
        try:
            token_response = requests.get(
                "https://graph.facebook.com/v25.0/oauth/access_token",
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
                timeout=15,
            )
            token_response.raise_for_status()
            user_token = token_response.json().get("access_token")
            if not user_token:
                raise RuntimeError("A Meta não devolveu um token de acesso.")

            long_response = requests.get(
                "https://graph.facebook.com/v25.0/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "fb_exchange_token": user_token,
                },
                timeout=15,
            )
            long_response.raise_for_status()
            user_token = long_response.json().get("access_token", user_token)

            # Diagnóstico seguro: registar apenas nomes/estados das permissões,
            # nunca tokens, códigos OAuth ou conteúdo sensível da resposta.
            try:
                permissions_response = requests.get(
                    "https://graph.facebook.com/v25.0/me/permissions",
                    params={"access_token": user_token},
                    timeout=15,
                )
                if permissions_response.ok:
                    permission_items = permissions_response.json().get("data", [])
                    permission_statuses = {
                        str(item.get("permission", "")): str(item.get("status", ""))
                        for item in permission_items
                        if isinstance(item, dict) and item.get("permission")
                    }
                    print(f"[FACEBOOK OAUTH DIAGNOSTIC] granted_permissions={permission_statuses!r}.")
                else:
                    print(
                        f"[FACEBOOK OAUTH DIAGNOSTIC] /me/permissions HTTP={permissions_response.status_code}; "
                        "não foi possível confirmar permissões concedidas."
                    )
            except (requests.RequestException, ValueError, AttributeError, TypeError) as permission_error:
                print(
                    f"[FACEBOOK OAUTH DIAGNOSTIC] /me/permissions falhou: "
                    f"{type(permission_error).__name__}."
                )

            pages_response = requests.get(
                "https://graph.facebook.com/v25.0/me/accounts",
                params={
                    "fields": "id,name,access_token,tasks",
                    "limit": 100,
                    "access_token": user_token,
                },
                timeout=15,
            )
            pages_response.raise_for_status()
            pages = pages_response.json().get("data", [])
            if not isinstance(pages, list) or not pages:
                return redirect(url_for(
                    "account",
                    error="A Meta não devolveu Páginas geridas por esta conta. Confirme o acesso às Páginas e as permissões concedidas."
                ))

            selector = secrets.token_urlsafe(32)
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS facebook_oauth_pending_pages (
                            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            selector TEXT NOT NULL,
                            workspace_slug VARCHAR(255) NOT NULL,
                            page_id VARCHAR(255) NOT NULL,
                            page_name VARCHAR(255) NOT NULL,
                            encrypted_page_token TEXT NOT NULL,
                            expires_at TIMESTAMP NOT NULL,
                            created_at TIMESTAMP DEFAULT NOW(),
                            UNIQUE (selector, workspace_slug, page_id)
                        );
                    """)
                    cur.execute(
                        "DELETE FROM facebook_oauth_pending_pages WHERE expires_at < NOW() OR workspace_slug=%s",
                        (workspace_slug,),
                    )
                    saved = 0
                    for page in pages:
                        page_id = str(page.get("id", "")).strip()
                        page_name = str(page.get("name", "")).strip()
                        page_tasks = page.get("tasks") or []
                        safe_tasks = [
                            str(task.get("name", "")) if isinstance(task, dict) else str(task)
                            for task in page_tasks
                        ]
                        print(
                            f"[FACEBOOK PAGE DIAGNOSTIC] page_id={page_id!r} "
                            f"tasks={safe_tasks!r} analyze_access={'ANALYZE' in safe_tasks}."
                        )
                        page_token = page.get("access_token")
                        if not page_id or not page_name or not page_token:
                            continue
                        cur.execute(
                            """INSERT INTO facebook_oauth_pending_pages
                               (selector, workspace_slug, page_id, page_name, encrypted_page_token, expires_at)
                               VALUES (%s, %s, %s, %s, %s, NOW() + INTERVAL '15 minutes')
                               ON CONFLICT (selector, workspace_slug, page_id) DO UPDATE SET
                                   page_name=EXCLUDED.page_name,
                                   encrypted_page_token=EXCLUDED.encrypted_page_token,
                                   expires_at=EXCLUDED.expires_at""",
                            (selector, workspace_slug, page_id, page_name, _encrypt_token(page_token)),
                        )
                        saved += 1
                conn.commit()
            finally:
                conn.close()

            if saved == 0:
                return redirect(url_for("account", error="Não foi possível obter tokens válidos para as Páginas selecionáveis."))
            return redirect(url_for("facebook_select_page", selector=selector))
        except (requests.RequestException, ValueError, InvalidToken) as erro:
            print(f"[FACEBOOK OAUTH ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível concluir a autorização do Facebook. Verifique as permissões da aplicação Meta."))
        except Exception as erro:
            print(f"[FACEBOOK OAUTH ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Ocorreu um erro ao preparar as Páginas do Facebook."))
    
    @server.route("/oauth/facebook/select", methods=["GET", "POST"])
    def facebook_select_page():
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        selector = request.values.get("selector", "").strip()
        workspace_slug = session.get("workspace_slug")
        if not selector:
            return redirect(url_for("account", error="Seleção de Página inválida. Inicie a ligação novamente."))

        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT page_id, page_name, encrypted_page_token
                       FROM facebook_oauth_pending_pages
                       WHERE selector=%s AND workspace_slug=%s AND expires_at > NOW()
                       ORDER BY page_name""",
                    (selector, workspace_slug),
                )
                pages = cur.fetchall()
                if not pages:
                    return redirect(url_for("account", error="A seleção expirou ou não existem Páginas disponíveis. Ligue o Facebook novamente."))

                if request.method == "GET":
                    options = "".join(
                        '<option value="' + str(page[0]).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;") +
                        '">' + str(page[1]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") +
                        "</option>"
                        for page in pages
                    )
                    html = """<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
                    <title>Selecionar Página — PMW Dashboard AI</title><style>
                    body{margin:0;background:#020617;color:#f8fafc;font-family:Arial,sans-serif;min-height:100vh;display:grid;place-items:center;padding:20px}
                    main{width:100%;max-width:520px;background:#111827;border:1px solid #334155;border-radius:16px;padding:28px;box-sizing:border-box}
                    p{color:#94a3b8;line-height:1.6}label{display:block;margin:18px 0 8px;font-weight:bold}
                    select,button{width:100%;padding:13px;border-radius:9px;font:inherit}
                    select{background:#020617;color:#fff;border:1px solid #475569}
                    button{margin-top:18px;background:#38bdf8;color:#020617;border:0;font-weight:bold;cursor:pointer}
                    a{color:#7dd3fc}</style></head><body><main><h1>Escolher Página do Facebook</h1>
                    <p>Selecione a Página que pretende ligar ao seu workspace PMW. O token será guardado cifrado no servidor.</p>
                    <form method="post" action="/oauth/facebook/select">
                    <input type="hidden" name="selector" value="__SELECTOR__">
                    <label for="page_id">Página do Facebook</label><select id="page_id" name="page_id" required>__OPTIONS__</select>
                    <button type="submit">Conectar Página selecionada</button></form>
                    <p><a href="/account">Cancelar e voltar à conta</a></p></main></body></html>"""
                    html = html.replace("__SELECTOR__", selector.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;"))
                    html = html.replace("__OPTIONS__", options)
                    return html

                page_id = request.form.get("page_id", "").strip()
                selected = next((page for page in pages if str(page[0]) == page_id), None)
                if not selected:
                    return redirect(url_for("account", error="A Página selecionada não é válida."))
                cur.execute(
                    "SELECT id, subscription_plan FROM workspaces WHERE slug=%s LIMIT 1",
                    (workspace_slug,),
                )
                workspace = cur.fetchone()
                if not workspace:
                    return redirect(url_for("account", error="Workspace não encontrado."))
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
                       WHERE workspace_id=%s AND platform='Facebook' AND username=%s LIMIT 1""",
                    (workspace[0], page_id),
                )
                existing = cur.fetchone()
                if not existing and isinstance(account_limit, int) and active_accounts >= account_limit:
                    return redirect(url_for(
                        "account",
                        error=f"O plano {PLANS.get(plan_key, PLANS['trial'])['name']} permite no máximo {account_limit} contas sociais ativas."
                    ))

                encrypted_token = selected[2]
                if existing:
                    social_account_id = existing[0]
                    cur.execute(
                        """UPDATE social_accounts SET access_token=%s, display_name=%s,
                               connection_status='connected', is_active=TRUE, last_sync_at=NOW()
                           WHERE id=%s AND workspace_id=%s""",
                        (encrypted_token, selected[1], social_account_id, workspace[0]),
                    )
                else:
                    cur.execute(
                        """INSERT INTO social_accounts
                           (workspace_id, platform, username, display_name, access_token,
                            connection_status, is_active, last_sync_at)
                           VALUES (%s, 'Facebook', %s, %s, %s, 'connected', TRUE, NOW())
                           RETURNING id""",
                        (workspace[0], page_id, selected[1], encrypted_token),
                    )
                    social_account_id = cur.fetchone()[0]
                cur.execute(
                    "DELETE FROM facebook_oauth_pending_pages WHERE selector=%s AND workspace_slug=%s",
                    (selector, workspace_slug),
                )
                page_token = _token_cipher().decrypt(encrypted_token.encode("utf-8")).decode("utf-8")

                # Descobrir a conta Instagram profissional associada à Página.
                # Guardar a relação separadamente evita alterar ou sobrescrever
                # uma ligação Instagram Login que já esteja em produção.
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS facebook_instagram_links (
                        social_account_id TEXT PRIMARY KEY,
                        page_id VARCHAR(255) NOT NULL,
                        instagram_account_id VARCHAR(255) NOT NULL,
                        instagram_username VARCHAR(255),
                        instagram_social_account_id UUID REFERENCES social_accounts(id) ON DELETE SET NULL,
                        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("""
                    ALTER TABLE facebook_instagram_links
                    ADD COLUMN IF NOT EXISTS instagram_social_account_id UUID
                    REFERENCES social_accounts(id) ON DELETE SET NULL
                """)
                instagram_link_response = requests.get(
                    f"https://graph.facebook.com/v25.0/{page_id}",
                    params={
                        "fields": "instagram_business_account{id,username}",
                        "access_token": page_token,
                    },
                    timeout=15,
                )
                linked_instagram = None
                if instagram_link_response.ok:
                    linked_instagram = (
                        instagram_link_response.json().get("instagram_business_account") or {}
                    )
                else:
                    try:
                        api_error = instagram_link_response.json().get("error", {})
                        print(
                            "[FACEBOOK INSTAGRAM LINK] "
                            f"HTTP={instagram_link_response.status_code} "
                            f"code={api_error.get('code')} "
                            f"motivo={str(api_error.get('message', ''))[:160]}"
                        )
                    except (ValueError, AttributeError):
                        print(
                            f"[FACEBOOK INSTAGRAM LINK] HTTP={instagram_link_response.status_code}; "
                            "não foi possível interpretar a resposta."
                        )
                instagram_social_account_id = None
                if linked_instagram and linked_instagram.get("id"):
                    ig_username = (linked_instagram.get("username") or
                                   "instagram_" + str(linked_instagram["id"]))
                    # Reutilizar apenas a identidade já criada para esta mesma Página
                    # e este mesmo Instagram ligado pelo Facebook Login. Nunca reutilizar
                    # uma conta Instagram Login, mesmo que o username coincida.
                    cur.execute(
                        """SELECT fil.instagram_social_account_id
                           FROM facebook_instagram_links fil
                           JOIN social_accounts ig ON ig.id=fil.instagram_social_account_id
                           WHERE fil.social_account_id=%s AND fil.page_id=%s
                             AND fil.instagram_account_id=%s
                             AND ig.workspace_id=%s AND ig.platform='Instagram'
                             AND LOWER(COALESCE(ig.connection_status, ''))='linked'
                           LIMIT 1""",
                        (str(social_account_id), page_id, str(linked_instagram["id"]), workspace[0]),
                    )
                    linked_row = cur.fetchone()
                    if linked_row and linked_row[0]:
                        instagram_social_account_id = linked_row[0]
                    else:
                        # Identidade complementar da Página: não consome uma vaga
                        # adicional do plano, pois é parte da mesma ligação social.
                        cur.execute(
                            """INSERT INTO social_accounts
                               (workspace_id, platform, username, display_name, access_token,
                                connection_status, is_active, last_sync_at)
                               VALUES (%s, 'Instagram', %s, %s, NULL, 'linked', TRUE, NOW())
                               RETURNING id""",
                            (workspace[0], ig_username, ig_username),
                        )
                        instagram_social_account_id = cur.fetchone()[0]
                    cur.execute(
                        """INSERT INTO facebook_instagram_links
                           (social_account_id, page_id, instagram_account_id,
                            instagram_username, instagram_social_account_id, updated_at)
                           VALUES (%s, %s, %s, %s, %s, NOW())
                           ON CONFLICT (social_account_id) DO UPDATE SET
                               page_id=EXCLUDED.page_id,
                               instagram_account_id=EXCLUDED.instagram_account_id,
                               instagram_username=EXCLUDED.instagram_username,
                               instagram_social_account_id=EXCLUDED.instagram_social_account_id,
                               updated_at=NOW()""",
                        (
                            str(social_account_id), page_id,
                            str(linked_instagram["id"]),
                            ig_username, instagram_social_account_id,
                        ),
                    )
                else:
                    cur.execute(
                        "DELETE FROM facebook_instagram_links WHERE social_account_id=%s",
                        (str(social_account_id),),
                    )
            conn.commit()
            synced = _sync_facebook_metrics(social_account_id, page_id, page_token)
            instagram_synced = False
            if linked_instagram and linked_instagram.get("id") and instagram_social_account_id:
                instagram_synced = _sync_instagram_metrics(
                    instagram_social_account_id,
                    str(linked_instagram["id"]),
                    page_token,
                    graph_host="graph.facebook.com",
                )
            if synced and instagram_synced:
                return redirect(url_for(
                    "account",
                    message="Facebook e conta Instagram profissional associada conectados; métricas disponíveis sincronizadas."
                ))
            if synced and linked_instagram and linked_instagram.get("id"):
                return redirect(url_for(
                    "account",
                    message="Facebook conectado e conta Instagram profissional identificada. A API não disponibilizou métricas Instagram nesta sincronização; consulte os logs e permissões."
                ))
            if synced:
                return redirect(url_for(
                    "account",
                    message="Página do Facebook conectada e métricas disponíveis sincronizadas. Não foi encontrada uma conta Instagram profissional associada."
                ))
            return redirect(url_for("account", message="Página do Facebook conectada. Algumas métricas podem não estar disponíveis; verifique as permissões e os logs."))
        except Exception as erro:
            if conn:
                conn.rollback()
            print(f"[FACEBOOK PAGE SELECT ERROR] {type(erro).__name__}: {erro}")
            return redirect(url_for("account", error="Não foi possível guardar a Página do Facebook. Tente novamente."))
        finally:
            if conn:
                conn.close()


    @server.post("/social-accounts/sync")
    def sync_social_accounts():
        """Sincroniza manualmente as contas ligadas ao workspace autenticado."""
        if not session.get("authenticated"):
            return redirect(url_for("login"))

        workspace_slug = session.get("workspace_slug")
        conn = None
        accounts = []
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT sa.id, sa.platform, sa.username, sa.access_token
                       FROM social_accounts sa
                       JOIN workspaces w ON w.id = sa.workspace_id
                       WHERE w.slug=%s
                         AND sa.is_active=TRUE
                         AND LOWER(COALESCE(sa.connection_status, ''))='connected'
                         AND COALESCE(sa.access_token, '') <> ''
                       ORDER BY sa.created_at ASC""",
                    (workspace_slug,),
                )
                accounts = cur.fetchall()
        except Exception as erro:
            print(f"[SOCIAL SYNC] Não foi possível carregar as contas: {type(erro).__name__}.")
            return redirect(url_for("account", error="Não foi possível carregar as contas para sincronização."))
        finally:
            if conn:
                conn.close()

        if not accounts:
            return redirect(url_for("account", message="Não existem contas sociais ligadas e ativas para sincronizar."))

        success_count = 0
        failed_count = 0
        for account_id, platform, username, encrypted_token in accounts:
            try:
                access_token = _token_cipher().decrypt(
                    encrypted_token.encode("utf-8")
                ).decode("utf-8")
                normalized_platform = (platform or "").strip().lower()

                if normalized_platform == "instagram":
                    api_version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
                    response = requests.get(
                        f"https://graph.instagram.com/{api_version}/me",
                        params={"fields": "id,username", "access_token": access_token},
                        timeout=15,
                    )
                    if not response.ok:
                        print(f"[SOCIAL SYNC] Instagram conta={account_id}: /me HTTP={response.status_code}.")
                        failed_count += 1
                        continue
                    ig_id = response.json().get("id")
                    if not ig_id:
                        print(f"[SOCIAL SYNC] Instagram conta={account_id}: API não devolveu ID.")
                        failed_count += 1
                        continue
                    synced = _sync_instagram_metrics(account_id, str(ig_id), access_token)
                elif normalized_platform == "facebook":
                    # O callback guarda o ID da Página no campo username.
                    synced = _sync_facebook_metrics(account_id, str(username), access_token)
                    # Sincronizar também a conta Instagram profissional associada,
                    # mas sempre no seu próprio social_account_id.
                    link_conn = None
                    try:
                        link_conn = get_connection()
                        with link_conn.cursor() as link_cur:
                            link_cur.execute(
                                """SELECT instagram_social_account_id, instagram_account_id
                                   FROM facebook_instagram_links
                                   WHERE social_account_id=%s
                                   LIMIT 1""",
                                (str(account_id),),
                            )
                            ig_link = link_cur.fetchone()
                        if ig_link and ig_link[0] and ig_link[1]:
                            ig_synced = _sync_instagram_metrics(
                                ig_link[0], str(ig_link[1]), access_token,
                                graph_host="graph.facebook.com",
                            )
                            synced = bool(synced or ig_synced)
                    finally:
                        if link_conn:
                            link_conn.close()
                else:
                    print(f"[SOCIAL SYNC] Plataforma não suportada para sincronização: {platform!r}.")
                    failed_count += 1
                    continue

                if synced:
                    success_count += 1
                else:
                    failed_count += 1
            except (InvalidToken, requests.RequestException, ValueError) as erro:
                print(f"[SOCIAL SYNC] Falha na conta={account_id}: {type(erro).__name__}.")
                failed_count += 1
            except Exception as erro:
                # Diagnóstico PostgreSQL seguro: identifica a tabela/coluna em falta
                # sem registar SQL, parâmetros, tokens ou outros dados sensíveis.
                diag = getattr(erro, "diag", None)
                sqlstate = getattr(erro, "pgcode", None)
                missing_table = getattr(diag, "table_name", None) if diag else None
                missing_schema = getattr(diag, "schema_name", None) if diag else None
                missing_column = getattr(diag, "column_name", None) if diag else None
                print(
                    f"[SOCIAL SYNC DB DIAGNOSTIC] conta={account_id} "
                    f"erro={type(erro).__name__} sqlstate={sqlstate!r} "
                    f"schema={missing_schema!r} tabela={missing_table!r} "
                    f"coluna={missing_column!r}."
                )
                failed_count += 1

        print(f"[SOCIAL SYNC] Resultado workspace={workspace_slug!r}: sucesso={success_count}, falhas={failed_count}.")
        if success_count and not failed_count:
            message = f"Sincronização concluída: {success_count} conta(s) atualizada(s)."
        elif success_count:
            message = f"Sincronização parcial: {success_count} conta(s) atualizada(s), {failed_count} sem dados atualizados. Consulte os logs do Render."
        else:
            message = f"Sincronização executada, mas nenhuma conta devolveu métricas válidas ({failed_count} falha(s)). Consulte os logs do Render."
        return redirect(url_for("account", message=message))


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

        if platform == "Facebook":
            return redirect(url_for("facebook_oauth"))

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
                       WHERE workspace_id=%s AND COALESCE(is_active, TRUE)
                         AND LOWER(COALESCE(connection_status, '')) <> 'linked'""",
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
