"""The team builder over HTTP (REB-512, spec § 3.2, § 3.5, § 5): the public proposal and
request, their caps and their sentences, the admin's «Richieste team», and the talents'
availability (REB-517, spec § 3.6): «Contatta i talenti» and the answer page's post."""

import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, MODEL
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from rebase_api.deps import get_clock, get_llm, get_sender, get_session_opener
from rebase_api.ratelimit import SIGNUPS_PER_MINUTE, reset_rate_limit
from rebase_core.config import Settings, get_settings
from rebase_core.errors import LlmUnavailable
from rebase_core.llm import UNAVAILABLE_SENTENCE, LlmRequest, LlmResponse, RecordingCall
from rebase_core.mail import Mail, RecordingSender
from rebase_core.models import (
    AdminAction,
    Freelancer,
    FreelancerCard,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
)
from rebase_core.team_builder import TeamBuilder

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
# 00:00:01 in Rome on 29 September (CEST, UTC+2), a second into the day the daily cap
# counts from: the instant a test that read the wall clock fell over (REB-581).
JUST_AFTER_MIDNIGHT = datetime(2026, 9, 28, 22, 0, 1, tzinfo=UTC)


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


def _clock(client: TestClient, now: datetime) -> None:
    """The builder's clock pinned at `now`, for the proposals it stamps and the daily cap
    it counts: a test that counts the day stamps its own rows from the same `now`."""
    client.app.dependency_overrides[get_clock] = lambda: lambda: now  # type: ignore[attr-defined]


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
        # A few minutes old, so a proposal the test makes afterwards is the newer one. The
        # wall clock decides which day that is in Rome, so a test that counts the day (the
        # daily cap) pins `_clock` and passes `created_at` from the same instant: planted
        # at 23:58 for a request at 00:03, or at 23:59:59 for one at 00:00:01, the rows
        # were yesterday's and the count 0 (REB-582, REB-581).
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
    # What it was asked with travels on the read (REB-675), for a page opened on it later.
    assert (body["descrizione"], body["persone"]) == (DESCRIZIONE, None)
    [member] = body["team"]
    assert member["posizione"] == 1
    assert member["freelancer_id"] is None
    assert member["nome"] is None and member["cognome"] is None
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

    # The number of people is one to ten (REB-591).
    nobody = client.post("/api/hub/team/proposals", json={"descrizione": DESCRIZIONE, "persone": 0})
    assert nobody.status_code == 422
    assert nobody.json()["detail"][0]["loc"] == ["body", "persone"]

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
    # The ask that found no slot is kept too (0028), beside the proposal that answered.
    team.expire_all()
    assert sorted(row.errore or "ok" for row in team.scalars(select(TeamProposal))) == [
        "ok",
        "team_builder_busy",
    ]
    # The slot is given back once the first answers.
    _llm(client, RecordingCall([proposal_response()]))
    assert _propose(client).status_code == 200


def test_public_proposal_is_503_when_the_daily_cap_is_reached(
    client: TestClient, team: Session
) -> None:
    # One instant for the rows, the proposal the route stamps and the cap's count.
    now = JUST_AFTER_MIDNIGHT
    _clock(client, now)
    member = _talent(team)
    _settings(client, team_builder_daily_cap=2)
    # Two proposals that cost nothing (an empty catalogue asks no model) do not count.
    _proposal_row(team, [], model="", created_at=now)
    _proposal_row(team, [], model="", created_at=now)
    _llm(client, RecordingCall([proposal_response()]))
    assert _propose(client).status_code == 200

    _proposal_row(team, [member], created_at=now)  # the second paid proposal of the day
    recording = RecordingCall([proposal_response()])
    _llm(client, recording)
    refused = client.post(
        "/api/hub/team/proposals", json={"descrizione": DESCRIZIONE, "persone": 4}
    )

    assert refused.status_code == 503
    assert refused.json() == {"detail": BUSY}
    assert recording.requests == []
    # The refused ask is kept (0028), stamped with the same instant, costing the day
    # nothing: a second ask is refused by the same count, not one higher.
    team.expire_all()
    [attempt] = team.scalars(select(TeamProposal).where(TeamProposal.errore.is_not(None))).all()
    assert (attempt.errore, attempt.persone, attempt.model, attempt.created_at) == (
        "team_builder_busy",
        4,
        "",
        now,
    )
    assert _propose(client).status_code == 503
    failed = TeamProposal.errore.is_not(None)
    assert team.scalar(select(func.count()).select_from(TeamProposal).where(failed)) == 2


