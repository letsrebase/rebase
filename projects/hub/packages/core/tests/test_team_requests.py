"""The team request (REB-512, spec § 3.2, § 3.5): a visitor's «Assumi team» on a proposal
becomes a row the admin works, one talent per member, and a mail to rebase; the
availability mail to each talent and the answer they give (REB-517, spec § 3.6); and the
daily cap on proposals (spec § 5).

The freelancers, their cards and the proposals are written straight into the tables:
what is under test is what the request makes of a proposal, not how the engine writes
one (`test_team_builder.py`).
"""

import hashlib
import logging
import re
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

from rebase_core.analytics import TEAM_REQUEST_SENT, TEAM_TALENT_ANSWER, Tracker
from rebase_core.bands import Band
from rebase_core.config import Settings
from rebase_core.db import session_factory
from rebase_core.errors import InvalidState, NotFound, TeamBuilderBusy, ValidationFailed
from rebase_core.mail import (
    AVAILABLE_GREEN,
    CTA,
    Mail,
    RecordingSender,
    team_availability_mail,
    team_request_mail,
)
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
    ALREADY_CONTACTED,
    ALREADY_REQUESTED,
    ANSWER_MAX_AGE,
    NAMES_THE_COMPANY,
    NO_SENDER,
    NOBODY_TO_CONTACT,
    NOBODY_TO_HIRE,
    PROPOSAL_REFUSED,
    REQUEST_CLOSED,
    TeamRequestService,
    names_the_company,
)
from rebase_core.team_schemas import TeamRequestCreate, TeamRequestRead

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
    session: Session, *, tracker: Tracker | None = None, settings: Settings = SETTINGS
) -> TeamRequestService:
    return TeamRequestService(session, settings=settings, tracker=tracker, now=lambda: NOW)


def _create(
    service: TeamRequestService, proposal_id: UUID, azienda: str = AZIENDA
) -> tuple[TeamRequestRead, Mail]:
    return service.create(
        _data(proposal_id, azienda), origine="pubblico", user_id=None, company_id=None
    )


def _public(
    service: TeamRequestService, proposal_id: UUID, azienda: str = AZIENDA
) -> TeamRequestRead:
    return _create(service, proposal_id, azienda)[0]


# ---- create --------------------------------------------------------------------------------


def test_request_from_a_proposal_files_the_talents_and_mails(clean: Session) -> None:
    first = _talent(clean, 1, tariffa=Decimal("450.00"))
    second = _talent(clean, 2, tariffa=None)
    proposal_id = _proposal(clean, [first, second], roles=["Backend developer", "Designer"])
    capture = FakeCapture()

    read, mail = _create(_service(clean, tracker=Tracker(capture)), proposal_id)

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

    # The mail is built, not sent: the route sends it after its answer.
    assert mail.to == "ciao@letsrebase.com"
    assert mail.subject == "Nuova richiesta team da Acme S.r.l."
    assert RIASSUNTO in mail.text
    assert f"https://letsrebase.com/hub/admin/team/{read.id}" in mail.text
    assert mail.html is not None and f"/admin/team/{read.id}" in mail.html
    # It names no talent: the names are on the request's page, behind the admin cookie.
    for talent in read.talenti:
        assert talent.cognome not in mail.text and talent.cognome not in mail.html
    assert "Lovelace" not in mail.text + mail.html

    [(event, _, properties)] = capture.calls
    assert event == TEAM_REQUEST_SENT == "team_richiesta_inviata"
    assert properties == {"origine": "pubblico", "$process_person_profile": False}


def test_the_mail_of_a_single_talent_request_names_the_talent(clean: Session) -> None:
    """`request_mail` is what the cloud's «Richiedi» (D3) reuses: a request of one
    talent and no proposal says who was asked, in place of a summary."""
    talent = _talent(clean, 1)
    row = TeamRequest(origine="cloud", azienda="Acme", email="wile@acme.it", stato="nuova")
    clean.add(row)
    clean.flush()
    clean.add(TeamRequestTalent(request_id=row.id, freelancer_id=talent, ruolo="Backend"))
    clean.commit()
    service = _service(clean)

    mail = service.request_mail(service.get(row.id))

    assert mail.subject == "Nuova richiesta team da Acme"
    assert "Ada1 Lovelace1" in mail.text and "talent cloud" in mail.text
    assert f"/admin/team/{row.id}" in mail.text


