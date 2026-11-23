import pytest

from social_account_policy import has_social_account_slot


class FakeCursor:
    def __init__(self, counts):
        self.counts = iter(counts)
        self.queries = []

    def execute(self, query, params):
        self.queries.append((query, params))

    def fetchone(self):
        return (next(self.counts),)


@pytest.mark.parametrize(
    "active, existing, limit, expected",
    [
        (2, 1, 2, True),   # Mesma identidade activa: reconexão permitida
        (2, 0, 2, False),  # Nova identidade: limite atingido
        (1, 0, 2, True),   # Nova identidade: ainda existe vaga
        (2, 0, 3, True),   # Nova identidade: abaixo do limite
        (0, 0, 1, True),   # Ligações passivas não entram na contagem
        (2, 0, None, True),# Plano sem limite inteiro
    ],
)
def test_social_account_slot_policy(active, existing, limit, expected):
    cur = FakeCursor([active, existing])

    result = has_social_account_slot(
        cur,
        workspace_id="workspace-test",
        platform="Facebook",
        external_account_id="page-123",
        username="page-123",
        account_limit=limit,
    )

    assert result is expected
    assert len(cur.queries) == 2


def test_policy_checks_workspace_wide_count():
    cur = FakeCursor([3, 0])

    has_social_account_slot(
        cur,
        workspace_id="workspace-test",
        platform="Instagram",
        external_account_id="ig-123",
        username="myaccount",
        account_limit=3,
    )

    count_query, count_params = cur.queries[0]
    assert "workspace_id = %s" in count_query
    assert "platform" not in count_query.lower()
    assert count_params == ("workspace-test",)


def test_policy_uses_external_identity_and_legacy_username_fallback():
    cur = FakeCursor([2, 1])

    has_social_account_slot(
        cur,
        workspace_id="workspace-test",
        platform="Instagram",
        external_account_id="ig-123",
        username="myaccount",
        account_limit=2,
    )

    identity_query, params = cur.queries[1]
    assert "external_account_id" in identity_query
    assert "username" in identity_query
    assert params == ("workspace-test", "Instagram", "ig-123", "ig-123", "myaccount", "myaccount")


def test_policy_does_not_count_passive_linked_accounts():
    cur = FakeCursor([1, 0])

    has_social_account_slot(
        cur,
        workspace_id="workspace-test",
        platform="Facebook",
        external_account_id="page-new",
        username="page-new",
        account_limit=1,
    )

    count_query, _ = cur.queries[0]
    assert "connection_status" in count_query
    assert "<> 'linked'" in count_query
