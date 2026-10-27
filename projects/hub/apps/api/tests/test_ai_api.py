"""«Modelli AI» over HTTP (REB-833): admin only, the key never comes back, «Prova» runs the
task's own provider and counts against its cap."""

import base64
import json
import os
import re
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fakes_cards import card_response, text_pdf
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_api.deps import (
    get_admin,
    get_model_factory,
    get_prova_call,
    get_session_opener,
    get_settings,
)
from rebase_core.admin_tokens import AdminRead
from rebase_core.ai_config import AiConfigService
from rebase_core.cards import CardWriter
from rebase_core.config import Settings
from rebase_core.errors import LlmUnavailable
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import LlmRequest, LlmResponse, RecordingCall
from rebase_core.mail import RecordingSender
from rebase_core.models import AdminAction, AiUsage, FreelancerCard, User
from rebase_core.schemas import FreelancerCreate

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
    ("put", f"{BASE}/sources/github/enabled", {"enabled": True}),
    ("get", f"{BASE}/cards/stale", None),
    ("post", f"{BASE}/cards/regenerate-stale", {"limit": 5}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_anonymous_gets_401_on_every_route(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    response = getattr(client, method)(path, **({} if body is None else {"json": body}))
    assert response.status_code == 401


@pytest.fixture
def member(client: TestClient, api_session: Session, sender: RecordingSender) -> Iterator[User]:
    """A signed-in member who is not an admin: a real session through the magic link, with
    `get_admin` not overridden, which is the point."""
    row = User(email="ada.member@studio.it", nome="Ada", cognome="Lovelace", role="member")
    api_session.add(row)
    api_session.commit()
    assert client.post("/api/hub/auth/link", json={"email": row.email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200
    assert client.get("/api/hub/me").json()["role"] == "member"
    yield row
    api_session.rollback()
    for table in ("admin_tokens", "freelancers", "users"):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_non_admin_gets_403_on_every_route(
    client: TestClient, member: User, method: str, path: str, body: Any
) -> None:
    """A member's cookie is a real identity at the wrong door: 403, not the signed-out 401,
    and nothing is saved, spent or read."""
    response = getattr(client, method)(path, **({} if body is None else {"json": body}))
    assert response.status_code == 403
    assert SECRET not in response.text


def test_a_malformed_key_request_does_not_echo_the_key_in_the_422(
    client: TestClient, admin: User
) -> None:
    """Review of #666, minor 7: FastAPI's default 422 carries each error's `input`."""
    too_long = SECRET + "x" * 600
    over = client.put(f"{BASE}/providers/anthropic/key", json={"key": too_long})
    bare = client.put(f"{BASE}/providers/anthropic/key", json=too_long)

    for response in (over, bare):
        assert response.status_code == 422
        assert SECRET not in response.text
        assert "input" not in json.dumps(response.json())
    # Every other route keeps FastAPI's own answer.
    other = client.put(f"{BASE}/providers/anthropic/enabled", json={"enabled": "peut-être"})
    assert other.status_code == 422 and "input" in other.json()["detail"][0]


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
    assert (
        client.put(f"{BASE}/tasks/card", json={**body, "max_output_tokens": 0}).status_code == 422
    )
    assert client.put(f"{BASE}/tasks/nope", json=body).status_code == 404


def test_an_amount_the_columns_cannot_hold_is_a_422_not_a_500(
    client: TestClient, admin: User
) -> None:
    price = {
        "provider": "anthropic",
        "model": "claude-opus-5",
        "input_eur_per_mtok": "100000000",
        "output_eur_per_mtok": "25",
    }
    assert client.put(f"{BASE}/prices", json=price).status_code == 422
    task = {
        "provider": "anthropic",
        "model": "claude-opus-5",
        "effort": "low",
        "max_output_tokens": 100,
        "daily_cap_eur": "1e9",
        "enabled": True,
    }
    assert client.put(f"{BASE}/tasks/card", json=task).status_code == 422


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


def test_prova_error_message_has_no_key(
    client: TestClient, admin: User, stub: dict[str, Any]
) -> None:
    client.put(f"{BASE}/providers/anthropic/key", json={"key": SECRET})
    stub["fail"] = True
    response = client.post(f"{BASE}/tasks/card/prova")
    assert response.status_code == 200
    assert response.json()["error"] == "Il modello non risponde in questo momento."
    assert SECRET not in response.text


def test_prova_goes_through_the_real_seam_and_counts_against_cap(
    client: TestClient, admin: User, api_engine: Engine
) -> None:
    """Only the provider's model is a stub: `get_prova_call` builds the real `AiCall`, so
    the usage row has the purpose and the task the wiring gave it, and the cap sees it."""
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_session_opener] = lambda: lambda: closing(Session(api_engine))
    seen: list[tuple[str, str, str, str | None]] = []

    def model_factory(
        provider: str, model: str, api_key: str, effort: str | None
    ) -> tuple[Model, ModelSettings]:
        seen.append((provider, model, api_key, effort))
        answer = ModelResponse(
            parts=[TextPart(content='{"answer": "ok"}')],
            usage=RequestUsage(input_tokens=10, output_tokens=2),
        )
        return FunctionModel(lambda messages, info: answer, model_name=model), ModelSettings()

    overrides[get_model_factory] = lambda: model_factory
    assert client.put(f"{BASE}/providers/anthropic/key", json={"key": SECRET}).status_code == 200
    assert (
        client.put(f"{BASE}/providers/anthropic/enabled", json={"enabled": True}).status_code == 200
    )
    _price(client)

    body = client.post(f"{BASE}/tasks/card/prova").json()

    assert body["error"] is None and body["answer"] == "ok"
    assert Decimal(body["cost_eur"]) > 0
    assert seen == [("anthropic", "claude-opus-5", SECRET, "medium")]
    spent = next(t for t in client.get(BASE).json()["tasks"] if t["task"] == "card")
    assert Decimal(spent["spent_today_eur"]) > 0
    with Session(api_engine) as check:
        [usage] = check.scalars(select(AiUsage)).all()
        assert (usage.task, usage.purpose) == ("card", "prova")


# ---- the sources and the cards that wait for an admin (REB-839) ---------------------------


def test_source_switches_default_off_and_toggle(
    client: TestClient, admin: User, api_session: Session
) -> None:
    """Both switches are off until the privacy notice covers reading what people linked;
    turning one is an `ai_switch` entry with the before and after, and off is read back."""
    assert [(s["kind"], s["enabled"]) for s in client.get(BASE).json()["sources"]] == [
        ("github", False),
        ("links", False),
    ]

    on = client.put(f"{BASE}/sources/github/enabled", json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True and on.json()["updated_by_name"] == "Ivan Sala"
    assert [s["enabled"] for s in client.get(BASE).json()["sources"]] == [True, False]
    [action] = api_session.scalars(select(AdminAction).where(AdminAction.kind == "ai_switch"))
    assert action.payload["source"] == "github"

    off = client.put(f"{BASE}/sources/github/enabled", json={"enabled": False})
    assert off.json()["enabled"] is False
    assert client.put(f"{BASE}/sources/linkedin/enabled", json={"enabled": True}).status_code == 422
    api_session.execute(text("UPDATE ai_sources SET enabled = false, updated_by = NULL"))
    api_session.commit()


def test_non_admin_cannot_toggle_sources(
    client: TestClient, member: User, api_session: Session
) -> None:
    response = client.put(f"{BASE}/sources/github/enabled", json={"enabled": True})

    assert response.status_code == 403
    enabled = api_session.scalar(text("SELECT count(*) FROM ai_sources WHERE enabled"))
    assert enabled == 0


@pytest.fixture
def written_cards(admin: User, api_session: Session) -> Iterator[list[Any]]:
    """Two people with a card written by prompt version 1 and a third with a fresh one."""
    people = []
    for n in range(1, 4):
        data = FreelancerCreate(
            nome=f"Persona{n}",
            cognome=f"Cognome{n}",
            email=f"persona{n}@studio.it",
            tariffa_giornaliera=Decimal("450.00"),
            posizione="Backend developer",
            remoto="remoto",
        )
        read, _ = FreelancerService(api_session).apply(
            data, text_pdf(f"Persona{n}, backend developer."), "cv.pdf", ""
        )
        CardWriter(api_session, RecordingCall([card_response()])).write(read.id)
        people.append(read.id)
    api_session.execute(
        text("UPDATE freelancer_cards SET prompt_version = 1 WHERE freelancer_id <> :keep"),
        {"keep": people[2]},
    )
    api_session.commit()
    yield people
    api_session.rollback()
    for table in ("ai_usage", "admin_actions", "freelancers"):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def test_stale_cards_lists_the_count_the_cost_and_the_reason(
    client: TestClient, written_cards: list[Any], api_session: Session
) -> None:
    waiting = client.get(f"{BASE}/cards/stale")

    assert waiting.status_code == 200, waiting.text
    body = waiting.json()
    assert (body["count"], body["by_reason"]) == (2, {"prompt": 2})
    assert body["estimated_cost_eur"] is None  # no `card` call recorded yet: never «free»
    assert sorted(item["nome"] for item in body["items"]) == [
        "Persona1 Cognome1",
        "Persona2 Cognome2",
    ]
    assert {item["reason"] for item in body["items"]} == {"prompt"}

    api_session.add(
        AiUsage(
            task="card",
            purpose="run",
            provider="anthropic",
            model="claude-opus-5",
            priced=True,
            cost_eur=Decimal("0.25"),
        )
    )
    api_session.commit()
    assert Decimal(client.get(f"{BASE}/cards/stale").json()["estimated_cost_eur"]) == Decimal("0.5")


def test_regenerate_stale_writes_the_limit_after_the_response(
    client: TestClient, written_cards: list[Any], api_session: Session, llm: RecordingCall
) -> None:
    started = client.post(f"{BASE}/cards/regenerate-stale", json={"limit": 1})

    assert started.status_code == 200, started.text
    assert started.json() == {"queued": 1}
    # The test client runs the background task before it answers.
    assert len(llm.requests) == 1
    assert client.get(f"{BASE}/cards/stale").json()["count"] == 1
    [action] = api_session.scalars(
        select(AdminAction).where(AdminAction.kind == "cards_regenerate")
    )
    assert action.payload == {"limit": 1, "queued": 1}
    rows = api_session.scalars(select(FreelancerCard).where(FreelancerCard.prompt_version == 2))
    assert len(rows.all()) == 2

    # Nothing is stale after the second start, and a start with nothing queues nothing.
    assert client.post(f"{BASE}/cards/regenerate-stale", json={"limit": 5}).json() == {"queued": 1}
    assert client.post(f"{BASE}/cards/regenerate-stale", json={"limit": 5}).json() == {"queued": 0}
    assert len(llm.requests) == 2


def test_regenerate_stale_needs_a_model_and_a_sane_limit(
    client: TestClient, written_cards: list[Any]
) -> None:
    no_model = client.post(f"{BASE}/cards/regenerate-stale", json={"limit": 5})
    assert no_model.status_code == 503

    for limit in (0, 51, "tutte"):
        bad = client.post(f"{BASE}/cards/regenerate-stale", json={"limit": limit})
        assert bad.status_code == 422


def test_a_start_while_another_is_running_queues_nothing(
    client: TestClient, written_cards: list[Any], api_session: Session, llm: RecordingCall
) -> None:
    """The lock of a running batch is held on a connection of its own, as the batch holds
    it: a second click finds it and the page is told nothing was queued."""
    import hashlib

    key = int.from_bytes(
        hashlib.sha256(b"rebase:regenerate_stale").digest()[:8], "big", signed=True
    )
    with api_session.get_bind().connect() as holder:  # type: ignore[union-attr]
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        try:
            second = client.post(f"{BASE}/cards/regenerate-stale", json={"limit": 5})
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            holder.commit()

    assert second.json() == {"queued": 0}
    assert llm.requests == []