def test_a_failing_count_or_record_never_keeps_a_slot_or_hides_the_refusal(
    client: TestClient, team: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 0028: the slot is given back whatever the day's count raises, so a
    database blip does not close the builder for good; and a record of the refusal
    that cannot be written is logged, never a 500 in place of the 503."""
    from rebase_api.routers import team as routes

    _talent(team)
    _settings(client, team_builder_concurrency=1)
    _llm(client, RecordingCall([proposal_response(), proposal_response()]))

    def broken_count(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("SELECT count(*)", {}, Exception("connection dropped"))

    monkeypatch.setattr(routes, "require_daily_room", broken_count)
    with pytest.raises(OperationalError):
        _propose(client)
    monkeypatch.undo()
    # The one slot is free again: the next ask is answered.
    assert _propose(client).status_code == 200

    def broken_record(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("INSERT", {}, Exception("connection dropped"))

    monkeypatch.setattr(TeamBuilder, "record_refusal", broken_record)
    _settings(client, team_builder_concurrency=1, team_builder_daily_cap=1)
    refused = _propose(client)
    assert refused.status_code == 503
    assert refused.json() == {"detail": BUSY}
    monkeypatch.undo()

    # With every refusal write busy for longer than the wait (PR #495) the row is
    # skipped: the answer is the same 503 and the table does not grow.
    from rebase_api.routers import team as routes

    _settings(client, team_builder_concurrency=1, team_builder_daily_cap=1)
    before = team.scalar(select(func.count()).select_from(TeamProposal))
    monkeypatch.setattr(routes, "_refusal_slots", threading.BoundedSemaphore(1))
    routes._refusal_slots.acquire()
    try:
        skipped = _propose(client)
    finally:
        routes._refusal_slots.release()
    assert skipped.status_code == 503 and skipped.json() == {"detail": BUSY}
    team.expire_all()
    assert team.scalar(select(func.count()).select_from(TeamProposal)) == before
    # And with every waiter taken the row is skipped at once, with no wait at all.
    monkeypatch.setattr(routes, "_refusal_waiters", threading.BoundedSemaphore(1))
    routes._refusal_waiters.acquire()
    try:
        started = time.monotonic()
        skipped = _propose(client)
        waited = time.monotonic() - started
    finally:
        routes._refusal_waiters.release()
    assert skipped.status_code == 503 and waited < routes.REFUSAL_WAIT_SECONDS
    team.expire_all()
    assert team.scalar(select(func.count()).select_from(TeamProposal)) == before


class _CrossingMidnight:
    """A clock at 23:59:59 in Rome on its first read and at 00:00:01 on every later one,
    as the wall clock is across a call to Claude that ends past midnight."""

    def __init__(self) -> None:
        self.reads: list[datetime] = []

    def __call__(self) -> datetime:
        now = JUST_AFTER_MIDNIGHT if self.reads else JUST_AFTER_MIDNIGHT - timedelta(seconds=2)
        self.reads.append(now)
        return now


def test_public_proposal_is_stamped_at_the_instant_the_daily_cap_counted(
    client: TestClient, team: Session
) -> None:
    # Read twice, the cap would count 28 September and the row be written on the 29th
    # (REB-581): the route reads the clock once, and both go by that instant.
    clock = _CrossingMidnight()
    client.app.dependency_overrides[get_clock] = lambda: clock  # type: ignore[attr-defined]
    _talent(team)
    _llm(client, RecordingCall([proposal_response()]))

    assert _propose(client).status_code == 200

    [row] = team.scalars(select(TeamProposal)).all()
    assert clock.reads == [JUST_AFTER_MIDNIGHT - timedelta(seconds=2)]
    assert row.created_at == clock.reads[0]


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
    # The ask is kept as an attempt (0028), a row the visitor was never handed.
    [attempt] = team.scalars(select(TeamProposal)).all()
    assert (attempt.errore, attempt.descrizione, attempt.riassunto) == (
        "llm_unavailable",
        DESCRIZIONE,
        "",
    )
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


def test_public_read_by_id_answers_a_fresh_public_proposal_and_404_for_the_rest(
    client: TestClient, team: Session
) -> None:
    """REB-675: `/hub/team?proposta=<id>` opens on this read, so the window is the one
    «Rigenera» and «Assumi team» already give a proposal, and every refusal is the same
    404, so the route never says which proposals exist."""
    member = _talent(team)
    fresh = _proposal_row(team, [member])
    old = _proposal_row(team, [member], created_at=datetime.now(UTC) - timedelta(days=1, minutes=1))
    cloud = _proposal_row(team, [member], origine="cloud")
    refused = _proposal_row(team, [], model="")
    team.execute(
        update(TeamProposal).where(TeamProposal.id == refused).values(errore="team_builder_busy")
    )
    team.commit()

    response = client.get(f"/api/hub/team/proposals/{fresh}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(fresh)
    assert (body["descrizione"], body["persone"], body["riassunto"]) == (
        DESCRIZIONE,
        None,
        RIASSUNTO,
    )
    [read] = body["team"]
    assert read["posizione"] == 1
    assert (
        read["freelancer_id"] is None and read["nome"] is None and read["scheda"]["luogo"] is None
    )
    assert str(member) not in response.text and "Lovelace" not in response.text
    for proposal_id in (old, cloud, refused, MISSING):
        missing = client.get(f"/api/hub/team/proposals/{proposal_id}")
        assert missing.status_code == 404, (proposal_id, missing.text)
        assert missing.json() == {"detail": f"team_proposal {proposal_id} non trovato"}
    assert client.get("/api/hub/team/proposals/not-an-id").status_code == 422
    # No speed bump on a read: the sixth in a minute still answers.
    for _ in range(SIGNUPS_PER_MINUTE + 1):
        assert client.get(f"/api/hub/team/proposals/{fresh}").status_code == 200
    # The read never touches Claude: a link kept while the builder is off still opens.
    _settings(client, team_builder_enabled=False)
    assert client.get(f"/api/hub/team/proposals/{fresh}").status_code == 200


# ---- the public request --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "stored"), [("ABCDEFGH23", "ABCDEFGH23"), ("not a code!!", None), (None, None)]
)
def test_public_request_keeps_a_well_shaped_rif_and_drops_the_rest(
    client: TestClient, team: Session, sent: str | None, stored: str | None
) -> None:
    """REB-600: `rif` is public input, cleaned like a signup's, and never a 422."""
    proposal_id = _proposal_row(team, [_talent(team)])

    created = client.post(
        "/api/hub/team/requests",
        json={"proposal_id": str(proposal_id), **CONTACTS, **({"rif": sent} if sent else {})},
    )

    assert created.status_code == 201, created.text
    row = team.get(TeamRequest, UUID(created.json()["id"]))
    assert row is not None and row.rif == stored


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
    # Sent after the answer, by the background task the route queued.
    [mail] = sender.sent
    assert mail.to == "ciao@letsrebase.com"
    assert mail.subject == "Nuova richiesta team da Acme S.r.l."
    assert f"/admin/team/{request_id}" in mail.text
    # No talent is named: the fixture's surname is on the request's page only.
    assert mail.html is not None
    assert "Lovelace" not in mail.text and "Lovelace" not in mail.html

    again = client.post(
        "/api/hub/team/requests", json={"proposal_id": str(proposal_id), **CONTACTS}
    )

    assert again.status_code == 409
    assert again.json() == {"detail": ALREADY}
    assert len(sender.sent) == 1


def test_public_request_without_a_mail_key_is_still_filed(
    client: TestClient, team: Session
) -> None:
    # No `get_sender` override: the settings carry no Resend key, so there is no sender.
    proposal_id = _proposal_row(team, [_talent(team)])

    created = client.post(
        "/api/hub/team/requests", json={"proposal_id": str(proposal_id), **CONTACTS}
    )

    assert created.status_code == 201, created.text
    assert team.get(TeamRequest, UUID(created.json()["id"])) is not None


def test_public_request_logs_nothing_personal_when_the_mail_is_refused(
    client: TestClient, team: Session, caplog: pytest.LogCaptureFixture
) -> None:
    class Refusing(RecordingSender):
        def send(self, mail: Any) -> bool:
            super().send(mail)
            return False

    refusing = Refusing()
    client.app.dependency_overrides[get_sender] = lambda: refusing  # type: ignore[attr-defined]
    # Alembic's `fileConfig` disabled every logger it does not list, this one among them.
    logging.getLogger("rebase_api.routers.team").disabled = False
    caplog.set_level(logging.INFO, logger="rebase_api.routers.team")
    proposal_id = _proposal_row(team, [_talent(team)])

    created = client.post(
        "/api/hub/team/requests", json={"proposal_id": str(proposal_id), **CONTACTS}
    )

    # The request stands: the refusal came after the answer.
    assert created.status_code == 201, created.text
    assert len(refusing.sent) == 1
    [record] = [record for record in caplog.records if record.levelno == logging.WARNING]
    logged = record.getMessage()
    assert created.json()["id"] in logged
    for secret in ("ciao@letsrebase.com", "wile@acme.it", "Acme", "gestionale", "345"):
        assert secret not in logged


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
        ("GET", "/api/hub/team/proposals", None),
        ("GET", base, None),
        ("POST", f"{base}/status", {"stato": "chiusa"}),
        ("PATCH", f"{base}/note", {"note": "x"}),
        ("PATCH", f"{base}/summary", {"riassunto": "Un riassunto."}),
        ("POST", f"{base}/contact", None),
        ("POST", f"{base}/contact?only_silent=true", None),
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
    removed = _talent(team, 2)
    team.execute(
        update(Freelancer).where(Freelancer.id == removed).values(deleted_at=datetime.now(UTC))
    )
    team.commit()
    ids = []
    for azienda in ("Uno Srl", "Due Srl", "Tre Srl"):
        proposal_id = _proposal_row(team, [member])
        created = client.post(
            "/api/hub/team/requests",
            json={"proposal_id": str(proposal_id), **CONTACTS, "azienda": azienda},
        )
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])
    # An older ask never filed, holding a talent removed with «Elimina» since (REB-607).
    never = _proposal_row(team, [member, removed], created_at=datetime.now(UTC) - timedelta(days=1))

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

    # «Proposte» (0028): the three asks, newest first, each naming its request.
    proposals = client.get("/api/hub/team/proposals", params={"limit": 2})
    assert proposals.status_code == 200, proposals.text
    assert [item["request_id"] for item in proposals.json()["items"]] == [ids[2], ids[1]]
    rest = client.get(
        "/api/hub/team/proposals", params={"limit": 2, "cursor": proposals.json()["next_cursor"]}
    ).json()
    oldest, unfiled = rest["items"]
    assert (oldest["request_id"], oldest["origine"], oldest["errore"], oldest["membri"]) == (
        ids[0],
        "pubblico",
        None,
        1,
    )
    assert oldest["descrizione"] == DESCRIZIONE and oldest["persone"] is None
    # REB-607: the row names who it held, with the id the admin's profile page takes;
    # a talent removed with «Elimina» since is kept by id and serialised unnamed.
    assert (unfiled["id"], unfiled["request_id"], unfiled["membri"]) == (str(never), None, 2)
    assert unfiled["team"] == [
        {
            "posizione": 1,
            "freelancer_id": str(member),
            "ruolo": "Backend developer",
            "nome": "Ada1",
            "cognome": "Lovelace1",
        },
        {
            "posizione": 2,
            "freelancer_id": str(removed),
            "ruolo": "Backend developer",
            "nome": None,
            "cognome": None,
        },
    ]
    assert rest["next_cursor"] is None
    assert client.get("/api/hub/team/proposals", params={"esito": "errore"}).json()["items"] == []
    assert client.get("/api/hub/team/proposals", params={"esito": "boh"}).status_code == 422
    assert client.get("/api/hub/team/proposals", params={"origine": "sito"}).status_code == 422

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


