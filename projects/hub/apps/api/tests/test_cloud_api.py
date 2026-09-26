"""The talent cloud over HTTP (REB-519, spec § 4.2): a referente with a live grant reads
the talents by name, filters them, opens a CV and asks for a team or one talent with no
form; everyone else is 401 or 403. The cloud's proposals share the public page's
semaphore and daily cap (spec § 5)."""

import json
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_cards import CARD, MODEL
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_llm
from rebase_api.ratelimit import reset_rate_limit
from rebase_core.cloud import TalentCloudService
from rebase_core.companies import CompanyService
from rebase_core.config import Settings, get_settings
from rebase_core.llm import LlmRequest, LlmResponse, RecordingCall
from rebase_core.mail import RecordingSender
from rebase_core.models import (
    Freelancer,
    FreelancerCard,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
)
from rebase_core.schemas import CompanyCreate

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
REFERENTE = "wile@acme.it"
CLOSED = "Il talent cloud non è aperto per questo account."
NOT_AVAILABLE = "Profilo non disponibile."
BUSY = "Troppe richieste in questo momento: riprova tra un minuto."
OFF = "Il team builder è spento."
DESCRIZIONE = (
    "Rifacciamo il gestionale degli ordini: un backend in Python con FastAPI, sei mesi, da remoto."
)
RIASSUNTO = "Un'azienda di logistica rifà il gestionale degli ordini, backend in Python."