def test_request_is_unique_per_proposal(clean: Session, hub_engine: Engine) -> None:
    """Two clicks in two sessions: the unique index decides, and the loser is a 409."""
    proposal_id = _proposal(clean, [_talent(clean, 1)])
    factory = session_factory(hub_engine)
    with factory() as one, factory() as two:
        first = _public(_service(one), proposal_id)
        with pytest.raises(InvalidState) as refused:
            _public(_service(two), proposal_id)

    assert refused.value.message == ALREADY_REQUESTED == "Questa proposta è già stata richiesta."
    assert refused.value.details == {"proposal_id": str(proposal_id)}
    clean.expire_all()
    assert clean.scalars(select(TeamRequest.id)).all() == [first.id]
    assert len(clean.scalars(select(TeamRequestTalent)).all()) == 1


def test_request_refuses_an_old_proposal(clean: Session) -> None:
    talent = _talent(clean, 1)
    day_old = _proposal(clean, [talent], created_at=NOW - timedelta(days=1))
    fresh = _proposal(clean, [talent], created_at=NOW - timedelta(days=1) + timedelta(seconds=1))
    service = _service(clean)

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
    service = _service(clean)

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
    with pytest.raises(ValidationFailed) as refused:
        _public(_service(clean), empty)

    assert refused.value.details == {
        "entity": "team_request",
        "field": "proposal_id",
        "reason": NOBODY_TO_HIRE,
    }
    assert NOBODY_TO_HIRE == "Questa proposta non ha nessuno da assumere."
    assert clean.scalars(select(TeamRequest)).all() == []


