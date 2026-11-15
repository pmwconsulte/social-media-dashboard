"""Real PostgreSQL integration tests for WhatsApp status reconciliation."""
import os
import uuid

import psycopg2
import pytest

import whatsapp_webhook
import facebook_messenger
import payments


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
            f'''INSERT INTO "{schema}".workspaces (id, slug)
                VALUES (%s, %s)''',
            (str(uuid.uuid4()), "workspace-other"),
        )
        cur.execute(
            f'''INSERT INTO "{schema}".whatsapp_phone_number_workspaces
                (phone_number_id, workspace_id)
                SELECT 'phone-test', id FROM "{schema}".workspaces
                WHERE slug='workspace-test' '''
        )
        cur.execute(
            f'''INSERT INTO "{schema}".whatsapp_phone_number_workspaces
                (phone_number_id, workspace_id)
                SELECT 'phone-other', id FROM "{schema}".workspaces
                WHERE slug='workspace-other' '''
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


def webhook_payload(status, message_id="wamid.pg-test", timestamp="1790000000", phone_id="phone-test"):
    return {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": phone_id},
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



def test_postgres_encaminha_evento_para_workspace_do_phone_number_id(pg_schema):
    message_id = "wamid-other-workspace"
    whatsapp_webhook._save_messages(
        webhook_payload("read", message_id, phone_id="phone-other")
    )
    conn = pg_schema()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT e.workspace_id, w.slug
                FROM whatsapp_message_status_events e
                JOIN workspaces w ON w.id=e.workspace_id
                WHERE e.message_id=%s
            """, (message_id,))
            row = cur.fetchone()
            assert row[1] == "workspace-other"
    finally:
        conn.close()


def test_postgres_rejeita_phone_number_id_sem_mapeamento(pg_schema):
    with pytest.raises(RuntimeError, match="sem workspace associado"):
        whatsapp_webhook._save_messages(
            webhook_payload("read", "wamid-unmapped", phone_id="unknown-phone")
        )


@pytest.fixture
def messenger_pg_schema(monkeypatch):
    schema = "msg_test_" + uuid.uuid4().hex
    workspace_a = str(uuid.uuid4())
    workspace_b = str(uuid.uuid4())
    account_a = str(uuid.uuid4())
    account_b = str(uuid.uuid4())
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
        cur.execute(f'''
            CREATE TABLE "{schema}".social_accounts (
                id UUID PRIMARY KEY,
                workspace_id UUID NOT NULL REFERENCES "{schema}".workspaces(id),
                username VARCHAR(255) NOT NULL,
                platform VARCHAR(40) NOT NULL,
                display_name VARCHAR(255),
                access_token TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                connection_status VARCHAR(40) DEFAULT 'connected',
                last_sync_at TIMESTAMPTZ
            )
        ''')
        cur.execute(
            f'INSERT INTO "{schema}".workspaces (id, slug) VALUES (%s, %s), (%s, %s)',
            (workspace_a, "workspace-a", workspace_b, "workspace-b"),
        )
        cur.execute(
            f'''INSERT INTO "{schema}".social_accounts
                (id, workspace_id, username, platform, display_name, access_token,
                 is_active, connection_status)
                VALUES (%s, %s, 'page-a', 'Facebook', 'Page A', 'encrypted-a', TRUE, 'connected'),
                       (%s, %s, 'page-b', 'Facebook', 'Page B', 'encrypted-b', TRUE, 'connected')''',
            (account_a, workspace_a, account_b, workspace_b),
        )
    admin.close()

    def connect():
        conn = psycopg2.connect(DSN)
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
        return conn

    monkeypatch.setattr(facebook_messenger, "get_connection", connect)
    yield connect, workspace_a, workspace_b, account_a, account_b
    cleanup = psycopg2.connect(DSN)
    cleanup.autocommit = True
    with cleanup.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cleanup.close()


def messenger_payload(page_id, message_id):
    return {
        "object": "page",
        "entry": [{
            "id": page_id,
            "messaging": [{
                "sender": {"id": "psid-customer"},
                "message": {"mid": message_id, "text": "Mensagem de teste"},
                "timestamp": 1790000000000,
            }],
        }],
    }


def test_postgres_messenger_lookup_blocks_account_from_another_workspace(messenger_pg_schema):
    connect, workspace_a, workspace_b, account_a, account_b = messenger_pg_schema
    assert facebook_messenger._workspace_account("workspace-a", account_b) is None
    account = facebook_messenger._workspace_account("workspace-b", account_b)
    assert account is not None
    assert str(account[0]) == account_b
    assert str(account[4]) == workspace_b


def test_postgres_messenger_webhook_routes_page_to_its_workspace(messenger_pg_schema):
    connect, workspace_a, workspace_b, account_a, account_b = messenger_pg_schema
    saved = facebook_messenger._save_webhook(messenger_payload("page-b", "mid-page-b"))
    assert saved == 1
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT w.slug FROM facebook_messenger_messages m
                   JOIN workspaces w ON w.id=m.workspace_id
                   WHERE m.message_id=%s""",
                ("mid-page-b",),
            )
            assert cur.fetchone() == ("workspace-b",)
    finally:
        conn.close()


def test_postgres_messenger_webhook_ignores_ambiguous_page_ownership(messenger_pg_schema):
    connect, workspace_a, workspace_b, account_a, account_b = messenger_pg_schema
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO social_accounts
                   (id, workspace_id, username, platform, display_name, access_token,
                    is_active, connection_status)
                   VALUES (%s, %s, 'page-shared', 'Facebook', 'Shared A', 'encrypted-a', TRUE, 'connected'),
                          (%s, %s, 'page-shared', 'Facebook', 'Shared B', 'encrypted-b', TRUE, 'connected')""",
                (str(uuid.uuid4()), workspace_a, str(uuid.uuid4()), workspace_b),
            )
        conn.commit()
    finally:
        conn.close()

    saved = facebook_messenger._save_webhook(messenger_payload("page-shared", "mid-shared"))
    assert saved == 0
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM facebook_messenger_messages WHERE message_id=%s",
                ("mid-shared",),
            )
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


@pytest.fixture
def billing_pg_schema(monkeypatch):
    schema = "bill_test_" + uuid.uuid4().hex
    workspace_a = str(uuid.uuid4())
    workspace_b = str(uuid.uuid4())
    admin = psycopg2.connect(DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'''
            CREATE TABLE "{schema}".workspaces (
                id UUID PRIMARY KEY,
                slug VARCHAR(120) UNIQUE NOT NULL,
                subscription_id VARCHAR(255),
                subscription_status VARCHAR(40) DEFAULT 'trialing',
                subscription_plan VARCHAR(40) DEFAULT 'trial',
                payment_provider VARCHAR(40)
            )
        ''')
        cur.execute(
            f'INSERT INTO "{schema}".workspaces (id, slug) VALUES (%s, %s), (%s, %s)',
            (workspace_a, "workspace-a", workspace_b, "workspace-b"),
        )
    admin.close()

    def connect():
        conn = psycopg2.connect(DSN)
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
        return conn

    monkeypatch.setattr(payments, "get_connection", connect)
    monkeypatch.setenv("PAYPAL_PLAN_STARTER_ID", "PLAN-STARTER-TEST")
    yield connect
    cleanup = psycopg2.connect(DSN)
    cleanup.autocommit = True
    with cleanup.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cleanup.close()


def test_postgres_paypal_subscription_cannot_be_claimed_by_another_workspace(billing_pg_schema):
    connect = billing_pg_schema
    subscription = {
        "id": "SUB-TENANT-ISOLATION",
        "custom_id": "workspace-a",
        "status": "ACTIVE",
        "plan_id": "PLAN-STARTER-TEST",
    }
    assert payments._set_active(subscription, "workspace-a") is True
    assert payments._set_active(
        {**subscription, "custom_id": "workspace-b"},
        "workspace-b",
    ) is False

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT slug, subscription_id, subscription_status
                   FROM workspaces ORDER BY slug"""
            )
            rows = cur.fetchall()
        assert rows == [
            ("workspace-a", "SUB-TENANT-ISOLATION", "active"),
            ("workspace-b", None, "trialing"),
        ]
    finally:
        conn.close()


