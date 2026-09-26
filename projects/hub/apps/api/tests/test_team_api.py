"""The team builder over HTTP (REB-512, spec § 3.2, § 3.5, § 5): the public proposal and
request, their caps and their sentences, and the admin's «Richieste team»."""

import json
import re
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, MODEL
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_llm
from rebase_api.ratelimit import SIGNUPS_PER_MINUTE, reset_rate_limit
from rebase_core.config import Settings, get_settings
from rebase_core.errors import LlmUnavailable
from rebase_core.llm import UNAVAILABLE_SENTENCE, LlmRequest, LlmResponse, RecordingCall
from rebase_core.mail import RecordingSender
from rebase_core.models import (
    AdminAction,
    Freelancer,
    FreelancerCard,
    TeamProposal,
    TeamRequest,
    User,
)

ADMIN_EMAIL = "ivan@rebase.it"
MISSING = "00000000-0000-7000-8000-000000000000"
OFF = "Il team builder è spento."
BUSY = "Troppe richieste in questo momento: riprova tra un minuto."
ALREADY = "Questa proposta è già stata richiesta."
DESCRIZIONE = (
    "Rifacciamo il gestionale degli ordini: un backend in Python con FastAPI, sei mesi, da remoto."
)
RIASSUNTO = "Un'azienda di logistica rifà il gestionale degli ordini, backend in Python."
CONTACTS = {"azienda": "Acme S.r.l.", "email": "wile@acme.it", "telefono": "+39 345 1234567"}


@pytest.fixture
def team(api_session: Session) -> Iterator[Session]:
    yield api_session
    api_session.rollback()
    for table in (
        "team_request_talents",
        "team_requests",
        "team_proposals",
        "admin_actions",
        "freelancers",
        "users",
        "signups",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _settings(client: TestClient, **overrides: Any) -> None:
    settings = Settings(_env_file=None, **overrides)  # type: ignore[call-arg]
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]


def _llm(client: TestClient, call: Any) -> None:
    client.app.dependency_overrides[get_llm] = lambda: call  # type: ignore[attr-defined]


def _talent(session: Session, n: int = 1) -> UUID:
    user = User(email=f"talento{n}@studio.it", nome=f"Ada{n}", cognome=f"Lovelace{n}")
    session.add(user)
    session.flush()
    row = Freelancer(
        user_id=user.id,
        remoto="remoto",
        tariffa_giornaliera=Decimal("450.00"),
        stato="nuovo",
        posizione="Backend developer",
    )
    session.add(row)
    session.flush()
    session.add(
        FreelancerCard(
            freelancer_id=row.id,
            cv_sha256="0" * 64,
            card=CARD,
            model=MODEL,
            input_tokens=1200,
            output_tokens=180,
            generated_at=datetime.now(UTC),
        )
    )
    session.commit()
    return row.id