def test_request_logs_a_summary_that_names_the_company(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    proposal_id = _proposal(
        clean, [_talent(clean, 1)], riassunto="ACME rifà il gestionale degli ordini."
    )

    read = _public(_service(clean), proposal_id)

    warnings = [record for record in logs.records if record.levelno == logging.WARNING]
    [record] = warnings
    logged = record.getMessage()
    assert str(read.id) in logged
    # Nothing personal: not the company, not the address, not the summary.
    for secret in ("ACME", "Acme", "wile@acme.it", "gestionale", "345"):
        assert secret not in logged
    assert clean.get(TeamRequest, read.id) is not None


@pytest.mark.parametrize(
    ("riassunto", "azienda", "expected"),
    [
        ("Acme rifà il gestionale.", "Acme S.r.l.", True),
        ("Un'azienda rifà il gestionale per ACME.", "Acme S.r.l.", True),
        ("Il gestionale di una rete di negozi.", "Rete Negozi Italia", True),
        # The legal forms are the kind of company, not which one, dotted or not.
        ("Un'azienda di logistica, una S.r.l. di Torino.", "Acme S.r.l.", False),
        ("Una srls di Torino rifà il gestionale.", "Acme Srls", False),
        ("Una SpA della logistica.", "Acme S.p.A.", False),
        ("Una snc e una sas.", "Rossi & Bianchi Snc", False),
        # Words under four letters are not the company either: «Di», «Più», «Ars».
        ("Un'app per le prenotazioni, di più.", "Di Più", False),
        ("L'ars dell'ospitalità.", "Ars Nova", False),
        ("Un portale per Nova, un'azienda di eventi.", "Ars Nova", True),
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
    service = _service(clean)
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
    service = _service(clean)
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

    # A summary that still names the company is refused before anything is written.
    with pytest.raises(InvalidState) as named:
        service.set_summary(request.id, "ACME rifà il gestionale: backend Python.", admin)
    assert named.value.message == NAMES_THE_COMPANY
    assert NAMES_THE_COMPANY == (
        "Il riassunto nomina l'azienda: correggilo prima di scrivere ai talenti."
    )
    assert service.get(request.id).riassunto == RIASSUNTO

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


# ---- the availability mail and the answer (REB-517, spec § 3.2, § 3.6) ------------------------

MISSING = UUID("00000000-0000-7000-8000-000000000000")


class Refusing(RecordingSender):
    """A provider that takes every mail but refuses the ones to `refused`."""

    def __init__(self, *refused: str) -> None:
        super().__init__()
        self.refused = set(refused)

    def send(self, mail: Mail) -> bool:
        super().send(mail)
        return mail.to not in self.refused


def _mailer(
    session: Session,
    sender: RecordingSender | None = None,
    *,
    tracker: Tracker | None = None,
    now: datetime = NOW,
) -> TeamRequestService:
    return TeamRequestService(
        session,
        settings=SETTINGS,
        sender=sender if sender is not None else RecordingSender(),
        tracker=tracker,
        now=lambda: now,
    )


def _token(mail: Mail, risposta: str = "si") -> str:
    """The raw token in the mail's link for `risposta`, on the hub's own origin."""
    found = re.search(
        rf"https://letsrebase\.com/hub/team/risposta\?t=([A-Za-z0-9_-]+)&r={risposta}\n",
        mail.text,
    )
    assert found, mail.text
    return found.group(1)


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _row(session: Session, request_id: UUID, freelancer_id: UUID) -> TeamRequestTalent:
    session.expire_all()
    row = session.scalar(
        select(TeamRequestTalent).where(
            TeamRequestTalent.request_id == request_id,
            TeamRequestTalent.freelancer_id == freelancer_id,
        )
    )
    assert row is not None
    return row


def _contacts(session: Session) -> list[dict[str, Any]]:
    actions = session.scalars(
        select(AdminAction)
        .where(AdminAction.kind == "talents_contacted")
        .order_by(AdminAction.created_at)
    ).all()
    return [action.payload for action in actions]


def test_contact_mails_every_talent_once(clean: Session) -> None:
    first = _talent(clean, 1, tariffa=Decimal("450.00"))
    second = _talent(clean, 2, tariffa=None)
    request = _public(
        _service(clean), _proposal(clean, [first, second], roles=["Backend developer", "Designer"])
    )
    admin = _user(clean, "ivan@rebase.it", role="admin")
    sender = RecordingSender()

    read = _mailer(clean, sender).contact_talents(request.id, admin, only_silent=False)

    assert read.stato == "contattata" and read.contacted_at == NOW
    assert [talent.mail_sent_at for talent in read.talenti] == [NOW, NOW]
    assert [talent.risposta for talent in read.talenti] == [None, None]
    ada, bea = sender.sent
    assert (ada.to, bea.to) == ("talento1@studio.it", "talento2@studio.it")
    for mail in (ada, bea):
        assert mail.subject == "Un progetto per te: sei disponibile?"
        assert RIASSUNTO in mail.text
        assert mail.html is not None
        # One token per talent, behind both answers.
        assert _token(mail, "si") == _token(mail, "no")
        # No company and never the client's band: the talent reads their own rate.
        for secret in ("Acme", "wile@acme.it", Band(min=500, max=650).label(), "fascia"):
            assert secret not in mail.text and secret not in mail.html, secret
    assert ada.text.startswith("Ciao Ada1,") and "Backend developer" in ada.text
    assert "450 € al giorno" in ada.text
    # No other talent: not the name, not the role.
    assert "Ada2" not in ada.text and "Lovelace" not in ada.text and "Designer" not in ada.text
    assert bea.text.startswith("Ciao Ada2,") and "Designer" in bea.text
    assert "€" not in bea.text and "Backend developer" not in bea.text
    assert _token(ada) != _token(bea)
    # Each token is at rest as its SHA-256, never as itself.
    for freelancer_id, mail in ((first, ada), (second, bea)):
        row = _row(clean, request.id, freelancer_id)
        assert row.token_hash == _sha256(_token(mail))
        assert row.mail_sent_at == NOW

    # A second «Contatta i talenti» mails nobody: writing again is «Rimanda», to the silent.
    with pytest.raises(InvalidState) as again:
        _mailer(clean, sender).contact_talents(request.id, admin, only_silent=False)

    assert again.value.message == ALREADY_CONTACTED
    assert ALREADY_CONTACTED == (
        "I talenti sono già stati contattati: rimanda a chi non ha risposto."
    )
    assert len(sender.sent) == 2
    assert _row(clean, request.id, first).token_hash == _sha256(_token(ada))
    assert _contacts(clean) == [{"talenti": 2, "only_silent": False}]
    [action] = clean.scalars(select(AdminAction).where(AdminAction.kind == "talents_contacted"))
    assert (action.entity_type, action.entity_id, action.admin_id) == (
        "team_request",
        request.id,
        admin,
    )


def test_contact_only_silent(clean: Session) -> None:
    """«Rimanda a chi non ha risposto»: a fresh link to each talent with no answer, in
    place of the one they had; a talent who answered is never mailed again."""
    talents = [_talent(clean, n) for n in (1, 2, 3)]
    request = _public(_service(clean), _proposal(clean, talents))
    admin = _user(clean, "ivan@rebase.it", role="admin")
    sender = RecordingSender()
    service = _mailer(clean, sender)
    service.contact_talents(request.id, admin, only_silent=False)
    first, second, third = sender.sent
    assert service.answer(_token(first), "si") == "si"
    assert service.answer(_token(third), "no") == "no"
    later = NOW + timedelta(days=2)

    read = _mailer(clean, sender, now=later).contact_talents(request.id, admin, only_silent=True)

    [again] = sender.sent[3:]
    assert again.to == "talento2@studio.it"
    assert _token(again) != _token(second)
    assert [talent.mail_sent_at for talent in read.talenti] == [NOW, later, NOW]
    assert [talent.risposta for talent in read.talenti] == ["si", None, "no"]
    assert read.contacted_at == NOW  # the first send's
    # The new link spent the old one, and answers.
    assert service.answer(_token(second), "si") == "invalid"
    assert _mailer(clean, now=later).answer(_token(again), "no") == "no"

    # Everyone answered: there is nobody left to write to.
    with pytest.raises(InvalidState) as nobody:
        _mailer(clean, sender, now=later).contact_talents(request.id, admin, only_silent=True)

    assert nobody.value.message == NOBODY_TO_CONTACT == "Non c'è nessun talento da contattare."
    assert len(sender.sent) == 4
    assert _contacts(clean) == [
        {"talenti": 3, "only_silent": False},
        {"talenti": 1, "only_silent": True},
    ]


def test_contact_skips_a_talent_gone_since_the_request(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    talents = [_talent(clean, n) for n in (1, 2, 3)]
    request = _public(_service(clean), _proposal(clean, talents))
    admin = _user(clean, "ivan@rebase.it", role="admin")
    clean.execute(update(Freelancer).where(Freelancer.id == talents[1]).values(deleted_at=NOW))
    clean.execute(update(Freelancer).where(Freelancer.id == talents[2]).values(stato="scartato"))
    clean.commit()
    sender = RecordingSender()
    service = _mailer(clean, sender)

    read = service.contact_talents(request.id, admin, only_silent=False)

    assert [mail.to for mail in sender.sent] == ["talento1@studio.it"]
    assert [talent.mail_sent_at for talent in read.talenti] == [NOW, None, None]
    for gone in talents[1:]:
        assert _row(clean, request.id, gone).token_hash is None
    lines = [record.getMessage() for record in logs.records]
    skipped = [line for line in lines if "not mailed" in line]
    assert len(skipped) == 2
    for line in skipped:
        assert str(request.id) in line
        for secret in ("Ada", "Lovelace", "studio.it"):
            assert secret not in line

    # «Rimanda» skips them too: with only them silent, nobody is left to write to.
    assert service.answer(_token(sender.sent[0]), "si") == "si"
    with pytest.raises(InvalidState) as nobody:
        service.contact_talents(request.id, admin, only_silent=True)
    assert nobody.value.message == NOBODY_TO_CONTACT
    assert len(sender.sent) == 1


def test_contact_refuses_a_summary_that_names_the_company(clean: Session) -> None:
    talent = _talent(clean, 1)
    request = _public(
        _service(clean),
        _proposal(clean, [talent], riassunto="ACME rifà il gestionale degli ordini."),
    )
    admin = _user(clean, "ivan@rebase.it", role="admin")
    sender = RecordingSender()

    with pytest.raises(InvalidState) as refused:
        _mailer(clean, sender).contact_talents(request.id, admin, only_silent=False)

    assert refused.value.message == NAMES_THE_COMPANY
    assert sender.sent == []
    read = _service(clean).get(request.id)
    assert read.stato == "nuova" and read.contacted_at is None
    assert _row(clean, request.id, talent).token_hash is None
    assert _contacts(clean) == []

    # Corrected, the summary goes out as the admin wrote it.
    summary = "Un'azienda di logistica rifà il gestionale degli ordini."
    _service(clean).set_summary(request.id, summary, admin)
    _mailer(clean, sender).contact_talents(request.id, admin, only_silent=False)
    [mail] = sender.sent
    assert summary in mail.text and "ACME" not in mail.text


def test_contact_needs_a_sender_and_an_open_request(clean: Session) -> None:
    talent = _talent(clean, 1)
    request = _public(_service(clean), _proposal(clean, [talent]))
    admin = _user(clean, "ivan@rebase.it", role="admin")

    with pytest.raises(InvalidState) as unsent:
        _service(clean).contact_talents(request.id, admin, only_silent=False)
    assert unsent.value.message == NO_SENDER

    _service(clean).set_status(request.id, "chiusa", admin)
    sender = RecordingSender()
    with pytest.raises(InvalidState) as closed:
        _mailer(clean, sender).contact_talents(request.id, admin, only_silent=False)
    assert closed.value.message == REQUEST_CLOSED
    assert REQUEST_CLOSED == "La richiesta è chiusa: riaprila prima di scrivere ai talenti."
    assert sender.sent == []
    assert _row(clean, request.id, talent).token_hash is None

    with pytest.raises(NotFound):
        _mailer(clean).contact_talents(MISSING, admin, only_silent=False)


def test_a_refused_mail_leaves_no_link_and_rimanda_tries_again(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    first, second = _talent(clean, 1), _talent(clean, 2)
    request = _public(_service(clean), _proposal(clean, [first, second]))
    admin = _user(clean, "ivan@rebase.it", role="admin")
    refusing = Refusing("talento2@studio.it")

    read = _mailer(clean, refusing).contact_talents(request.id, admin, only_silent=False)

    assert [talent.mail_sent_at for talent in read.talenti] == [NOW, None]
    assert _row(clean, request.id, second).token_hash is None
    assert _mailer(clean).answer(_token(refusing.sent[1]), "si") == "invalid"
    [warning] = [
        record.getMessage() for record in logs.records if record.levelno >= logging.WARNING
    ]
    assert str(request.id) in warning
    for secret in ("talento2", "studio.it", "Ada", "Lovelace"):
        assert secret not in warning

    # One talent has the mail, so the first send is spent; «Rimanda» writes to both silent.
    with pytest.raises(InvalidState):
        _mailer(clean).contact_talents(request.id, admin, only_silent=False)
    sender = RecordingSender()
    _mailer(clean, sender).contact_talents(request.id, admin, only_silent=True)
    assert [mail.to for mail in sender.sent] == ["talento1@studio.it", "talento2@studio.it"]

    # A request whose every mail was refused was never contacted: the first send is free.
    lone = _public(_service(clean), _proposal(clean, [_talent(clean, 3)]), "Tre Srl")
    _mailer(clean, Refusing("talento3@studio.it")).contact_talents(
        lone.id, admin, only_silent=False
    )
    retried = RecordingSender()
    _mailer(clean, retried).contact_talents(lone.id, admin, only_silent=False)
    assert [mail.to for mail in retried.sent] == ["talento3@studio.it"]


def test_availability_records_yes_and_no(clean: Session) -> None:
    first, second = _talent(clean, 1), _talent(clean, 2)
    request = _public(_service(clean), _proposal(clean, [first, second]))
    admin = _user(clean, "ivan@rebase.it", role="admin")
    sender = RecordingSender()
    _mailer(clean, sender).contact_talents(request.id, admin, only_silent=False)
    capture = FakeCapture()
    later = NOW + timedelta(hours=3)
    service = _mailer(clean, tracker=Tracker(capture), now=later)

    assert service.answer(_token(sender.sent[0], "si"), "si") == "si"
    assert service.answer(_token(sender.sent[1], "no"), "no") == "no"

    read = service.get(request.id)
    assert [(talent.risposta, talent.risposta_at) for talent in read.talenti] == [
        ("si", later),
        ("no", later),
    ]
    [item] = service.list_recent(stato=None, origine=None).items
    assert (item.talenti_totale, item.talenti_si) == (2, 1)
    # Counted with the answer alone: no talent, no request, no address.
    assert [(event, properties) for event, _, properties in capture.calls] == [
        (TEAM_TALENT_ANSWER, {"risposta": "si", "$process_person_profile": False}),
        (TEAM_TALENT_ANSWER, {"risposta": "no", "$process_person_profile": False}),
    ]
    assert TEAM_TALENT_ANSWER == "team_talento_risposta"


def test_availability_token_is_one_use_and_expires(clean: Session) -> None:
    talents = [_talent(clean, n) for n in (1, 2, 3)]
    request = _public(_service(clean), _proposal(clean, talents))
    admin = _user(clean, "ivan@rebase.it", role="admin")
    sender = RecordingSender()
    _mailer(clean, sender).contact_talents(request.id, admin, only_silent=False)
    ada, bea, cleo = (_token(mail) for mail in sender.sent)
    service = _mailer(clean)

    assert service.answer(ada, "si") == "si"
    # Spent: another answer, the same or the other, is refused and changes nothing.
    assert service.answer(ada, "no") == "invalid"
    assert service.answer(ada, "si") == "invalid"
    assert _row(clean, request.id, talents[0]).risposta == "si"
    # An unknown or an empty token is the same refusal.
    assert service.answer("non-un-token", "si") == "invalid"
    assert service.answer("", "no") == "invalid"
    # Thirty days from the mail, and not a second more.
    assert timedelta(days=30) == ANSWER_MAX_AGE
    last = NOW + ANSWER_MAX_AGE - timedelta(seconds=1)
    assert _mailer(clean, now=last).answer(bea, "no") == "no"
    assert _mailer(clean, now=NOW + ANSWER_MAX_AGE).answer(cleo, "si") == "invalid"
    assert _row(clean, request.id, talents[2]).risposta is None

    with pytest.raises(ValidationFailed) as unknown:
        service.answer(cleo, "forse")
    assert unknown.value.details["field"] == "risposta"


def test_the_availability_mail_carries_both_answers_and_escapes_the_summary() -> None:
    yes = "https://letsrebase.com/hub/team/risposta?t=abc&r=si"
    no = "https://letsrebase.com/hub/team/risposta?t=abc&r=no"

    mail = team_availability_mail(
        "ada@studio.it",
        nome="Ada",
        ruolo="Backend <developer>",
        riassunto="Un gestionale <script>alert(1)</script>.",
        tariffa=Decimal("1450.50"),
        yes_url=yes,
        no_url=no,
    )

    assert mail.to == "ada@studio.it"
    assert mail.subject == "Un progetto per te: sei disponibile?"
    assert f"Sono disponibile:\n{yes}\n" in mail.text
    assert f"Non sono disponibile:\n{no}\n" in mail.text
    assert "Backend <developer>" in mail.text
    assert "1.450,50 € al giorno" in mail.text
    assert "trenta giorni" in mail.text and "ti riscriviamo noi con i dettagli" in mail.text
    html = mail.html
    assert html is not None
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Backend &lt;developer&gt;" in html
    # Each link is a button and a bare URL, escaped in the attribute and in the text.
    for url in (yes, no):
        assert url not in html
        assert html.count(url.replace("&", "&amp;")) == 3
    # «Sono disponibile» on the green, «Non sono disponibile» on the brand's CTA red.
    assert AVAILABLE_GREEN == "#2b8a3e" and CTA == "#e5133e"
    green = html.index(f'bgcolor="{AVAILABLE_GREEN}"')
    red = html.index(f'bgcolor="{CTA}"')
    assert green < html.index(">Sono disponibile</a>") < red
    assert red < html.index(">Non sono disponibile</a>")

    unrated = team_availability_mail(
        "ada@studio.it",
        nome="Ada",
        ruolo="Designer",
        riassunto=None,
        tariffa=None,
        yes_url=yes,
        no_url=no,
    )
    assert "€" not in unrated.text and "None" not in unrated.text
    assert "tariffa giornaliera" in unrated.text
    whole = team_availability_mail(
        "ada@studio.it",
        nome="",
        ruolo="Designer",
        riassunto="Un gestionale.",
        tariffa=Decimal("450.00"),
        yes_url=yes,
        no_url=no,
    )
    assert whole.text.startswith("Ciao,\n") and "450 € al giorno" in whole.text


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
