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
    """Obtém apenas contas Instagram ativas com token guardado."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, access_token
                FROM social_accounts
                WHERE LOWER(platform) LIKE '%instagram%'
                  AND COALESCE(is_active, TRUE) = TRUE
                  AND COALESCE(connection_status, 'connected') = 'connected'
                  AND access_token IS NOT NULL
                  AND access_token <> ''
                ORDER BY created_at ASC
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


def sync_account(account_id, username, encrypted_token):
    """Desencripta o token em memória, identifica a conta e sincroniza Insights."""
    try:
        access_token = _token_cipher().decrypt(
            encrypted_token.encode("utf-8")
        ).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        print(
            f"[CRON INSTAGRAM] Conta {username or account_id}: "
            "não foi possível desencriptar o token. Verifique OAUTH_TOKEN_ENCRYPTION_KEY."
        )
        return False

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
                f"[CRON INSTAGRAM] Conta {username or account_id}: "
                f"API de perfil respondeu HTTP {response.status_code}."
            )
            return False
        profile = response.json()
        instagram_id = str(profile.get("id") or "")
        if not instagram_id:
            print(
                f"[CRON INSTAGRAM] Conta {username or account_id}: "
                "a API não devolveu o ID do Instagram."
            )
            return False
    except (requests.RequestException, ValueError) as error:
        print(
            f"[CRON INSTAGRAM] Conta {username or account_id}: "
            f"falha ao consultar perfil ({type(error).__name__})."
        )
        return False

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

    print(f"[CRON INSTAGRAM] Início: {len(accounts)} conta(s) elegível(is).")
    succeeded = 0
    failed = 0

    for account_id, username, encrypted_token in accounts:
        if sync_account(account_id, username, encrypted_token):
            succeeded += 1
        else:
            failed += 1

    print(
        f"[CRON INSTAGRAM] Resultado: {succeeded} sucesso(s), "
        f"{failed} falha(s), de {len(accounts)} conta(s)."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