# ---- the talents' availability (REB-517, spec § 3.2, § 3.6) --------------------------------

NAMES = "Il riassunto nomina l'azienda: correggilo prima di scrivere ai talenti."
CONTACTED = "I talenti sono già stati contattati: rimanda a chi non ha risposto."
NOBODY = "Non c'è nessun talento da contattare."


@pytest.fixture
def mailbox(client: TestClient, team: Session, sender: RecordingSender) -> RecordingSender:
    """The recording sender, and the background delivery's session the test's own, the
    way the `llm` fixture hands the card writer's: the mails leave after the answer."""
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_session_opener] = lambda: lambda: nullcontext(team)
    return sender


def _admin_request(
    client: TestClient, team: Session, sender: RecordingSender, members: int = 2
) -> tuple[str, list[UUID]]:
    """An admin signed in, and a public request for a team of `members` talents."""
    team.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="Sala", role="admin"))
    team.commit()
    _login(client, sender)
    talents = [_talent(team, n) for n in range(1, members + 1)]
    created = client.post(
        "/api/hub/team/requests",
        json={"proposal_id": str(_proposal_row(team, talents)), **CONTACTS},
    )
    assert created.status_code == 201, created.text
    reset_rate_limit()
    return created.json()["id"], talents


def _availability(sender: RecordingSender) -> list[Mail]:
    return [mail for mail in sender.sent if mail.subject == "Un progetto per te: sei disponibile?"]


