"""Sincronização periódica das métricas das contas Instagram ligadas.

Executar como Render Cron Job com o comando:
    python sync_instagram_metrics.py

Requer as mesmas variáveis de ambiente do serviço web, incluindo DATABASE_URL
e OAUTH_TOKEN_ENCRYPTION_KEY. Nunca regista tokens de acesso.
"""
import os
import sys

import requests
from cryptography.fernet import InvalidToken

from auth import _sync_instagram_metrics, _token_cipher
from database import get_connection


def load_connected_instagram_accounts():
    """Obtém contas Instagram ativas, mantendo o workspace para deduplicação."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, workspace_id, username, access_token
                FROM social_accounts
                WHERE LOWER(platform) LIKE '%instagram%'
                  AND COALESCE(is_active, TRUE) = TRUE
                  AND COALESCE(connection_status, 'connected') = 'connected'
                  AND access_token IS NOT NULL
                  AND access_token <> ''
                ORDER BY created_at ASC, id ASC
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_profile_and_token(username, encrypted_token):
    """Desencripta o token e obtém o ID oficial da conta Instagram."""
    try:
        access_token = _token_cipher().decrypt(
            encrypted_token.encode("utf-8")
        ).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        print(
            f"[CRON INSTAGRAM] Conta {username or 'desconhecida'}: "
            "não foi possível desencriptar o token. Verifique OAUTH_TOKEN_ENCRYPTION_KEY."
        )
        return None, None

    api_version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0").strip()
    try:
        response = requests.get(
            f"https://graph.instagram.com/{api_version}/me",
            params={
                "fields": "id,username,account_type",
                "access_token": access_token,
            },
            timeout=20,
        )
        if not response.ok:
            print(
                f"[CRON INSTAGRAM] Conta {username or 'desconhecida'}: "
                f"API de perfil respondeu HTTP {response.status_code}."
            )
            return None, None
        profile = response.json()
        instagram_id = str(profile.get("id") or "")
        if not instagram_id:
            print(
                f"[CRON INSTAGRAM] Conta {username or 'desconhecida'}: "
                "a API não devolveu o ID do Instagram."
            )
            return None, None
        return access_token, instagram_id
    except (requests.RequestException, ValueError) as error:
        print(
            f"[CRON INSTAGRAM] Conta {username or 'desconhecida'}: "
            f"falha ao consultar perfil ({type(error).__name__})."
        )
        return None, None


def sync_account(account_id, username, access_token, instagram_id):
    """Sincroniza Insights de uma conta já identificada pelo ID oficial."""
    if not _sync_instagram_metrics(account_id, instagram_id, access_token):
        print(
            f"[CRON INSTAGRAM] Conta {username or account_id}: "
            "a sincronização não guardou métricas."
        )
        return False

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE social_accounts
                SET last_sync_at = NOW()
                WHERE id = %s
                """,
                (account_id,),
            )
        conn.commit()
    except Exception as error:
        if conn:
            conn.rollback()
        print(
            f"[CRON INSTAGRAM] Conta {username or account_id}: "
            f"métricas guardadas, mas não foi possível atualizar last_sync_at "
            f"({type(error).__name__})."
        )
        return False
    finally:
        if conn:
            conn.close()

    print(f"[CRON INSTAGRAM] Conta {username or account_id}: sincronização concluída.")
    return True


def main():
    try:
        accounts = load_connected_instagram_accounts()
    except Exception as error:
        print(
            f"[CRON INSTAGRAM] Não foi possível consultar as contas "
            f"({type(error).__name__})."
        )
        return 1

    if not accounts:
        print("[CRON INSTAGRAM] Não existem contas Instagram ligadas e ativas para sincronizar.")
        return 0

    print(f"[CRON INSTAGRAM] Início: {len(accounts)} registo(s) elegível(is).")
    succeeded = 0
    failed = 0
    seen_accounts = set()

    for account_id, workspace_id, username, encrypted_token in accounts:
        access_token, instagram_id = get_profile_and_token(username, encrypted_token)
        if not access_token or not instagram_id:
            failed += 1
            continue

        # A mesma conta Instagram pode estar ligada mais de uma vez no mesmo
        # workspace. Deduplicar pelo ID oficial, não pelo username, e manter
        # cada cliente/workspace independente.
        dedupe_key = (str(workspace_id), instagram_id)
        if dedupe_key in seen_accounts:
            print(
                f"[CRON INSTAGRAM] Conta {username or account_id}: "
                "duplicada no mesmo workspace; sincronização ignorada "
                "(o registo e o histórico foram preservados)."
            )
            continue

        seen_accounts.add(dedupe_key)
        if sync_account(account_id, username, access_token, instagram_id):
            succeeded += 1
        else:
            failed += 1

    print(
        f"[CRON INSTAGRAM] Resultado: {succeeded} sucesso(s), "
        f"{failed} falha(s), de {len(accounts)} registo(s) elegível(is)."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