@pytest.fixture
def cloud(api_session: Session) -> Iterator[Session]:
    yield api_session
    api_session.rollback()
    for table in (
        "team_request_talents",
        "team_requests",
        "team_proposals",
        "talent_cloud_grants",
        "admin_actions",
        "comments",
        "sessions",
        "magic_link_tokens",
        "logins",
        "freelancers",
        "companies",
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


def _login(client: TestClient, sender: RecordingSender, email: str = REFERENTE) -> None:
    reset_rate_limit()
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200
    reset_rate_limit()
    sender.sent.clear()


def _company(session: Session, azienda: str = "Acme S.r.l.") -> UUID:
    read, _ = CompanyService(session).request(
        CompanyCreate(
            nome_azienda=azienda,
            referente_nome="Wile",
            referente_cognome="Coyote",
            email=REFERENTE,
            telefono="+39 345 1234567",
            figura_richiesta="Backend developer",
            progetto="Serve un backend developer per tre mesi.",
            periodo_da=date(2026, 10, 1),
            durata="3 mesi",
            budget_giornaliero=Decimal("500"),
            remoto="remoto",
            numero_risorse=1,
        )
    )
    return read.id


def _open(session: Session) -> tuple[UUID, UUID]:
    """A live grant for Acme's referente: their user id and the company."""
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    session.add(admin)
    session.commit()
    company_id = _company(session)
    TalentCloudService(session, Settings(_env_file=None)).grant(company_id, admin.id)  # type: ignore[call-arg]
    user_id = session.scalar(select(User.id).where(User.email == REFERENTE))
    assert user_id is not None
    return user_id, company_id


def _talent(
    session: Session,
    cognome: str,
    *,
    stato: str = "nuovo",
    card: dict[str, Any] | None = None,
    vetted: bool = False,
) -> UUID:
    slug = cognome.lower()
    user = User(email=f"{slug}@studio.it", nome="Ada", cognome=cognome, telefono="+39 333 9998877")
    session.add(user)
    session.flush()
    row = Freelancer(
        user_id=user.id,
        remoto="remoto",
        tariffa_giornaliera=Decimal("463.21"),
        stato=stato,
        note="Nota riservata dell'admin.",
        posizione="Backend developer",
        links=[f"https://{slug}.dev"],
        cv_bytes=PDF,
        cv_filename=f"CV {cognome}.pdf",
        cv_mime="application/pdf",
        cv_size=len(PDF),
        vetted_at=datetime.now(UTC) if vetted else None,
    )
    session.add(row)
    session.flush()
    session.add(
        FreelancerCard(
            freelancer_id=row.id,
            cv_sha256="0" * 64,
            card={**CARD, **(card or {})},
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
    origine: str = "cloud",
    user_id: UUID | None = None,
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
        input_tokens=5200,
        output_tokens=640,
        cache_read_tokens=0,
        origine=origine,
        user_id=user_id,
        created_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(row)
    session.commit()
    return row.id


def proposal_response() -> LlmResponse:
    body = {
        "riassunto": RIASSUNTO,
        "luogo": {"locale": False, "dove": None},
        "team": [
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


def _cloud_propose(client: TestClient, **extra: Any) -> Any:
    return client.post("/api/hub/me/cloud/proposals", json={"descrizione": DESCRIZIONE, **extra})


def _public_propose(client: TestClient) -> Any:
    return client.post("/api/hub/team/proposals", json={"descrizione": DESCRIZIONE})


# ---- the guard -----------------------------------------------------------------------------


def _every_route(client: TestClient, freelancer_id: UUID) -> list[Any]:
    return [
        client.get("/api/hub/me/cloud/talents"),
        client.get(f"/api/hub/me/cloud/talents/{freelancer_id}/cv"),
        _cloud_propose(client),
        client.post("/api/hub/me/cloud/requests", json={"freelancer_id": str(freelancer_id)}),
    ]


def test_the_cloud_is_closed_to_anyone_without_a_live_grant(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    ada = _talent(cloud, "Lovelace")
    _llm(client, RecordingCall([proposal_response()]))

    assert [answer.status_code for answer in _every_route(client, ada)] == [401] * 4

    # Signed in, with a company request and no grant: a real person at the wrong door.
    _company(cloud)
    _login(client, sender)
    refused = _every_route(client, ada)
    assert [answer.status_code for answer in refused] == [403] * 4
    assert all(answer.json() == {"detail": CLOSED} for answer in refused)

    # An admin is no exception: the cloud is a company's.
    cloud.add(User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin"))
    cloud.commit()
    _login(client, sender, "ivan@rebase.it")
    assert [answer.status_code for answer in _every_route(client, ada)] == [403] * 4
    assert cloud.scalars(select(TeamProposal)).all() == []
    assert cloud.scalars(select(TeamRequest)).all() == []


def test_a_revoked_grant_closes_the_cloud_at_once(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    _, company_id = _open(cloud)
    _login(client, sender)
    assert client.get("/api/hub/me/cloud/talents").status_code == 200

    admin_id = cloud.scalar(select(User.id).where(User.role == "admin"))
    assert admin_id is not None
    TalentCloudService(cloud).revoke(company_id, admin_id)

    closed = client.get("/api/hub/me/cloud/talents")
    assert closed.status_code == 403 and closed.json() == {"detail": CLOSED}


# ---- the talents and their CVs ---------------------------------------------------------------


def test_the_cloud_lists_the_talents_by_name_and_filters_them(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    lovelace = _talent(cloud, "Lovelace", card={"seniority": "senior"})
    hopper = _talent(cloud, "Hopper", card={"seniority": "mid"}, vetted=True)
    _talent(cloud, "Scartata", stato="scartato")
    _open(cloud)
    _login(client, sender)

    listed = client.get("/api/hub/me/cloud/talents")

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [item["freelancer_id"] for item in body["items"]] == [str(hopper), str(lovelace)]
    assert body["ruoli"] == [CARD["ruolo"]] and body["capped"] is False
    first = body["items"][0]
    assert (first["nome"], first["cognome"], first["vetted"]) == ("Ada", "Hopper", True)
    assert first["links"] == ["https://hopper.dev"] and first["ha_cv"] is True
    assert first["card"]["sintesi"] == CARD["sintesi"] and first["card"]["luogo"] is None
    assert first["fascia"] == {"min": 500, "max": 650} and first["modalita"] == "remoto"
    # Never the rate, the state, the notes, the address or the phone.
    for secret in ("463.21", "tariffa", '"stato"', "Nota riservata", "@studio.it", "+39"):
        assert secret not in listed.text, secret

    narrowed = client.get(
        "/api/hub/me/cloud/talents",
        params={"seniority": "senior", "competenza": "fastapi", "fascia_min": 500},
    )
    assert [item["freelancer_id"] for item in narrowed.json()["items"]] == [str(lovelace)]
    assert client.get("/api/hub/me/cloud/talents", params={"modalita": "luna"}).status_code == 422
    assert client.get("/api/hub/me/cloud/talents", params={"fascia_min": -1}).status_code == 422
    long_skill = {"competenza": "x" * 101}
    assert client.get("/api/hub/me/cloud/talents", params=long_skill).status_code == 422


def test_the_cv_route_streams_a_cloud_talents_cv_and_nobody_elses(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    ada = _talent(cloud, "Lovelace")
    turned_down = _talent(cloud, "Scartata", stato="scartato")
    _open(cloud)
    _login(client, sender)

    cv = client.get(f"/api/hub/me/cloud/talents/{ada}/cv")

    assert cv.status_code == 200
    assert cv.content == PDF
    assert cv.headers["content-type"] == "application/pdf"
    assert "CV Lovelace.pdf" in cv.headers["content-disposition"]
    for freelancer_id in (turned_down, uuid4()):
        refused = client.get(f"/api/hub/me/cloud/talents/{freelancer_id}/cv")
        assert refused.status_code == 404
        assert refused.json() == {"detail": NOT_AVAILABLE}


# ---- the builder inside ----------------------------------------------------------------------


def test_a_cloud_proposal_is_the_callers_and_keeps_the_ids(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    ada = _talent(cloud, "Lovelace")
    user_id, _ = _open(cloud)
    _login(client, sender)
    recording = RecordingCall([proposal_response()])
    _llm(client, recording)

    proposed = _cloud_propose(client)

    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["origine"] == "cloud"
    [member] = body["team"]
    # The cloud's read keeps who each member is, and the card's place: it shows the CV.
    assert member["freelancer_id"] == str(ada) and member["scheda"]["luogo"] == CARD["luogo"]
    # And their name, which the builder's result shows as each card's heading.
    assert (member["nome"], member["cognome"]) == ("Ada", "Lovelace")
    row = cloud.get(TeamProposal, UUID(body["id"]))
    assert row is not None and (row.origine, row.user_id) == ("cloud", user_id)

    # «Rigenera» takes only the caller's own cloud proposal.
    public = _proposal_row(cloud, [ada], origine="pubblico")
    admin_id = cloud.scalar(select(User.id).where(User.role == "admin"))
    someone_elses = _proposal_row(cloud, [ada], user_id=admin_id)
    for previous in (public, someone_elses):
        refused = _cloud_propose(client, previous_id=str(previous), nota="Più junior.")
        assert refused.status_code == 422
    _llm(client, RecordingCall([proposal_response()]))
    again = _cloud_propose(client, previous_id=body["id"], nota="Più junior.")
    assert again.status_code == 200 and again.json()["previous_id"] == body["id"]


def test_a_cloud_proposal_is_503_when_the_builder_is_off(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    _talent(cloud, "Lovelace")
    _open(cloud)
    _login(client, sender)

    refused = _cloud_propose(client)

    assert refused.status_code == 503 and refused.json() == {"detail": OFF}


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


def _while_one_holds_the_slot(
    client: TestClient, holder: Callable[[TestClient], Any], other: Callable[[TestClient], Any]
) -> tuple[Any, Any, _Blocking]:
    """`holder`'s proposal waits inside Claude while `other` asks: their two answers,
    and the Claude that saw the calls."""
    blocking = _Blocking()
    _llm(client, blocking)
    answers: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: answers.setdefault("first", holder(client)))
    thread.start()
    try:
        assert blocking.entered.wait(10)
        refused = other(client)
    finally:
        blocking.release.set()
        thread.join(10)
    reset_rate_limit()
    return answers["first"], refused, blocking


def test_the_cloud_and_the_public_page_share_the_proposal_slots(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    """One semaphore for the process: a public proposal holding the only slot leaves
    none for the cloud, and the other way round."""
    _talent(cloud, "Lovelace")
    _open(cloud)
    _login(client, sender)
    _settings(client, team_builder_concurrency=1)

    for holder, other in ((_public_propose, _cloud_propose), (_cloud_propose, _public_propose)):
        first, refused, blocking = _while_one_holds_the_slot(client, holder, other)

        assert refused.status_code == 503, refused.text
        assert refused.json() == {"detail": BUSY}
        assert first.status_code == 200, first.text
        assert len(blocking.requests) == 1


def test_the_cloud_proposals_count_in_the_daily_cap(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    _talent(cloud, "Lovelace")
    _open(cloud)
    _login(client, sender)
    _settings(client, team_builder_daily_cap=1)
    _llm(client, RecordingCall([proposal_response()]))
    assert _cloud_propose(client).status_code == 200

    recording = RecordingCall([proposal_response(), proposal_response()])
    _llm(client, recording)
    for refused in (_public_propose(client), _cloud_propose(client)):
        assert refused.status_code == 503
        assert refused.json() == {"detail": BUSY}
    assert recording.requests == []


# ---- the requests, with no form --------------------------------------------------------------


def test_richiedi_files_one_talent_for_the_grants_company_and_mails_rebase(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    ada = _talent(cloud, "Lovelace", card={"ruolo": "Data engineer"})
    turned_down = _talent(cloud, "Scartata", stato="scartato")
    user_id, company_id = _open(cloud)
    _login(client, sender)

    created = client.post("/api/hub/me/cloud/requests", json={"freelancer_id": str(ada)})

    assert created.status_code == 201, created.text
    request_id = UUID(created.json()["id"])
    assert created.json() == {"id": str(request_id)}
    row = cloud.get(TeamRequest, request_id)
    assert row is not None
    assert (row.origine, row.proposal_id, row.user_id, row.company_id) == (
        "cloud",
        None,
        user_id,
        company_id,
    )
    assert (row.azienda, row.email, row.telefono) == ("Acme S.r.l.", REFERENTE, "+39 345 1234567")
    [talent] = cloud.scalars(
        select(TeamRequestTalent).where(TeamRequestTalent.request_id == request_id)
    ).all()
    assert (talent.freelancer_id, talent.ruolo) == (ada, "Data engineer")
    [mail] = sender.sent
    assert mail.to == "ciao@letsrebase.com"
    assert mail.subject == "Nuova richiesta team da Acme S.r.l."
    assert "Ada Lovelace" in mail.text and f"/admin/team/{request_id}" in mail.text

    # A referente with no phone on file is asked for none: the request has none.
    referente = cloud.get(User, user_id)
    assert referente is not None
    referente.telefono = None
    cloud.commit()
    again = client.post("/api/hub/me/cloud/requests", json={"freelancer_id": str(ada)})
    assert again.status_code == 201
    stored = cloud.get(TeamRequest, UUID(again.json()["id"]))
    assert stored is not None and stored.telefono is None

    for outside in (turned_down, uuid4()):
        refused = client.post("/api/hub/me/cloud/requests", json={"freelancer_id": str(outside)})
        assert refused.status_code == 404
        assert refused.json() == {"detail": NOT_AVAILABLE}
    assert len(sender.sent) == 2


def test_assumi_team_files_the_callers_cloud_proposal_at_once(
    client: TestClient, cloud: Session, sender: RecordingSender
) -> None:
    ada = _talent(cloud, "Lovelace")
    user_id, company_id = _open(cloud)
    own = _proposal_row(cloud, [ada], user_id=user_id)
    public = _proposal_row(cloud, [ada], origine="pubblico")
    _login(client, sender)

    created = client.post("/api/hub/me/cloud/requests", json={"proposal_id": str(own)})

    assert created.status_code == 201, created.text
    row = cloud.get(TeamRequest, UUID(created.json()["id"]))
    assert row is not None
    assert (row.origine, row.proposal_id, row.user_id, row.company_id, row.azienda) == (
        "cloud",
        own,
        user_id,
        company_id,
        "Acme S.r.l.",
    )
    [mail] = sender.sent
    assert RIASSUNTO in mail.text and "Lovelace" not in mail.text

    twice = client.post("/api/hub/me/cloud/requests", json={"proposal_id": str(own)})
    assert twice.status_code == 409
    refused = client.post("/api/hub/me/cloud/requests", json={"proposal_id": str(public)})
    assert refused.status_code == 422
    both = {"proposal_id": str(own), "freelancer_id": str(ada)}
    assert client.post("/api/hub/me/cloud/requests", json=both).status_code == 422
    assert client.post("/api/hub/me/cloud/requests", json={}).status_code == 422
    assert len(sender.sent) == 1