def _token(mail: Mail) -> str:
    found = re.search(r"/hub/team/risposta\?t=([A-Za-z0-9_-]+)&r=si", mail.text)
    assert found, mail.text
    return found.group(1)


def test_an_admin_contacts_the_talents_and_each_answers_once(
    client: TestClient, team: Session, mailbox: RecordingSender
) -> None:
    request_id, talents = _admin_request(client, team, mailbox)

    contacted = client.post(f"/api/hub/team/requests/{request_id}/contact")

    assert contacted.status_code == 200, contacted.text
    body = contacted.json()
    assert body["stato"] == "contattata" and body["contacted_at"] is not None
    assert all(talento["mail_sent_at"] is not None for talento in body["talenti"])
    assert all(talento["contattabile"] for talento in body["talenti"])
    # Sent after the answer, by the background task, to each talent once.
    first, second = _availability(mailbox)
    assert (first.to, second.to) == ("talento1@studio.it", "talento2@studio.it")
    assert "https://letsrebase.com/hub/team/risposta?t=" in first.text
    assert "Acme" not in first.text

    # A mail scanner's GET records nothing.
    assert client.get("/api/hub/team/availability", params={"t": _token(first)}).status_code == 405
    answered = client.post(
        "/api/hub/team/availability", json={"t": _token(first), "risposta": "si"}
    )
    assert answered.status_code == 200, answered.text
    assert answered.json() == {"esito": "si"}
    again = client.post("/api/hub/team/availability", json={"t": _token(first), "risposta": "no"})
    assert again.json() == {"esito": "invalid"}
    unknown = client.post(
        "/api/hub/team/availability", json={"t": "non-un-token", "risposta": "no"}
    )
    assert unknown.json() == {"esito": "invalid"}
    assert (
        client.post(
            "/api/hub/team/availability", json={"t": _token(second), "risposta": "forse"}
        ).status_code
        == 422
    )
    team.expire_all()
    row = team.scalar(
        select(TeamRequestTalent).where(TeamRequestTalent.freelancer_id == talents[0])
    )
    assert row is not None and row.risposta == "si" and row.risposta_at is not None
    reset_rate_limit()

    # «Contatta i talenti» is a first time only; «Rimanda» writes to the silent one.
    refused = client.post(f"/api/hub/team/requests/{request_id}/contact")
    assert refused.status_code == 409
    assert refused.json() == {"detail": CONTACTED}
    resent = client.post(f"/api/hub/team/requests/{request_id}/contact?only_silent=true")
    assert resent.status_code == 200, resent.text
    [*_, last] = _availability(mailbox)
    assert len(_availability(mailbox)) == 3 and last.to == "talento2@studio.it"
    assert client.post(
        "/api/hub/team/availability", json={"t": _token(last), "risposta": "no"}
    ).json() == {"esito": "no"}
    nobody = client.post(f"/api/hub/team/requests/{request_id}/contact?only_silent=true")
    assert nobody.status_code == 409
    assert nobody.json() == {"detail": NOBODY}

    detail = client.get(f"/api/hub/team/requests/{request_id}").json()
    assert [talento["risposta"] for talento in detail["talenti"]] == ["si", "no"]
    item = client.get("/api/hub/team/requests").json()["items"][0]
    assert (item["talenti_totale"], item["talenti_si"]) == (2, 1)


