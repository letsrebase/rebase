"""The team request (REB-512, spec § 3.2, § 3.5): a visitor's «Assumi team» on a proposal
becomes a row the admin works, one talent per member, and a mail to rebase; and the
daily cap on proposals (spec § 5).

The freelancers, their cards and the proposals are written straight into the tables:
what is under test is what the request makes of a proposal, not how the engine writes
one (`test_team_builder.py`).
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, MODEL
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from rebase_core.analytics import TEAM_REQUEST_SENT, Tracker
from rebase_core.bands import Band
from rebase_core.config import Settings
from rebase_core.db import session_factory
from rebase_core.errors import InvalidState, NotFound, TeamBuilderBusy, ValidationFailed
from rebase_core.mail import RecordingSender, team_request_mail
from rebase_core.models import (
    AdminAction,
    Freelancer,
    FreelancerCard,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
)
from rebase_core.team_caps import BUSY_SENTENCE, proposals_today, require_daily_room
from rebase_core.team_requests import (
    ALREADY_REQUESTED,
    NOBODY_TO_HIRE,
    PROPOSAL_REFUSED,
    TeamRequestService,
    names_the_company,
)
from rebase_core.team_schemas import TeamRequestCreate

HUB = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 26, 9, 30, tzinfo=UTC)
RIASSUNTO = (
    "Un'azienda di logistica rifà il gestionale degli ordini: backend Python e frontend "
    "React, sei mesi da remoto."
)
AZIENDA = "Acme S.r.l."


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


SETTINGS = _settings(hub_url="https://letsrebase.com/hub/", contracts_mail="ciao@letsrebase.com")


class FakeCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        self.calls.append((event, distinct_id, properties))


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in (
        "team_request_talents",
        "team_requests",
        "team_proposals",
        "admin_actions",
        "freelancers",
        "users",
    ):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


@pytest.fixture
def logs(clean: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """`caplog` on this module's logger, after `clean`: Alembic's `fileConfig` disables
    every logger `alembic.ini` does not list, this one among them."""
    logging.getLogger("rebase_core.team_requests").disabled = False
    caplog.set_level(logging.INFO, logger="rebase_core.team_requests")
    return caplog


def _talent(
    session: Session,
    n: int,
    *,
    tariffa: Decimal | None = Decimal("450.00"),
    remoto: str | None = "remoto",
) -> UUID:
    user = User(email=f"talento{n}@studio.it", nome=f"Ada{n}", cognome=f"Lovelace{n}")
    session.add(user)
    session.flush()
    row = Freelancer(
        user_id=user.id,
        remoto=remoto,
        tariffa_giornaliera=tariffa,
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
            generated_at=NOW,
        )
    )
    session.commit()
    return row.id


def _user(session: Session, email: str, *, role: str = "member") -> UUID:
    user = User(email=email, nome="Ivan", cognome="Admin", role=role)
    session.add(user)
    session.commit()
    return user.id


def _proposal(
    session: Session,
    members: list[UUID],
    *,
    origine: str = "pubblico",
    user_id: UUID | None = None,
    created_at: datetime = NOW - timedelta(hours=2),
    riassunto: str = RIASSUNTO,
    model: str = MODEL,
    roles: list[str] | None = None,
) -> UUID:
    ruoli = roles or [f"Ruolo {index}" for index in range(1, len(members) + 1)]
    row = TeamProposal(
        descrizione="Rifacciamo il gestionale degli ordini, sei mesi, da remoto, in Python.",
        riassunto=riassunto,
        luogo={"locale": False, "dove": None},
        team=[
            {
                "posizione": index,
                "freelancer_id": str(freelancer_id),
                "ruolo": ruolo,
                "motivazione": "Nove anni di API in Python.",
                "giorni_settimana": 5,
            }
            for index, (freelancer_id, ruolo) in enumerate(
                zip(members, ruoli, strict=True), start=1
            )
        ],
        economia={"giorno": None, "mese": None, "giorni_mese": 22},
        model=model,
        input_tokens=5200 if model else 0,
        output_tokens=640 if model else 0,
        cache_read_tokens=4800 if model else 0,
        origine=origine,
        user_id=user_id,
        created_at=created_at,
    )
    session.add(row)
    session.commit()
    return row.id


def _data(proposal_id: UUID, azienda: str = AZIENDA) -> TeamRequestCreate:
    return TeamRequestCreate(
        proposal_id=proposal_id,
        azienda=azienda,
        email="wile@acme.it",
        telefono="+39 345 1234567",
    )


def _service(
    session: Session,
    *,
    sender: RecordingSender | None = None,
    tracker: Tracker | None = None,
    settings: Settings = SETTINGS,
) -> TeamRequestService:
    return TeamRequestService(
        session, settings=settings, sender=sender, tracker=tracker, now=lambda: NOW
    )


def _public(service: TeamRequestService, proposal_id: UUID, azienda: str = AZIENDA) -> Any:
    return service.create(
        _data(proposal_id, azienda), origine="pubblico", user_id=None, company_id=None
    )


# ---- create --------------------------------------------------------------------------------


def test_request_from_a_proposal_files_the_talents_and_mails(clean: Session) -> None:
    first = _talent(clean, 1, tariffa=Decimal("450.00"))
    second = _talent(clean, 2, tariffa=None)
    proposal_id = _proposal(clean, [first, second], roles=["Backend developer", "Designer"])
    sender = RecordingSender()
    capture = FakeCapture()

    read = _public(
        _service(clean, sender=sender, tracker=Tracker(capture)),
        proposal_id,
    )

    assert read.origine == "pubblico"
    assert read.azienda == AZIENDA
    assert read.email == "wile@acme.it"
    assert read.telefono == "+39 345 1234567"
    assert read.stato == "nuova"
    assert read.user_id is None and read.company_id is None
    assert read.note is None and read.contacted_at is None and read.closed_at is None
    assert read.riassunto == RIASSUNTO
    assert read.descrizione is not None and "gestionale" in read.descrizione
    # The admin's read of the proposal: ids and the card's place kept.
    assert read.proposal is not None
    assert [member.freelancer_id for member in read.proposal.team] == [first, second]
    assert read.proposal.team[0].scheda.luogo == "Torino"
    # One talent per member, in the proposal's order, with their own rate and band.
    assert [talent.freelancer_id for talent in read.talenti] == [first, second]
    ada = read.talenti[0]
    assert (ada.nome, ada.cognome, ada.ruolo) == ("Ada1", "Lovelace1", "Backend developer")
    assert ada.tariffa_giornaliera == Decimal("450.00")
    assert ada.fascia == Band(min=500, max=650)  # 450 x 1.4 = 630
    assert ada.mail_sent_at is None and ada.risposta is None and ada.risposta_at is None
    assert read.talenti[1].tariffa_giornaliera is None and read.talenti[1].fascia is None
    stored = clean.scalars(
        select(TeamRequestTalent).where(TeamRequestTalent.request_id == read.id)
    ).all()
    assert {row.ruolo for row in stored} == {"Backend developer", "Designer"}

    [mail] = sender.sent
    assert mail.to == "ciao@letsrebase.com"
    assert mail.subject == "Nuova richiesta team da Acme S.r.l."
    assert RIASSUNTO in mail.text
    assert f"https://letsrebase.com/hub/admin/team/{read.id}" in mail.text
    assert mail.html is not None and f"/admin/team/{read.id}" in mail.html

    [(event, _, properties)] = capture.calls
    assert event == TEAM_REQUEST_SENT == "team_richiesta_inviata"
    assert properties == {"origine": "pubblico", "$process_person_profile": False}


def test_request_without_a_sender_is_still_filed(clean: Session) -> None:
    proposal_id = _proposal(clean, [_talent(clean, 1)])

    read = _public(_service(clean), proposal_id)

    assert clean.get(TeamRequest, read.id) is not None


def test_request_is_unique_per_proposal(clean: Session, hub_engine: Engine) -> None:
    """Two clicks in two sessions: the unique index decides, and the loser is a 409."""
    proposal_id = _proposal(clean, [_talent(clean, 1)])
    sender = RecordingSender()
    factory = session_factory(hub_engine)
    with factory() as one, factory() as two:
        first = _public(_service(one, sender=sender), proposal_id)
        with pytest.raises(InvalidState) as refused:
            _public(_service(two, sender=sender), proposal_id)

    assert refused.value.message == ALREADY_REQUESTED == "Questa proposta è già stata richiesta."
    assert refused.value.details == {"proposal_id": str(proposal_id)}
    clean.expire_all()
    assert clean.scalars(select(TeamRequest.id)).all() == [first.id]
    assert len(clean.scalars(select(TeamRequestTalent)).all()) == 1
    assert len(sender.sent) == 1


def test_request_refuses_an_old_proposal(clean: Session) -> None:
    talent = _talent(clean, 1)
    day_old = _proposal(clean, [talent], created_at=NOW - timedelta(days=1))
    fresh = _proposal(clean, [talent], created_at=NOW - timedelta(days=1) + timedelta(seconds=1))
    sender = RecordingSender()
    service = _service(clean, sender=sender)

    with pytest.raises(ValidationFailed) as refused:
        _public(service, day_old)

    assert refused.value.details["field"] == "proposal_id"
    assert refused.value.details["reason"] == PROPOSAL_REFUSED
    assert _public(service, fresh).proposal is not None
    assert clean.scalars(select(TeamRequest.proposal_id)).all() == [fresh]


def test_request_refuses_a_proposal_of_another_origin_or_none(clean: Session) -> None:
    talent = _talent(clean, 1)
    owner = _user(clean, "referente@acme.it")
    cloud = _proposal(clean, [talent], origine="cloud", user_id=owner)
    admin = _proposal(clean, [talent], origine="admin", user_id=owner)
    service = _service(clean, sender=RecordingSender())

    for proposal_id in (cloud, admin, UUID("00000000-0000-7000-8000-000000000000")):
        with pytest.raises(ValidationFailed) as refused:
            _public(service, proposal_id)
        assert refused.value.details["reason"] == PROPOSAL_REFUSED
    # The cloud's own request (D3) takes only its own user's cloud proposal.
    with pytest.raises(ValidationFailed):
        service.create(_data(cloud), origine="cloud", user_id=None, company_id=None)
    assert clean.scalars(select(TeamRequest)).all() == []


def test_request_refuses_a_proposal_with_nobody(clean: Session) -> None:
    empty = _proposal(clean, [], model="", riassunto="Al momento nessun profilo corrisponde.")
    sender = RecordingSender()

    with pytest.raises(ValidationFailed) as refused:
        _public(_service(clean, sender=sender), empty)

    assert refused.value.details == {
        "entity": "team_request",
        "field": "proposal_id",
        "reason": NOBODY_TO_HIRE,
    }
    assert NOBODY_TO_HIRE == "Questa proposta non ha nessuno da assumere."
    assert clean.scalars(select(TeamRequest)).all() == []
    assert sender.sent == []


def test_request_logs_a_summary_that_names_the_company(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    proposal_id = _proposal(
        clean, [_talent(clean, 1)], riassunto="ACME rifà il gestionale degli ordini."
    )

    read = _public(_service(clean, sender=RecordingSender()), proposal_id)

    warnings = [record for record in logs.records if record.levelno == logging.WARNING]
    [record] = warnings
    logged = record.getMessage()
    assert str(read.id) in logged
    # Nothing personal: not the company, not the address, not the summary.
    for secret in ("ACME", "Acme", "wile@acme.it", "gestionale", "345"):
        assert secret not in logged
    assert clean.get(TeamRequest, read.id) is not None


def test_request_logs_nothing_personal_when_the_mail_is_refused(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    class Refusing(RecordingSender):
        def send(self, mail: Any) -> bool:
            super().send(mail)
            return False

    proposal_id = _proposal(clean, [_talent(clean, 1)])

    read = _public(_service(clean, sender=Refusing()), proposal_id)

    [record] = [record for record in logs.records if record.levelno == logging.WARNING]
    logged = record.getMessage()
    assert str(read.id) in logged
    assert "ciao@letsrebase.com" not in logged and "Acme" not in logged


@pytest.mark.parametrize(
    ("riassunto", "azienda", "expected"),
    [
        ("Acme rifà il gestionale.", "Acme S.r.l.", True),
        ("Un'azienda rifà il gestionale per ACME.", "Acme S.r.l.", True),
        ("Il gestionale di una rete di negozi.", "Rete Negozi Italia", True),
        # Words under three letters are not the company: «S», «r», «l», «di».
        ("Un'azienda di logistica, una S.r.l. di Torino.", "Acme S.r.l.", False),
        ("Un'app per le prenotazioni.", "Di Più", False),
        # A whole word, not a piece of one: «data» is not in «database».
        ("Un database per un'azienda di logistica.", "Data Srl", False),
        ("Un'azienda di logistica rifà il gestionale.", "Caffè Nero", False),
        ("Il nuovo sito del caffè più noto di Torino.", "Caffè Nero", True),
    ],
)
def test_names_the_company_words(riassunto: str, azienda: str, expected: bool) -> None:
    assert names_the_company(riassunto, azienda) is expected


def test_team_request_mail_escapes_what_the_visitor_typed() -> None:
    mail = team_request_mail(
        "ciao@letsrebase.com",
        azienda="<b>Acme</b> & C.",
        riassunto="Un gestionale <script>alert(1)</script>.",
        talento=None,
        url="https://letsrebase.com/hub/admin/team/abc",
    )

    assert mail.subject == "Nuova richiesta team da <b>Acme</b> & C."
    assert mail.html is not None
    assert "<script>" not in mail.html and "<b>Acme" not in mail.html
    assert "&lt;b&gt;Acme&lt;/b&gt; &amp; C." in mail.html
    assert "https://letsrebase.com/hub/admin/team/abc" in mail.text

    single = team_request_mail(
        "ciao@letsrebase.com",
        azienda="Acme",
        riassunto=None,
        talento="Ada Lovelace",
        url="https://letsrebase.com/hub/admin/team/abc",
    )
    assert "Ada Lovelace" in single.text


# ---- the admin's list and page -------------------------------------------------------------


def test_list_filters_and_pages_by_cursor(clean: Session) -> None:
    first = _talent(clean, 1)
    second = _talent(clean, 2)
    service = _service(clean, sender=RecordingSender())
    oldest = _public(service, _proposal(clean, [first, second]), "Uno Srl")
    middle = _public(service, _proposal(clean, [first]), "Due Srl")
    newest = _public(service, _proposal(clean, [second]), "Tre Srl")
    admin = _user(clean, "ivan@rebase.it", role="admin")
    service.set_status(middle.id, "contattata", admin)
    clean.execute(
        update(TeamRequestTalent)
        .where(TeamRequestTalent.request_id == oldest.id, TeamRequestTalent.freelancer_id == first)
        .values(risposta="si", risposta_at=NOW)
    )
    clean.commit()

    page = service.list_recent(stato=None, origine=None, limit=2, cursor=None)
    assert [item.id for item in page.items] == [newest.id, middle.id]
    assert page.next_cursor is not None
    rest = service.list_recent(stato=None, origine=None, limit=2, cursor=page.next_cursor)
    assert [item.id for item in rest.items] == [oldest.id]
    assert rest.next_cursor is None
    [item] = rest.items
    assert (item.azienda, item.origine, item.stato) == ("Uno Srl", "pubblico", "nuova")
    assert (item.talenti_totale, item.talenti_si) == (2, 1)
    assert item.contacted_at is None

    contacted = service.list_recent(stato="contattata", origine=None, limit=50, cursor=None)
    assert [item.id for item in contacted.items] == [middle.id]
    assert contacted.items[0].contacted_at == NOW
    public = service.list_recent(stato=None, origine="pubblico", limit=50, cursor=None)
    assert len(public.items) == 3
    assert service.list_recent(stato=None, origine="cloud", limit=50, cursor=None).items == []

    for field, kwargs in (
        ("stato", {"stato": "aperta", "origine": None, "cursor": None}),
        ("origine", {"stato": None, "origine": "admin", "cursor": None}),
        ("cursor", {"stato": None, "origine": None, "cursor": "non-un-cursore"}),
    ):
        with pytest.raises(ValidationFailed) as refused:
            service.list_recent(limit=10, **kwargs)
        assert refused.value.details["field"] == field


def test_get_of_a_missing_request_is_not_found(clean: Session) -> None:
    with pytest.raises(NotFound):
        _service(clean).get(UUID("00000000-0000-7000-8000-000000000000"))


def test_status_note_and_summary_record_the_admin(clean: Session) -> None:
    service = _service(clean, sender=RecordingSender())
    request = _public(service, _proposal(clean, [_talent(clean, 1)]))
    admin = _user(clean, "ivan@rebase.it", role="admin")

    contacted = service.set_status(request.id, "contattata", admin)
    assert contacted.stato == "contattata" and contacted.contacted_at == NOW
    closed = service.set_status(request.id, "chiusa", admin)
    assert closed.stato == "chiusa" and closed.closed_at == NOW
    assert closed.contacted_at == NOW
    reopened = service.set_status(request.id, "nuova", admin)
    assert reopened.closed_at is None and reopened.contacted_at == NOW
    with pytest.raises(ValidationFailed) as refused:
        service.set_status(request.id, "persa", admin)
    assert refused.value.details["field"] == "stato"

    noted = service.set_note(request.id, "  Richiamare lunedì.  ", admin)
    assert noted.note == "Richiamare lunedì."
    assert service.set_note(request.id, "   ", admin).note is None

    summary = "Un'azienda di logistica rifà il gestionale: backend Python, sei mesi."
    edited = service.set_summary(request.id, summary, admin)
    assert edited.riassunto == summary
    assert edited.proposal is not None and edited.proposal.riassunto == summary
    clean.expire_all()
    assert request.proposal is not None
    proposal = clean.get(TeamProposal, request.proposal.id)
    assert proposal is not None and proposal.riassunto == summary

    # The same value again is not a change, and records nothing.
    service.set_summary(request.id, summary, admin)
    service.set_status(request.id, "nuova", admin)

    actions = clean.scalars(select(AdminAction).order_by(AdminAction.created_at)).all()
    assert {action.entity_type for action in actions} == {"team_request"}
    assert {action.entity_id for action in actions} == {request.id}
    assert {action.admin_id for action in actions} == {admin}
    assert {action.kind for action in actions} == {"overridden"}
    assert [action.payload["changed"] for action in actions] == [
        ["stato"],
        ["stato"],
        ["stato"],
        ["note"],
        ["note"],
        ["riassunto"],
    ]
    assert actions[-1].payload["before"] == {"riassunto": RIASSUNTO}
    assert actions[-1].payload["after"] == {"riassunto": summary}


def test_summary_of_a_request_with_no_proposal_is_refused(clean: Session) -> None:
    admin = _user(clean, "ivan@rebase.it", role="admin")
    row = TeamRequest(origine="cloud", azienda="Acme", email="wile@acme.it", stato="nuova")
    clean.add(row)
    clean.commit()

    service = _service(clean)
    read = service.get(row.id)
    assert read.proposal is None and read.riassunto is None and read.descrizione is None
    with pytest.raises(InvalidState):
        service.set_summary(row.id, "Un riassunto.", admin)


# ---- the daily cap (spec § 5) --------------------------------------------------------------


def _paid(
    session: Session, created_at: datetime, *, origine: str = "pubblico", model: str = MODEL
) -> None:
    _proposal(session, [], origine=origine, created_at=created_at, model=model)


def test_the_daily_cap_counts_paid_proposals_since_midnight_in_rome(clean: Session) -> None:
    # 00:30 in Rome on 26 September (CEST, UTC+2): the day began at 22:00 UTC on the 25th.
    now = datetime(2026, 9, 25, 22, 30, tzinfo=UTC)
    midnight = datetime(2026, 9, 25, 22, 0, tzinfo=UTC)
    _paid(clean, midnight)
    _paid(clean, midnight + timedelta(minutes=10), origine="cloud")
    _paid(clean, midnight - timedelta(seconds=1))  # yesterday in Rome
    _paid(clean, midnight + timedelta(minutes=5), model="")  # an empty catalogue: no call
    owner = _user(clean, "ivan@rebase.it", role="admin")
    _proposal(clean, [], origine="admin", user_id=owner, created_at=midnight)

    assert proposals_today(clean, now=now) == 2

    require_daily_room(clean, _settings(team_builder_daily_cap=3), now=now)
    with pytest.raises(TeamBuilderBusy) as refused:
        require_daily_room(clean, _settings(team_builder_daily_cap=2), now=now)
    assert refused.value.message == BUSY_SENTENCE
    assert BUSY_SENTENCE == "Troppe richieste in questo momento: riprova tra un minuto."


def test_the_caps_have_their_defaults_and_reach_the_container() -> None:
    settings = _settings()
    assert settings.team_builder_concurrency == 4
    assert settings.team_builder_daily_cap == 300
    compose = (HUB / "docker-compose.yml").read_text(encoding="utf-8")
    example = (HUB / ".env.example").read_text(encoding="utf-8")
    for name, default in (
        ("REBASE_TEAM_BUILDER_CONCURRENCY", "4"),
        ("REBASE_TEAM_BUILDER_DAILY_CAP", "300"),
    ):
        assert f"{name}: ${{{name}:-{default}}}" in compose, name
        assert f"{name}={default}" in example, name
