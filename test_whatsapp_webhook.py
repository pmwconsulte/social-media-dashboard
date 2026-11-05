"""Tests for WhatsApp delivery status webhook persistence and reconciliation."""
import whatsapp_webhook


class FakeCursor:
    def __init__(self):
        self.statements = []
        self.rowcount = 1
        self._fetchone = ("workspace-id",)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.statements.append((" ".join(query.split()), params))

    def fetchone(self):
        return self._fetchone


class FakeConnection:
    def __init__(self):
        self.fake_cursor = FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _payload(status, timestamp="1790000000", message_id="wamid.test-1", errors=None):
    item = {
        "id": message_id,
        "status": status,
        "timestamp": timestamp,
        "recipient_id": "258823002555",
    }
    if errors is not None:
        item["errors"] = errors
    return {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "phone-test"},
                    "statuses": [item],
                },
            }],
        }],
    }


def _run(monkeypatch, payload):
    conn = FakeConnection()
    monkeypatch.setattr(whatsapp_webhook, "get_connection", lambda: conn)
    saved = whatsapp_webhook._save_messages(payload)
    assert conn.committed
    assert not conn.rolled_back
    assert conn.closed
    return conn, saved


def _queries(conn):
    return [query for query, _ in conn.fake_cursor.statements]


def test_persiste_os_quatro_estados_e_eventos_idempotentes(monkeypatch):
    for status in ("sent", "delivered", "read", "failed"):
        conn, _ = _run(monkeypatch, _payload(status))
        queries = _queries(conn)
        event_insert = next(
            query for query in queries
            if "INSERT INTO whatsapp_message_status_events" in query
        )
        assert "ON CONFLICT (message_id, status, event_timestamp) DO NOTHING" in event_insert

        if status in ("sent", "delivered", "read", "failed"):
            assert any(
                "UPDATE whatsapp_messages" in query
                and ("SET status" in query)
                for query in queries
            )


def test_evento_antecipado_fica_guardado_para_reconciliacao_no_insert(monkeypatch):
    conn, _ = _run(monkeypatch, _payload("read"))
    queries = _queries(conn)

    assert any("INSERT INTO whatsapp_message_status_events" in q for q in queries)
    trigger = next(
        q for q in queries
        if "CREATE OR REPLACE FUNCTION sync_whatsapp_message_status_before_insert" in q
    )
    assert "RETURNS TRIGGER AS $whatsapp$" in trigger
    assert "FROM whatsapp_message_status_events" in trigger
    assert "NEW.status := latest_status" in trigger
    assert "END DESC" in trigger
    assert trigger.index("END DESC") < trigger.index("event_timestamp DESC")
    assert "CREATE TRIGGER trg_sync_whatsapp_message_status" in " ".join(queries)


def test_estado_entregue_nao_regride_de_lido_ou_falhado(monkeypatch):
    conn, _ = _run(monkeypatch, _payload("delivered"))
    update = next(
        q for q in _queries(conn)
        if "UPDATE whatsapp_messages" in q and "SET status='delivered'" in q
    )
    assert "status NOT IN ('read', 'failed')" in update


def test_estado_lido_nao_sobrescreve_falha(monkeypatch):
    conn, _ = _run(monkeypatch, _payload("read"))
    update = next(
        q for q in _queries(conn)
        if "UPDATE whatsapp_messages" in q and "SET status='read'" in q
    )
    assert "status <> 'failed'" in update


def test_falha_guarda_codigo_e_descricao(monkeypatch):
    conn, _ = _run(
        monkeypatch,
        _payload("failed", errors=[{"code": 131047, "title": "Re-engagement message"}]),
    )
    match = next(
        params for query, params in conn.fake_cursor.statements
        if "INSERT INTO whatsapp_message_status_events" in query
    )
    assert match[-2:] == ("131047", "Re-engagement message")
    update = next(
        q for q in _queries(conn)
        if "UPDATE whatsapp_messages" in q and "SET status='failed'" in q
    )
    assert "failure_code=%s" in update
    assert "failure_detail=%s" in update
    assert "status <> 'read'" in update


def test_timestamp_invalido_usa_fallback_sem_interpolacao_sql(monkeypatch):
    conn, _ = _run(monkeypatch, _payload("delivered", timestamp="invalid"))
    insert_params = next(
        params for query, params in conn.fake_cursor.statements
        if "INSERT INTO whatsapp_message_status_events" in query
    )
    assert insert_params[4:6] == ("invalid", "invalid")
    insert = next(
        q for q in _queries(conn)
        if "INSERT INTO whatsapp_message_status_events" in q
    )
    assert "CASE WHEN %s ~" in insert
