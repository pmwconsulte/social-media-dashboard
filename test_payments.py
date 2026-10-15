import pytest
import payments


def test_paypal_sandbox_e_o_ambiente_por_omissao(monkeypatch):
    monkeypatch.delenv("PAYPAL_MODE", raising=False)
    assert payments._api_base() == "https://api-m.sandbox.paypal.com"


def test_paypal_live_usa_o_endereco_oficial(monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "live")
    assert payments._api_base() == "https://api-m.paypal.com"


def test_modo_paypal_invalido_e_rejeitado(monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "outro")
    with pytest.raises(RuntimeError, match="PAYPAL_MODE"):
        payments._api_base()


@pytest.mark.parametrize(
    ("plan_key", "env_name"),
    [
        ("starter", "PAYPAL_PLAN_STARTER_ID"),
        ("pro", "PAYPAL_PLAN_PRO_ID"),
        ("business", "PAYPAL_PLAN_BUSINESS_ID"),
    ],
)
def test_planos_usam_os_ids_configurados(monkeypatch, plan_key, env_name):
    monkeypatch.setenv(env_name, "PLAN-DE-TESTE")
    assert payments._plan_id(plan_key) == "PLAN-DE-TESTE"


def test_plano_sem_id_configurado_e_rejeitado(monkeypatch):
    monkeypatch.delenv("PAYPAL_PLAN_STARTER_ID", raising=False)
    with pytest.raises(RuntimeError, match="Plan ID"):
        payments._plan_id("starter")


def test_plano_desconhecido_e_rejeitado():
    with pytest.raises(RuntimeError, match="Plan ID"):
        payments._plan_id("desconhecido")


def test_credenciais_em_falta_nao_fazem_pedido_http(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)

    def pedido_http_nao_esperado(*args, **kwargs):
        pytest.fail("Não devia contactar o PayPal sem credenciais")

    monkeypatch.setattr(payments.requests, "post", pedido_http_nao_esperado)

    with pytest.raises(RuntimeError, match="Credenciais PayPal"):
        payments._access_token()


def test_webhook_sem_id_configurado_e_rejeitado(monkeypatch):
    monkeypatch.delenv("PAYPAL_WEBHOOK_ID", raising=False)
    with pytest.raises(RuntimeError, match="PAYPAL_WEBHOOK_ID"):
        payments._verify_webhook({"id": "EVENT-TEST"})


def test_webhook_sem_cabecalhos_obrigatorios_e_rejeitado(monkeypatch):
    from flask import Flask

    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WEBHOOK-TEST")
    app = Flask(__name__)

    with app.test_request_context("/webhooks/paypal"):
        assert payments._verify_webhook({"id": "EVENT-TEST"}) is False


def test_webhook_com_verificacao_simulada_com_sucesso(monkeypatch):
    from flask import Flask

    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "WEBHOOK-TEST")
    app = Flask(__name__)
    evento = {"id": "EVENT-TEST", "event_type": "BILLING.SUBSCRIPTION.ACTIVATED"}

    headers = {
        "PAYPAL-AUTH-ALGO": "SHA256withRSA",
        "PAYPAL-CERT-URL": "https://example.invalid/cert",
        "PAYPAL-TRANSMISSION-ID": "TRANSMISSION-TEST",
        "PAYPAL-TRANSMISSION-SIG": "SIGNATURE-TEST",
        "PAYPAL-TRANSMISSION-TIME": "2026-10-15T10:00:00Z",
    }

    chamadas = []

    def verificar_simulado(method, path, **kwargs):
        chamadas.append((method, path, kwargs))
        return {"verification_status": "SUCCESS"}

    monkeypatch.setattr(payments, "_api_request", verificar_simulado)

    with app.test_request_context("/webhooks/paypal", headers=headers):
        assert payments._verify_webhook(evento) is True

    assert len(chamadas) == 1
    method, path, kwargs = chamadas[0]
    assert method == "POST"
    assert path == "/v1/notifications/verify-webhook-signature"
    assert kwargs["json"]["webhook_id"] == "WEBHOOK-TEST"
    assert kwargs["json"]["webhook_event"] == evento


