"""«Modelli AI» over HTTP (REB-833): admin only, the key never comes back, «Prova» runs the
task's own provider and counts against its cap."""

import base64
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_admin, get_prova_call, get_settings
from rebase_core.admin_tokens import AdminRead
from rebase_core.ai_config import AiConfigService
from rebase_core.config import Settings
from rebase_core.errors import LlmUnavailable
from rebase_core.llm import LlmRequest, LlmResponse
from rebase_core.models import AdminAction, User

SECRET = "sk-secret-abcd"
SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()
BASE = "/api/hub/admin/ai"


class StubCall:
    """The provider, stubbed: records the usage the way `AiCall` does, with the purpose it
    was built with, and answers `ok` (or fails with the seam's fixed sentence)."""

    def __init__(self, session: Session, task: str, *, fail: bool = False) -> None:
        self.session = session
        self.task = task
        self.fail = fail

    def complete(self, request: LlmRequest) -> LlmResponse:
        if self.fail:
            raise LlmUnavailable("Il modello non risponde in questo momento.")
        service = AiConfigService(self.session, Settings(_env_file=None))  # type: ignore[call-arg]
        service.record_usage(
            task=self.task,
            purpose="prova",
            provider="anthropic",
            model="claude-opus-5",
            input_tokens=10,
            output_tokens=2,
        )
        return LlmResponse(
            text='{"answer": "ok"}',
            stop_reason="end_turn",
            refusal_category=None,
            model="claude-opus-5",
            provider="anthropic",
            input_tokens=10,
            output_tokens=2,
            cache_read_tokens=0,
            parsed=request.output_model(answer="ok"),
        )


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[User]:
    row = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    api_session.add(row)
    api_session.commit()
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_admin] = lambda: AdminRead(
        id=row.id, email=row.email, nome=row.nome, attivo=True, created_at=datetime.now(UTC)
    )
    overrides[get_settings] = lambda: Settings(secrets_key=SECRETS_KEY, _env_file=None)  # type: ignore[call-arg]
    yield row
    api_session.rollback()
    for statement in (
        "DELETE FROM ai_usage",
        "DELETE FROM ai_prices",
        "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, key_last4 = NULL, "
        "saved_by = NULL, saved_at = NULL, enabled = false",
        "UPDATE ai_tasks SET provider = 'anthropic', model = 'claude-opus-5', "
        "effort = 'medium', daily_cap_eur = NULL, enabled = true",
        "DELETE FROM admin_actions",
        "DELETE FROM users",
    ):
        api_session.execute(text(statement))
    api_session.commit()


@pytest.fixture
def stub(client: TestClient, api_session: Session) -> dict[str, Any]:
    state: dict[str, Any] = {"fail": False}
    client.app.dependency_overrides[get_prova_call] = lambda: (  # type: ignore[attr-defined]
        lambda task: StubCall(api_session, task, fail=state["fail"])
    )
    return state


def _price(client: TestClient) -> None:
    body = {
        "provider": "anthropic",
        "model": "claude-opus-5",
        "input_eur_per_mtok": "5",
        "output_eur_per_mtok": "25",
    }
    assert client.put(f"{BASE}/prices", json=body).status_code == 200


ROUTES = [
    ("get", BASE, None),
    ("put", f"{BASE}/providers/anthropic/key", {"key": SECRET}),
    ("put", f"{BASE}/providers/anthropic/enabled", {"enabled": True}),
    (
        "put",
        f"{BASE}/tasks/card",
        {
            "provider": "anthropic",
            "model": "claude-opus-5",
            "effort": "low",
            "max_output_tokens": 100,
            "daily_cap_eur": None,
            "enabled": True,
        },
    ),
    (
        "put",
        f"{BASE}/prices",
        {
            "provider": "anthropic",
            "model": "claude-opus-5",
            "input_eur_per_mtok": "1",
            "output_eur_per_mtok": "2",
        },
    ),
    ("post", f"{BASE}/tasks/card/prova", {}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_anonymous_gets_401_on_every_route(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    response = getattr(client, method)(path, **({} if body is None else {"json": body}))
    assert response.status_code == 401


def test_get_never_contains_the_key(client: TestClient, admin: User) -> None:
    saved = client.put(f"{BASE}/providers/anthropic/key", json={"key": SECRET})
    assert saved.status_code == 200
    assert saved.json()["key_hint"] == "configurata, finisce con …abcd"
    read = client.get(BASE)
    assert SECRET not in saved.text and SECRET not in read.text
    assert read.json()["secrets_key_present"] is True


def test_the_audit_says_replaced_never_the_key(
    client: TestClient, admin: User, api_session: Session
) -> None:
    client.put(f"{BASE}/providers/anthropic/key", json={"key": SECRET})
    payloads = [json.dumps(a.payload) for a in api_session.scalars(select(AdminAction))]
    assert any("chiave sostituita" in p for p in payloads)
    assert not any(SECRET in p for p in payloads)


def test_put_key_without_secrets_key_returns_409_with_sentence(
    client: TestClient, admin: User
) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[attr-defined, call-arg]
    response = client.put(f"{BASE}/providers/anthropic/key", json={"key": SECRET})
    assert response.status_code == 409
    assert "REBASE_SECRETS_KEY" in response.json()["detail"]
    assert SECRET not in response.text
    assert client.get(BASE).json()["secrets_key_present"] is False


def test_put_task_validates_provider_and_effort(client: TestClient, admin: User) -> None:
    body = {
        "provider": "anthropic",
        "model": "claude-opus-5",
        "effort": "low",
        "max_output_tokens": 100,
        "daily_cap_eur": None,
        "enabled": True,
    }
    assert client.put(f"{BASE}/tasks/card", json={**body, "provider": "nope"}).status_code == 422
    assert client.put(f"{BASE}/tasks/card", json={**body, "effort": "extreme"}).status_code == 422
    assert client.put(f"{BASE}/tasks/card", json={**body, "max_output_tokens": 0}).status_code == 422
    assert client.put(f"{BASE}/tasks/nope", json=body).status_code == 404


def test_prova_returns_answer_latency_cost_with_stub_model(
    client: TestClient, admin: User, stub: dict[str, Any]
) -> None:
    _price(client)
    response = client.post(f"{BASE}/tasks/card/prova")
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "ok"
    assert body["error"] is None
    assert body["latency_ms"] >= 0
    assert Decimal(body["cost_eur"]) > 0


def test_prova_error_message_has_no_key(client: TestClient, admin: User, stub: dict[str, Any]) -> None:
    client.put(f"{BASE}/providers/anthropic/key", json={"key": SECRET})
    stub["fail"] = True
    response = client.post(f"{BASE}/tasks/card/prova")
    assert response.status_code == 200
    assert response.json()["error"] == "Il modello non risponde in questo momento."
    assert SECRET not in response.text


def test_prova_counts_against_cap(client: TestClient, admin: User, stub: dict[str, Any]) -> None:
    _price(client)
    assert client.post(f"{BASE}/tasks/card/prova").json()["error"] is None
    spent = next(t for t in client.get(BASE).json()["tasks"] if t["task"] == "card")
    assert Decimal(spent["spent_today_eur"]) > 0