def _proposal_row(
    session: Session,
    members: list[UUID],
    *,
    origine: str = "pubblico",
    created_at: datetime | None = None,
    model: str = MODEL,
) -> UUID:
    row = TeamProposal(
        descrizione=DESCRIZIONE,
        riassunto=RIASSUNTO,
        luogo={"locale": False, "dove": None},
        team=[
            {
                "posizione": index,
                "freelancer_id": str(freelancer_id),
                "ruolo": "Backend developer",
                "motivazione": "Nove anni di API in Python.",
                "giorni_settimana": 5,
            }
            for index, freelancer_id in enumerate(members, start=1)
        ],
        economia={"giorno": None, "mese": None, "giorni_mese": 22},
        model=model,
        input_tokens=5200 if model else 0,
        output_tokens=640 if model else 0,
        cache_read_tokens=0,
        origine=origine,
        created_at=created_at or datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(row)
    session.commit()
    return row.id


def proposal_response(team: list[dict[str, Any]] | None = None) -> LlmResponse:
    body = {
        "riassunto": RIASSUNTO,
        "luogo": {"locale": False, "dove": None},
        "team": team
        if team is not None
        else [
            {
                "id": "t1",
                "ruolo": "Backend developer",
                "motivazione": "Nove anni di API in Python e FastAPI.",
                "giorni_settimana": 5,
            }
        ],
    }
    return LlmResponse(
        text=json.dumps(body),
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        input_tokens=5200,
        output_tokens=640,
        cache_read_tokens=4800,
    )


def _propose(client: TestClient) -> Any:
    return client.post("/api/hub/team/proposals", json={"descrizione": DESCRIZIONE})


def _login(client: TestClient, sender: RecordingSender, email: str = ADMIN_EMAIL) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200
    # Signing in spends two of the five a minute the public writes share.
    reset_rate_limit()


# ---- the public proposal -------------------------------------------------------------------


def test_public_proposal_answers_the_team_without_ids(client: TestClient, team: Session) -> None:
    freelancer_id = _talent(team)
    recording = RecordingCall([proposal_response()])
    _llm(client, recording)

    response = _propose(client)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["riassunto"] == RIASSUNTO
    assert body["origine"] == "pubblico"
    [member] = body["team"]
    assert member["posizione"] == 1
    assert member["freelancer_id"] is None
    assert member["scheda"]["luogo"] is None
    assert member["fascia"] == {"min": 500, "max": 650}
    assert str(freelancer_id) not in response.text
    assert "Lovelace" not in response.text and "talento1@studio.it" not in response.text
    assert len(recording.requests) == 1
    [row] = team.scalars(select(TeamProposal)).all()
    assert (row.origine, row.user_id) == ("pubblico", None)


def test_public_proposal_is_422_on_a_short_description_or_a_foreign_previous(
    client: TestClient, team: Session
) -> None:
    member = _talent(team)
    recording = RecordingCall([])
    _llm(client, recording)

    refused = client.post("/api/hub/team/proposals", json={"descrizione": "Un sito."})

    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"][-1] == "descrizione"

    # «Rigenera» on the public page takes only a public proposal (spec § 3.2).
    cloud = _proposal_row(team, [member], origine="cloud")
    foreign = client.post(
        "/api/hub/team/proposals",
        json={"descrizione": DESCRIZIONE, "previous_id": str(cloud), "nota": "Più junior."},
    )
    assert foreign.status_code == 422
    assert foreign.json()["detail"][0]["loc"] == ["body", "previous_id"]
    assert recording.requests == []


def test_public_proposal_is_503_when_off(client: TestClient, team: Session) -> None:
    _talent(team)
    # No key: no seam at all.
    refused = _propose(client)
    assert refused.status_code == 503
    assert refused.json() == {"detail": OFF}

    # A key, and the switch off.
    recording = RecordingCall([proposal_response()])
    _llm(client, recording)
    _settings(client, anthropic_api_key="sk-ant-test", team_builder_enabled=False)
    refused = _propose(client)
    assert refused.status_code == 503
    assert refused.json() == {"detail": OFF}
    assert recording.requests == []
    assert team.scalars(select(TeamProposal)).all() == []


class _Blocking:
    """A Claude that holds the call until the test lets it answer."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        self.entered.set()
        assert self.release.wait(10), "the test never let the call answer"
        return proposal_response()


def test_public_proposal_is_503_when_the_cap_is_full(client: TestClient, team: Session) -> None:
    _talent(team)
    _settings(client, team_builder_concurrency=1)
    blocking = _Blocking()
    _llm(client, blocking)
    answers: dict[str, Any] = {}
    first = threading.Thread(target=lambda: answers.setdefault("first", _propose(client)))

    first.start()
    try:
        assert blocking.entered.wait(10)
        second = _propose(client)
    finally:
        blocking.release.set()
        first.join(10)

    assert second.status_code == 503
    assert second.json() == {"detail": BUSY}
    assert second.headers["Retry-After"] == "60"
    assert answers["first"].status_code == 200, answers["first"].text
    assert len(blocking.requests) == 1
    # The slot is given back once the first answers.
    _llm(client, RecordingCall([proposal_response()]))
    assert _propose(client).status_code == 200


def test_public_proposal_is_503_when_the_daily_cap_is_reached(
    client: TestClient, team: Session
) -> None:
    member = _talent(team)
    _settings(client, team_builder_daily_cap=2)
    # Two proposals that cost nothing (an empty catalogue asks no model) do not count.
    _proposal_row(team, [], model="")
    _proposal_row(team, [], model="")
    _llm(client, RecordingCall([proposal_response()]))
    assert _propose(client).status_code == 200

    _proposal_row(team, [member])  # the second paid proposal of the day
    recording = RecordingCall([proposal_response()])
    _llm(client, recording)
    refused = _propose(client)

    assert refused.status_code == 503
    assert refused.json() == {"detail": BUSY}
    assert recording.requests == []


def test_public_proposal_is_502_when_claude_is_down(client: TestClient, team: Session) -> None:
    _talent(team)
    _settings(client, team_builder_concurrency=1)

    class Down:
        def complete(self, request: LlmRequest) -> LlmResponse:
            raise LlmUnavailable(UNAVAILABLE_SENTENCE)

    _llm(client, Down())
    refused = _propose(client)

    assert refused.status_code == 502
    assert refused.json() == {"detail": "Non riesco a proporre un team adesso: riprova tra poco."}
    assert team.scalars(select(TeamProposal)).all() == []
    # The one slot came back through the failure.
    _llm(client, RecordingCall([proposal_response()]))
    assert _propose(client).status_code == 200


def test_public_proposal_is_throttled(client: TestClient, team: Session) -> None:
    _talent(team)
    _llm(client, RecordingCall([proposal_response() for _ in range(SIGNUPS_PER_MINUTE)]))
    for _ in range(SIGNUPS_PER_MINUTE):
        assert _propose(client).status_code == 200

    refused = _propose(client)

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"


# ---- the public request --------------------------------------------------------------------


def test_public_request_is_201_then_409(
    client: TestClient, team: Session, sender: RecordingSender
) -> None:
    proposal_id = _proposal_row(team, [_talent(team)])

    created = client.post(
        "/api/hub/team/requests", json={"proposal_id": str(proposal_id), **CONTACTS}
    )

    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json() == {"id": request_id}
    row = team.get(TeamRequest, UUID(request_id))
    assert row is not None and row.origine == "pubblico" and row.azienda == "Acme S.r.l."
    [mail] = sender.sent
    assert mail.to == "ciao@letsrebase.com"
    assert mail.subject == "Nuova richiesta team da Acme S.r.l."
    assert f"/admin/team/{request_id}" in mail.text

    again = client.post(
        "/api/hub/team/requests", json={"proposal_id": str(proposal_id), **CONTACTS}
    )

    assert again.status_code == 409
    assert again.json() == {"detail": ALREADY}
    assert len(sender.sent) == 1


def test_public_request_refuses_an_old_a_cloud_or_an_empty_proposal(
    client: TestClient, team: Session, sender: RecordingSender
) -> None:
    member = _talent(team)
    old = _proposal_row(team, [member], created_at=datetime.now(UTC) - timedelta(days=1, minutes=1))
    cloud = _proposal_row(team, [member], origine="cloud")
    empty = _proposal_row(team, [], model="")

    for proposal_id, reason in (
        (old, "Questa proposta non esiste o è scaduta: chiedi di nuovo il team."),
        (cloud, "Questa proposta non esiste o è scaduta: chiedi di nuovo il team."),
        (MISSING, "Questa proposta non esiste o è scaduta: chiedi di nuovo il team."),
        (empty, "Questa proposta non ha nessuno da assumere."),
    ):
        refused = client.post(
            "/api/hub/team/requests", json={"proposal_id": str(proposal_id), **CONTACTS}
        )
        assert refused.status_code == 422, refused.text
        [detail] = refused.json()["detail"]
        assert detail["loc"] == ["body", "proposal_id"]
        assert detail["msg"] == reason
        reset_rate_limit()

    bad = client.post(
        "/api/hub/team/requests",
        json={"proposal_id": str(old), **CONTACTS, "email": "non-una-mail", "telefono": "12"},
    )
    assert bad.status_code == 422
    assert {item["loc"][-1] for item in bad.json()["detail"]} == {"email", "telefono"}
    assert team.scalars(select(TeamRequest)).all() == []
    assert sender.sent == []


def test_public_request_is_throttled(
    client: TestClient, team: Session, sender: RecordingSender
) -> None:
    proposal_id = _proposal_row(team, [_talent(team)])
    body = {"proposal_id": str(proposal_id), **CONTACTS}
    assert client.post("/api/hub/team/requests", json=body).status_code == 201
    for _ in range(SIGNUPS_PER_MINUTE - 1):
        assert client.post("/api/hub/team/requests", json=body).status_code == 409

    refused = client.post("/api/hub/team/requests", json=body)

    assert refused.status_code == 429
    assert len(sender.sent) == 1


# ---- the admin's «Richieste team» ----------------------------------------------------------


def _admin_routes() -> list[tuple[str, str, dict[str, Any] | None]]:
    base = f"/api/hub/team/requests/{MISSING}"
    return [
        ("GET", "/api/hub/team/requests", None),
        ("GET", base, None),
        ("POST", f"{base}/status", {"stato": "chiusa"}),
        ("PATCH", f"{base}/note", {"note": "x"}),
        ("PATCH", f"{base}/summary", {"riassunto": "Un riassunto."}),
    ]


def test_admin_routes_need_an_admin(
    client: TestClient, team: Session, sender: RecordingSender
) -> None:
    for method, path, body in _admin_routes():
        assert client.request(method, path, json=body).status_code == 401, path

    team.add(User(email="membro@studio.it", nome="Ada", cognome="Membro"))
    team.commit()
    _login(client, sender, "membro@studio.it")
    for method, path, body in _admin_routes():
        assert client.request(method, path, json=body).status_code == 403, path


def test_an_admin_reads_and_works_a_request(
    client: TestClient, team: Session, sender: RecordingSender
) -> None:
    team.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="Sala", role="admin"))
    team.commit()
    _login(client, sender)
    member = _talent(team)
    ids = []
    for azienda in ("Uno Srl", "Due Srl", "Tre Srl"):
        proposal_id = _proposal_row(team, [member])
        created = client.post(
            "/api/hub/team/requests",
            json={"proposal_id": str(proposal_id), **CONTACTS, "azienda": azienda},
        )
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])

    page = client.get("/api/hub/team/requests", params={"limit": 2})
    assert page.status_code == 200, page.text
    assert [item["id"] for item in page.json()["items"]] == [ids[2], ids[1]]
    rest = client.get(
        "/api/hub/team/requests", params={"limit": 2, "cursor": page.json()["next_cursor"]}
    ).json()
    assert [item["id"] for item in rest["items"]] == [ids[0]]
    assert rest["next_cursor"] is None
    item = rest["items"][0]
    assert (item["azienda"], item["origine"], item["stato"]) == ("Uno Srl", "pubblico", "nuova")
    assert (item["talenti_totale"], item["talenti_si"]) == (1, 0)

    detail = client.get(f"/api/hub/team/requests/{ids[0]}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["email"] == "wile@acme.it" and body["telefono"] == "+39 345 1234567"
    assert body["proposal"]["team"][0]["freelancer_id"] == str(member)
    [talento] = body["talenti"]
    assert (talento["nome"], talento["cognome"]) == ("Ada1", "Lovelace1")
    assert talento["tariffa_giornaliera"] == "450.00"

    moved = client.post(f"/api/hub/team/requests/{ids[0]}/status", json={"stato": "contattata"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["stato"] == "contattata" and moved.json()["contacted_at"] is not None
    only = client.get("/api/hub/team/requests", params={"stato": "contattata"}).json()
    assert [item["id"] for item in only["items"]] == [ids[0]]
    unknown = client.post(f"/api/hub/team/requests/{ids[0]}/status", json={"stato": "persa"})
    assert unknown.status_code == 422

    noted = client.patch(f"/api/hub/team/requests/{ids[0]}/note", json={"note": "Richiamare."})
    assert noted.status_code == 200 and noted.json()["note"] == "Richiamare."
    summary = "Un'azienda di logistica rifà il gestionale, sei mesi."
    edited = client.patch(f"/api/hub/team/requests/{ids[0]}/summary", json={"riassunto": summary})
    assert edited.status_code == 200, edited.text
    assert edited.json()["riassunto"] == summary
    blank = client.patch(f"/api/hub/team/requests/{ids[0]}/summary", json={"riassunto": "  "})
    assert blank.status_code == 422

    admin_id = team.scalar(select(User.id).where(User.email == ADMIN_EMAIL))
    actions = team.scalars(select(AdminAction)).all()
    assert len(actions) == 3
    assert {(action.entity_type, action.admin_id) for action in actions} == {
        ("team_request", admin_id)
    }

    assert client.get(f"/api/hub/team/requests/{MISSING}").status_code == 404
    malformed = client.get("/api/hub/team/requests", params={"cursor": "non-un-cursore"})
    assert malformed.status_code == 422