class FakeCursor:
    def __init__(self, workspace_exists=True, duplicate_subscription=False):
        self.query = ""
        self.params = None
        self.workspace_exists = workspace_exists
        self.duplicate_subscription = duplicate_subscription
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.query = query
        self.params = params
        if query.strip().startswith("UPDATE workspaces"):
            self.rowcount = 1

    def fetchone(self):
        if "WHERE slug=%s" in self.query:
            return ("workspace-uuid",) if self.workspace_exists else None
        if "WHERE subscription_id=%s" in self.query:
            return ("other-workspace-uuid",) if self.duplicate_subscription else None
        return None


class FakeConnection:
    def __init__(self, workspace_exists=True, duplicate_subscription=False):
        self.fake_cursor = FakeCursor(workspace_exists, duplicate_subscription)
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


def test_ativacao_valida_atualiza_workspace_simulado(monkeypatch):
    monkeypatch.setenv("PAYPAL_PLAN_STARTER_ID", "PLAN-STARTER-TEST")
    conn = FakeConnection()
    monkeypatch.setattr(payments, "get_connection", lambda: conn)

    subscription = {
        "id": "SUB-TEST-001",
        "custom_id": "workspace-a",
        "status": "ACTIVE",
        "plan_id": "PLAN-STARTER-TEST",
    }

    result = payments._set_active(subscription, "workspace-a")

    assert result is True
    assert conn.committed is True
    assert conn.closed is True
    assert "UPDATE workspaces" in conn.fake_cursor.query


def test_ativacao_rejeita_workspace_diferente(monkeypatch):
    def connection_nao_esperada():
        pytest.fail("Não deve aceder à base de dados")

    monkeypatch.setattr(payments, "get_connection", connection_nao_esperada)

    subscription = {
        "id": "SUB-TEST-002",
        "custom_id": "workspace-b",
        "status": "ACTIVE",
        "plan_id": "PLAN-STARTER-TEST",
    }

    assert payments._set_active(subscription, "workspace-a") is False


def test_ativacao_rejeita_plano_desconhecido(monkeypatch):
    for env_name in payments.PLAN_ENV.values():
        monkeypatch.delenv(env_name, raising=False)

    conn = FakeConnection()
    monkeypatch.setattr(payments, "get_connection", lambda: conn)

    subscription = {
        "id": "SUB-TEST-003",
        "custom_id": "workspace-a",
        "status": "ACTIVE",
        "plan_id": "PLAN-NOT-CONFIGURED",
    }

    assert payments._set_active(subscription, "workspace-a") is False
    assert conn.committed is False
    assert conn.closed is False


def test_ativacao_rejeita_subscricao_associada_a_outro_workspace(monkeypatch):
    monkeypatch.setenv("PAYPAL_PLAN_STARTER_ID", "PLAN-STARTER-TEST")
    conn = FakeConnection(duplicate_subscription=True)
    monkeypatch.setattr(payments, "get_connection", lambda: conn)

    subscription = {
        "id": "SUB-ALREADY-USED",
        "custom_id": "workspace-a",
        "status": "ACTIVE",
        "plan_id": "PLAN-STARTER-TEST",
    }

    assert payments._set_active(subscription, "workspace-a") is False
    assert conn.committed is False
    assert conn.closed is True


import pytest


@pytest.mark.parametrize(
    ("event_type", "expected_status"),
    [
        ("BILLING.SUBSCRIPTION.CANCELLED", "cancelled"),
        ("BILLING.SUBSCRIPTION.SUSPENDED", "suspended"),
        ("BILLING.SUBSCRIPTION.EXPIRED", "expired"),
    ],
)
def test_set_inactive_mapeia_estado_corretamente(
    monkeypatch, event_type, expected_status
):
    conn = FakeConnection()
    monkeypatch.setattr(payments, "get_connection", lambda: conn)

    subscription = {"id": "SUB-INACTIVE-TEST"}

    result = payments._set_inactive(subscription, event_type)

    assert result is True
    assert conn.committed is True
    assert conn.closed is True
    assert conn.fake_cursor.params[0] == expected_status


def test_set_inactive_rejeita_evento_desconhecido(monkeypatch):
    def connection_nao_esperada():
        pytest.fail("Não deve aceder à base de dados")

    monkeypatch.setattr(payments, "get_connection", connection_nao_esperada)

    assert payments._set_inactive(
        {"id": "SUB-INACTIVE-TEST"},
        "BILLING.SUBSCRIPTION.UNKNOWN",
    ) is False


