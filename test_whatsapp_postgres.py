"""Real PostgreSQL integration tests for WhatsApp status reconciliation."""
import os
import uuid

import psycopg2
import pytest

import whatsapp_webhook


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL não configurada")


@pytest.fixture
def pg_schema(monkeypatch):
    schema = "wa_test_" + uuid.uuid4().hex
    admin = psycopg2.connect(DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'''
            CREATE TABLE "{schema}".workspaces (
                id UUID PRIMARY KEY,
                slug VARCHAR(120) UNIQUE NOT NULL
            )
        ''')
        cur.execute(
            f'INSERT INTO "{schema}".workspaces (id, slug) VALUES (%s, %s)',
            (str(uuid.uuid4()), "workspace-test"),
        )
        cur.execute(f'''
            CREATE TABLE "{schema}".whatsapp_phone_number_workspaces (
                phone_number_id VARCHAR(80) PRIMARY KEY,
                workspace_id UUID NOT NULL REFERENCES "{schema}".workspaces(id)
            )
        ''')
        cur.execute(
            f'''INSERT INTO "{schema}".whatsapp_phone_number_workspaces
                (phone_number_id, workspace_id)
                SELECT 'phone-test', id FROM "{schema}".workspaces
                WHERE slug='workspace-test' '''
        )
    admin.close()

    def connect():
        conn = psycopg2.connect(DSN)
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
        return conn

    monkeypatch.setenv("WHATSAPP_WORKSPACE_SLUG", "workspace-test")
    monkeypatch.setattr(whatsapp_webhook, "get_connection", connect)
    yield connect
    cleanup = psycopg2.connect(DSN)
    cleanup.autocommit = True
    with cleanup.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cleanup.close()


def webhook_payload(status, message_id="wamid.pg-test", timestamp="1790000000"):
    return {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "phone-test"},
                    "statuses": [{
                        "id": message_id,
                        "status": status,
                        "timestamp": timestamp,
                        "recipient_id": "258823002555",
                        **({"errors": [{"code": 131047, "title": "Test failure"}]}
                           if status == "failed" else {}),
                    }],
                },
            }],
        }],
    }


def insert_outbound(connect, message_id):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO whatsapp_messages
                    (workspace_id, message_id, sender_phone, message_type,
                     direction, status)
                SELECT id, %s, '258823002555', 'text', 'outbound', 'sent'
                FROM workspaces WHERE slug='workspace-test'
            """, (message_id,))
        conn.commit()
    finally:
        conn.close()


def fetch_message(connect, message_id):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, delivered_at, read_at, failed_at,
                       failure_code, failure_detail
                FROM whatsapp_messages WHERE message_id=%s
            """, (message_id,))
            return cur.fetchone()
    finally:
        conn.close()


@pytest.mark.parametrize(
    "status,expected",
    [("sent", "sent"), ("delivered", "delivered"),
     ("read", "read"), ("failed", "failed")],
)
def test_postgres_aplica_evento_recebido_antes_do_insert(pg_schema, status, expected):
    message_id = "wamid-" + status
    whatsapp_webhook._save_messages(webhook_payload(status, message_id))
    insert_outbound(pg_schema, message_id)
    row = fetch_message(pg_schema, message_id)
    assert row[0] == expected
    if status == "delivered":
        assert row[1] is not None
    if status == "read":
        assert row[1] is not None and row[2] is not None
    if status == "failed":
        assert row[3] is not None
        assert row[4:] == ("131047", "Test failure")


def test_postgres_eventos_duplicados_e_ordem_nao_regridem_estado(pg_schema):
    message_id = "wamid-order"
    whatsapp_webhook._save_messages(webhook_payload("read", message_id))
    whatsapp_webhook._save_messages(webhook_payload("read", message_id))
    insert_outbound(pg_schema, message_id)

    # A entrega tardia não pode fazer regredir o estado lido.
    whatsapp_webhook._save_messages(webhook_payload("delivered", message_id, "1790000001"))
    assert fetch_message(pg_schema, message_id)[0] == "read"

    # Um evento de leitura mantém-se como estado final quando chega duplicado.
    whatsapp_webhook._save_messages(webhook_payload("read", message_id, "1790000002"))
    assert fetch_message(pg_schema, message_id)[0] == "read"
    conn = pg_schema()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM whatsapp_message_status_events WHERE message_id=%s",
                (message_id,),
            )
            assert cur.fetchone()[0] == 3
    finally:
        conn.close()



def test_postgres_read_prevalece_sobre_failed_independentemente_da_ordem(pg_schema):
    for first, second in (("failed", "read"), ("read", "failed")):
        message_id = f"wamid-{first}-{second}"
        whatsapp_webhook._save_messages(webhook_payload(first, message_id, "1790000001"))
        whatsapp_webhook._save_messages(webhook_payload(second, message_id, "1790000002"))
        insert_outbound(pg_schema, message_id)
        row = fetch_message(pg_schema, message_id)
        assert row[0] == "read"
        assert row[2] is not None


def test_postgres_eventos_concorrentes_preservam_estado_final_read(pg_schema):
    from concurrent.futures import ThreadPoolExecutor

    message_id = "wamid-concurrent"
    payloads = [
        webhook_payload("failed", message_id, "1790000001"),
        webhook_payload("read", message_id, "1790000002"),
    ] * 8

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(whatsapp_webhook._save_messages, payloads))

    insert_outbound(pg_schema, message_id)
    assert fetch_message(pg_schema, message_id)[0] == "read"