def test_postgres_concurrent_paypal_claims_allow_only_one_workspace(billing_pg_schema):
    from concurrent.futures import ThreadPoolExecutor

    claims = [
        {
            "id": "SUB-CONCURRENT-CLAIM",
            "custom_id": "workspace-a",
            "status": "ACTIVE",
            "plan_id": "PLAN-STARTER-TEST",
        },
        {
            "id": "SUB-CONCURRENT-CLAIM",
            "custom_id": "workspace-b",
            "status": "ACTIVE",
            "plan_id": "PLAN-STARTER-TEST",
        },
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda item: payments._set_active(item, item["custom_id"]),
            claims,
        ))
    assert sorted(results) == [False, True]

    conn = billing_pg_schema()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM workspaces WHERE subscription_id=%s",
                ("SUB-CONCURRENT-CLAIM",),
            )
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_postgres_paypal_cancellation_changes_only_owning_workspace(billing_pg_schema):
    connect = billing_pg_schema
    subscription = {
        "id": "SUB-CANCEL-A",
        "custom_id": "workspace-a",
        "status": "ACTIVE",
        "plan_id": "PLAN-STARTER-TEST",
    }
    assert payments._set_active(subscription, "workspace-a") is True
    assert payments._set_inactive(
        {"id": "SUB-CANCEL-A", "status": "CANCELLED", "custom_id": "workspace-a"},
        "BILLING.SUBSCRIPTION.CANCELLED",
    ) is True

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT slug, subscription_status
                   FROM workspaces ORDER BY slug"""
            )
            rows = cur.fetchall()
        assert rows == [
            ("workspace-a", "cancelled"),
            ("workspace-b", "trialing"),
        ]
    finally:
        conn.close()