def test_set_inactive_rejeita_subscricao_sem_id(monkeypatch):
    def connection_nao_esperada():
        pytest.fail("Não deve aceder à base de dados")

    monkeypatch.setattr(payments, "get_connection", connection_nao_esperada)

    assert payments._set_inactive(
        {},
        "BILLING.SUBSCRIPTION.CANCELLED",
    ) is False

from flask import Flask


class WebhookTestCursor:
    def __init__(self, event_is_new=True):
        self.event_is_new = event_is_new
        self.query = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self.query = query
        self.params = params

    def fetchone(self):
        if "INSERT INTO paypal_webhook_events" in self.query:
            return ("EVENT-TEST-001",) if self.event_is_new else None
        return None


class WebhookTestConnection:
    def __init__(self, event_is_new=True):
        self.fake_cursor = WebhookTestCursor(event_is_new)
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


def criar_app_webhook():
    app = Flask(__name__)
    app.config["TESTING"] = True
    payments.register_paypal_routes(app)
    return app


def configurar_webhook_teste(monkeypatch, conn=None):
    monkeypatch.setattr(payments, "_verify_webhook", lambda payload: True)
    monkeypatch.setattr(payments, "_enabled", lambda: True)
    monkeypatch.setattr(
        payments, "_ensure_webhook_table", lambda connection: None
    )

    if conn is not None:
        monkeypatch.setattr(payments, "get_connection", lambda: conn)


def test_webhook_evento_duplicado_responde_200(monkeypatch):
    conn = WebhookTestConnection(event_is_new=False)
    configurar_webhook_teste(monkeypatch, conn)

    app = criar_app_webhook()
    response = app.test_client().post(
        "/webhooks/paypal",
        json={
            "id": "EVENT-TEST-001",
            "event_type": "OUTRO.EVENTO",
            "resource": {},
        },
    )

    assert response.status_code == 200
    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.closed is True


def test_webhook_falha_ativacao_faz_rollback(monkeypatch):
    conn = WebhookTestConnection(event_is_new=True)
    configurar_webhook_teste(monkeypatch, conn)

    monkeypatch.setattr(
        payments,
        "_api_request",
        lambda *args, **kwargs: {
            "id": "SUB-TEST-001",
            "custom_id": "workspace-a",
            "status": "ACTIVE",
            "plan_id": "PLAN-TEST",
        },
    )
    monkeypatch.setattr(
        payments, "_set_active", lambda *args, **kwargs: False
    )

    app = criar_app_webhook()
    response = app.test_client().post(
        "/webhooks/paypal",
        json={
            "id": "EVENT-ACTIVATION-FAIL",
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
            "resource": {"id": "SUB-TEST-001"},
        },
    )

    assert response.status_code == 503
    assert conn.rolled_back is True
    assert conn.closed is True


def test_webhook_erro_interno_faz_rollback_e_fecha_conexao(monkeypatch):
    conn = WebhookTestConnection()
    configurar_webhook_teste(monkeypatch, conn)

    def falhar(*args, **kwargs):
        raise ValueError("Erro simulado")

    monkeypatch.setattr(payments, "_ensure_webhook_table", falhar)

    app = criar_app_webhook()
    response = app.test_client().post(
        "/webhooks/paypal",
        json={
            "id": "EVENT-ERROR-001",
            "event_type": "OUTRO.EVENTO",
            "resource": {},
        },
    )

    assert response.status_code == 500
    assert conn.rolled_back is True
    assert conn.closed is True




def test_webhook_automacao_desativada_responde_202(monkeypatch):
    app = criar_app_webhook()

    monkeypatch.setattr(
        payments, "_verify_webhook", lambda payload: True
    )
    monkeypatch.setattr(payments, "_enabled", lambda: False)

    chamadas_bd = []

    def conexao_nao_esperada():
        chamadas_bd.append(True)
        raise AssertionError(
            "O webhook não deve aceder à base de dados "
            "com a automação desativada"
        )

    monkeypatch.setattr(
        payments, "get_connection", conexao_nao_esperada
    )

    response = app.test_client().post(
        "/webhooks/paypal",
        json={
            "id": "EVENT-DISABLED-001",
            "event_type": "OUTRO.EVENTO",
            "resource": {},
        },
    )

    assert response.status_code == 202
    assert chamadas_bd == []