def test_contact_refuses_a_summary_that_names_the_company(
    client: TestClient, team: Session, mailbox: RecordingSender
) -> None:
    request_id, talents = _admin_request(client, team, mailbox, members=1)
    row = team.get(TeamRequest, UUID(request_id))
    assert row is not None and row.proposal_id is not None
    proposal = team.get(TeamProposal, row.proposal_id)
    assert proposal is not None
    proposal.riassunto = "ACME rifà il gestionale degli ordini."
    team.commit()

    refused = client.post(f"/api/hub/team/requests/{request_id}/contact")

    assert refused.status_code == 409
    assert refused.json() == {"detail": NAMES}
    assert _availability(mailbox) == []
    assert client.get(f"/api/hub/team/requests/{request_id}").json()["stato"] == "nuova"


def test_contact_without_a_mail_key_is_503(
    client: TestClient, team: Session, sender: RecordingSender
) -> None:
    request_id, _ = _admin_request(client, team, sender, members=1)
    client.app.dependency_overrides[get_sender] = lambda: None  # type: ignore[attr-defined]

    refused = client.post(f"/api/hub/team/requests/{request_id}/contact")

    assert refused.status_code == 503
    assert refused.json() == {
        "detail": (
            "L'invio delle email non è attivo su questo ambiente: la mail non arriverebbe "
            "ai talenti."
        )
    }
    detail = client.get(f"/api/hub/team/requests/{request_id}").json()
    assert detail["stato"] == "nuova"
    assert [talento["mail_sent_at"] for talento in detail["talenti"]] == [None]


def test_a_refused_availability_mail_is_logged_by_id_alone(
    client: TestClient, team: Session, sender: RecordingSender, caplog: pytest.LogCaptureFixture
) -> None:
    request_id, _ = _admin_request(client, team, sender, members=1)

    class Refusing(RecordingSender):
        def send(self, mail: Mail) -> bool:
            super().send(mail)
            return False

    refusing = Refusing()
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_sender] = lambda: refusing
    overrides[get_session_opener] = lambda: lambda: nullcontext(team)
    logging.getLogger("rebase_core.team_requests").disabled = False
    caplog.set_level(logging.INFO, logger="rebase_core.team_requests")

    contacted = client.post(f"/api/hub/team/requests/{request_id}/contact")

    assert contacted.status_code == 200, contacted.text
    # The answer came before the delivery: it shows the talent contacted.
    assert contacted.json()["stato"] == "contattata"
    assert len(refusing.sent) == 1
    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 2  # the refusal, and the request put back
    for logged in warnings:
        assert request_id in logged
        for secret in ("talento1", "studio.it", "Ada1", "Lovelace", "Acme"):
            assert secret not in logged
    # The link reached nobody: the talent is not contacted, nor is the request, and
    # «Contatta i talenti» is free again.
    detail = client.get(f"/api/hub/team/requests/{request_id}").json()
    assert [talento["mail_sent_at"] for talento in detail["talenti"]] == [None]
    assert detail["stato"] == "nuova" and detail["contacted_at"] is None


def test_the_answer_post_is_throttled(client: TestClient, team: Session) -> None:
    for _ in range(SIGNUPS_PER_MINUTE):
        answered = client.post("/api/hub/team/availability", json={"t": "x", "risposta": "si"})
        assert answered.json() == {"esito": "invalid"}

    refused = client.post("/api/hub/team/availability", json={"t": "x", "risposta": "si"})

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
