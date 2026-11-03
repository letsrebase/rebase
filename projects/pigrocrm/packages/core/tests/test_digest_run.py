"""Who gets the weekly report, how often, and what is left behind when it has gone out.

Spec 2026-09-16 §3.3. `DigestRun` is the one piece of the report that *decides* rather
than composes: a space with nothing in it hears nothing, a week already sent is not sent
twice, only the people who asked for it are written to, and what happened is recorded in
one row and one timeline entry that carry counts and addresses -- never a figure.

The fixture is the shape `test_digest_build.py` records, for the same reason and one
more. `DigestService.build` opens the operational dashboard's `REPEATABLE READ` snapshot
and refuses a session that already has a transaction in progress, which `db_session`
holds open; and `DigestRun` *commits*, so a test of it has to see its own committed row
and then remove it. So the corpus is committed on the engine, every run gets a session of
its own -- as a second cron invocation would -- and the teardown deletes the `digests` and
`activities` rows the run itself wrote alongside the ones the fixture wrote.

Nothing here asserts against a mock. The sender is `RecordingSender`, which keeps the
real `Mail` objects; the tracker is a real `Tracker` over a capture that appends; the row,
the payload and the addresses are read back out of Postgres in a fresh session.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, NamedTuple
from uuid import UUID

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.digest.models import Digest
from pigrocrm.core.digest.run import DigestOutcome, DigestRun, sections_with_lines
from pigrocrm.core.digest.schemas import DigestTask, DigestTaskLine, WeeklyDigest
from pigrocrm.core.digest.service import iso_week
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.mail import Mail, RecordingSender
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.telemetry import DIGEST_SENT, Tracker
from pigrocrm.core.timetracking.models import TimeEntry

SETTINGS = Settings(_env_file=None, timezone="Europe/Rome")  # type: ignore[call-arg]
PUBLIC_URL = "https://esempio.pigrocrm.test"
SLUG = "studio-esempio"

_PREFIX = "DIGESTRUN"
_ENTITY = "digest"
_KIND = "digest.sent"

# The five sections this corpus fills: «da emettere» (six unbilled hours), «emesse» (the
# invoice of the week), «le ore», «in pipeline» (one open deal in one stage) and «da
# sistemare» (the invoice hangs off an open deal, so «fatturato ma non vinto» fires).
# «Da incassare» and «Incassate» stay empty -- the invoice is due far in the future and
# nobody has paid it.
SECTIONS_ATTESE = 5


class Corpus(NamedTuple):
    engine: Engine
    week: tuple[date, date]
    iso: str
    owner: str
    collega: str
    # The two ids the tracker must be told about, in the order `list_all` answers.
    recipients: tuple[UUID, ...]


@dataclass
class CapturaRecorded:
    """A real `Tracker` capture that appends instead of talking to PostHog."""

    chiamate: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        self.chiamate.append((event, distinct_id, properties))


class CapturaRotta:
    """A capture that raises, so `digest_sent` answers `False`. The SDK's failure is not
    the digest's: the mail has already gone and the row is already committed."""

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        raise RuntimeError("posthog irraggiungibile")


class SenderCheEsplode:
    """The one thing an `EmailSender` promises never to do. `DigestRun` must survive it
    anyway: nothing of that week may be half-written."""

    def send(self, mail: Mail) -> bool:
        raise RuntimeError("il provider ha chiuso la connessione")


class SenderSelettivo:
    """What `ResendSender` actually does on a non-2xx: it answers `False` and does not
    raise. Keeps the mails it accepted, so the assertions are about real sends."""

    def __init__(self, *rifiutati: str) -> None:
        self.rifiutati = set(rifiutati)
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        if mail.to in self.rifiutati:
            return False
        self.sent.append(mail)
        return True


def _week_scorsa() -> tuple[date, date]:
    """The last complete week, computed here and not through `previous_week`: a fixture
    that derived its window from a function under test would move with that function."""
    today = today_local(SETTINGS)
    lunedi = today - timedelta(days=today.isoweekday() - 1 + 7)
    return lunedi, lunedi + timedelta(days=6)


def _require_empty(session: Session) -> None:
    """The report has no scope narrower than the space, and neither has the recipient
    list: `list_all()` is every user there is. So a committed row left by another file
    changes what this one asserts, and a loud precondition beats an off-by-N."""
    for model in (Invoice, TimeEntry, Deal, Document, Customer, User, Digest):
        leftovers = session.execute(select(func.count(model.id))).scalar_one()
        assert leftovers == 0, (
            f"{leftovers} committed {model.__tablename__} row(s) were already present; "
            "the weekly report reads the whole space, so leftovers change it."
        )


@pytest.fixture
def corpus(db_engine: Engine) -> Iterator[Corpus]:
    """A space with something to say, and four users who differ only in whether they
    should hear it: the owner, a colleague who wants it, one who switched it off, and one
    who has been deactivated."""
    factory = session_factory(db_engine)
    da, a = _week_scorsa()
    with factory() as session:
        _require_empty(session)
        legal_entity_id = committed_default_legal_entity(session)
        open = PipelineStage(
            nome=f"{_PREFIX} Aperto", position=0, default_probability=20, type="open"
        )
        customer = Customer(legal_name=f"{_PREFIX} Cliente", country="IT", custom_fields={})
        session.add_all([open, customer])
        session.flush()

        deal = Deal(
            nome=f"{_PREFIX} Deal",
            customer_id=customer.id,
            pipeline_stage_id=open.id,
            probability=open.default_probability,
            expected_value=Decimal("5000.00"),
            custom_fields={},
        )
        session.add(deal)
        session.flush()

        def _user(nome: str, *, active: bool = True, digest: bool = True) -> User:
            user = User(
                email=f"{_PREFIX.lower()}-{nome.lower()}-{uuid7()}@example.test",
                password_hash="x",
                nome=f"{_PREFIX} {nome}",
                role="admin" if nome == "Ada" else "collaborator",
                active=active,
                weekly_digest=digest,
            )
            session.add(user)
            session.flush()
            return user

        # `list_all` orders by `nome`, and the run keeps that order, so Ada comes before
        # Bruno in `sent_to` and in the tracker's calls.
        owner = _user("Ada")
        collega = _user("Bruno")
        _user("Carla", digest=False)
        _user("Dario", active=False)

        # Issued inside the week, on an *open* deal, so «fatturato ma non vinto» fires and
        # the report has a «da sistemare» section. Due far enough ahead that it is neither
        # overdue nor inside the seven-day «in scadenza» window, whenever this runs.
        session.add(
            Invoice(
                customer_id=customer.id,
                deal_id=deal.id,
                type="invoice",
                status="issued",
                payment_status="unpaid",
                year=2026,
                number=1,
                taxable_amount=Decimal("1000.00"),
                vat_amount=Decimal("0.00"),
                stamp_duty=Decimal("0.00"),
                total=Decimal("1000.00"),
                issue_date=da + timedelta(days=1),
                due_date=a + timedelta(days=120),
                payment_date=None,
                sdi_document_type="TD01",
                currency="EUR",
                custom_fields={},
            )
        )
        session.add(
            TimeEntry(
                deal_id=deal.id,
                user_id=owner.id,
                data=da + timedelta(days=1),
                hours=Decimal("6.00"),
                description=f"{_PREFIX} lavorazione",
                billable=True,
                applied_rate=Decimal("50.000000"),
                rate_source="manual",
                applied_cost=None,
                cost_source="absent",
                invoice_line_id=None,
                custom_fields={},
            )
        )
        indirizzi = (owner.email, collega.email)
        ids = (owner.id, collega.id)
        session.commit()
    try:
        yield Corpus(db_engine, (da, a), iso_week(da), indirizzi[0], indirizzi[1], ids)
    finally:
        _pulisci(factory, iso_week(da), legal_entity_id)


def _pulisci(factory: Any, iso: str, legal_entity_id: UUID | None = None) -> None:
    """The fixture's own rows *and* whatever the run committed on top of them.

    Scoped to what this file can have written: the prefix for the corpus, and the one ISO
    week the runs are given for the `digests` row and the timeline entries hanging off it.
    A blanket `delete(Digest)` would take another file's row with it, and the whole point
    of `_require_empty` is that nobody's leftovers are anybody else's business.
    """
    with factory() as session:
        corpus_customers = select(Customer.id).where(Customer.legal_name.like(f"{_PREFIX} %"))
        corpus_deals = select(Deal.id).where(Deal.nome.like(f"{_PREFIX} %"))
        weeks = select(Digest.id).where(Digest.week == iso)
        session.execute(
            delete(Activity).where(Activity.entity_type == _ENTITY, Activity.entity_id.in_(weeks))
        )
        session.execute(delete(Activity).where(Activity.entity_id.in_(corpus_deals)))
        session.execute(delete(Digest).where(Digest.week == iso))
        session.execute(delete(TimeEntry).where(TimeEntry.deal_id.in_(corpus_deals)))
        session.execute(delete(Invoice).where(Invoice.customer_id.in_(corpus_customers)))
        session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
        session.execute(delete(Customer).where(Customer.legal_name.like(f"{_PREFIX} %")))
        session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
        session.execute(delete(Task).where(Task.title.like(f"{_PREFIX} %")))
        session.execute(delete(User).where(User.nome.like(f"{_PREFIX} %")))
        remove_legal_entity(session, legal_entity_id)
        session.commit()


def _run(
    engine: Engine,
    *,
    week: tuple[date, date],
    owner: str,
    sender: Any = None,
    tracker: Tracker | None = None,
    forza: bool = False,
    dry_run: bool = False,
) -> DigestOutcome:
    """One run, in a session of its own -- which is what a second cron invocation is.

    The session is checked here, on every run this file makes, rather than in one test of
    its own: whichever of the five answers comes back, `DigestRun` must hand the session
    over with no transaction left open. The cron holds one session per space, and a
    connection sitting idle-in-transaction across a whole run is how autovacuum stops
    working -- a defect no assertion about the outcome would ever notice.
    """
    with session_factory(engine)() as session:
        run = DigestRun(session, SETTINGS, sender=sender, tracker=tracker, public_url=PUBLIC_URL)
        outcome = run.send_for_space(SLUG, owner, week, forza=forza, dry_run=dry_run)
        assert not session.in_transaction(), (
            f"«{outcome.outcome}» left a transaction open on the session it was handed"
        )
        return outcome


class Riga(NamedTuple):
    id: UUID
    sent_to: list[str]
    occurred_at: datetime


def _line(engine: Engine, iso: str) -> Riga | None:
    with session_factory(engine)() as session:
        row = session.execute(select(Digest).where(Digest.week == iso)).scalar_one_or_none()
        return None if row is None else Riga(row.id, list(row.sent_to), row.occurred_at)


class Traccia(NamedTuple):
    kind: str
    entity_id: UUID
    actor_id: UUID | None
    actor_type: str
    payload: dict[str, Any]


def _task(engine: Engine) -> list[Traccia]:
    with session_factory(engine)() as session:
        rows = (
            session.execute(select(Activity).where(Activity.entity_type == _ENTITY)).scalars().all()
        )
        return [
            Traccia(r.kind, r.entity_id, r.actor_id, r.actor_type, dict(r.payload)) for r in rows
        ]


# --- the count of sections that had something in them ---------------------------------


def _digest_empty(**sovrascritture: Any) -> WeeklyDigest:
    fields: dict[str, Any] = {
        "week": "2026-W37",
        "da": date(2026, 9, 7),
        "a": date(2026, 9, 13),
        "overdue": [],
        "in_due_date": [],
        "won_to_invoice": 0,
        "hours_uninvoiced": Decimal("0.00"),
        "accrued_value": Decimal("0.00"),
        "emesse": [],
        "total_month_current": Decimal("0.00"),
        "total_month_previous": Decimal("0.00"),
        "incassate": [],
        "hours": None,
        "pipeline": [],
        "deal_mossi": [],
        "offers_pending": [],
        "signals": [],
    }
    return WeeklyDigest(**(fields | sovrascritture))


def _invoice() -> dict[str, Any]:
    return {
        "invoice_id": uuid7(),
        "number": "2026/1",
        "customer": "Cliente",
        "amount": Decimal("100.00"),
        "data": date(2026, 9, 8),
        "status": "issued",
        "payment_status": "unpaid",
    }


def test_no_section_has_rows_in_an_empty_week() -> None:
    assert sections_with_lines(_digest_empty()) == 0


@pytest.mark.parametrize(
    "sovrascrittura",
    [
        {"overdue": [_invoice()]},
        {"in_due_date": [_invoice()]},
        {"won_to_invoice": 1},
        {"hours_uninvoiced": Decimal("0.25")},
        # The third of «Da emettere»'s figures. `digest_mail` prints that section's second
        # line on `ore_non_fatturate or valore_maturato`, so accrued value with no unbilled
        # hours behind it is still a section the recipient saw.
        {"accrued_value": Decimal("1.00")},
        {"emesse": [_invoice()]},
        {"incassate": [_invoice()]},
        {"pipeline": [{"stage_name": "Aperto", "number": 1, "total_value": Decimal("1.00")}]},
        {
            "deal_mossi": [
                {
                    "deal_id": uuid7(),
                    "title": "D",
                    "stage_name": "Vinto",
                    "quando": date(2026, 9, 8),
                }
            ]
        },
        {"offers_pending": [{"document_id": uuid7(), "title": "O", "days": 3}]},
        {"signals": [{"code": "c", "label": "e", "count": 1, "link": "/x"}]},
    ],
)
def test_one_filled_list_lights_exactly_one_section(sovrascrittura: dict[str, Any]) -> None:
    """Eleven ways to fill one of the seven sections. Each lights one and only one, which
    is what makes the count a count of *sections* rather than of lists."""
    assert sections_with_lines(_digest_empty(**sovrascrittura)) == 1


def test_the_three_figures_of_da_emettere_are_one_section() -> None:
    """«Da emettere» is one heading over two lines and three figures. `digest_mail` prints
    it when any of the three is set, and this count has to agree or PostHog is told about
    a section the recipient never saw -- or not told about one they did."""
    for fields in (
        {"won_to_invoice": 2},
        {"hours_uninvoiced": Decimal("3.00")},
        {"accrued_value": Decimal("150.00")},
        {
            "won_to_invoice": 2,
            "hours_uninvoiced": Decimal("3.00"),
            "accrued_value": Decimal("150.00"),
        },
    ):
        assert sections_with_lines(_digest_empty(**fields)) == 1, fields


def test_the_two_halves_of_a_section_still_count_once() -> None:
    """«Da incassare» is two lists and one section: a week with both must not be reported
    as having had more to say than a week with one."""
    doppia = _digest_empty(overdue=[_invoice()], in_due_date=[_invoice()])
    assert sections_with_lines(doppia) == 1


def test_the_task_of_the_week_count_as_one_section() -> None:
    riga = DigestTaskLine(task_id=uuid7(), title="Telefonare", due_date=date(2026, 9, 9))
    con = _digest_empty(
        attivita=DigestTask(in_week=[riga], in_week_total=1),
    )

    assert sections_with_lines(con) == 1
    assert con.empty is False


def test_a_week_with_everything_lights_all_seven() -> None:
    pieno = _digest_empty(
        overdue=[_invoice()],
        won_to_invoice=2,
        issued=[_invoice()],
        paid=[_invoice()],
        hours={
            "da": date(2026, 9, 7),
            "a": date(2026, 9, 13),
            "days": [],
            "days_without_hours": [],
            "total_hours": Decimal("6.00"),
        },
        pipeline=[{"stage_name": "Aperto", "number": 1, "total_value": Decimal("1.00")}],
        signals=[{"code": "c", "label": "e", "count": 1, "link": "/x"}],
    )
    assert sections_with_lines(pieno) == 7


# --- the send -------------------------------------------------------------------------


def test_the_report_goes_to_everyone_who_asked_for_it_and_to_nobody_else(
    corpus: Corpus,
) -> None:
    """§3.3: the recipients are the active users who have not switched the report off.
    Carla asked not to receive it and Dario has been deactivated; neither is written to,
    and neither appears in the row that says who was."""
    sender = RecordingSender()
    outcome = _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=sender)

    assert outcome == DigestOutcome(slug=SLUG, outcome="sent", week=corpus.iso, recipients=2)
    assert [mail.to for mail in sender.sent] == [corpus.owner, corpus.collega]
    assert all(mail.subject for mail in sender.sent)
    # The real mail, not a stub: the week's own invoice is in the body it carries.
    assert all("2026/1" in (mail.html or "") for mail in sender.sent)

    riga = _line(corpus.engine, corpus.iso)
    assert riga is not None
    assert riga.sent_to == [corpus.owner, corpus.collega]


def test_each_recipient_gets_their_own_task_of_the_week(corpus: Corpus) -> None:
    """REB-726: the digest lists an attività due this week that is assigned to the
    recipient, and not one assigned to somebody else. The overdue come first, a
    completed one is on neither list, and an undated one belongs to the Oggi page."""
    today = today_local(SETTINGS)
    ada, bruno = corpus.recipients
    factory = session_factory(corpus.engine)
    with factory() as session:
        session.add_all(
            [
                Task(
                    title=f"{_PREFIX} Telefonare a Rossi",
                    due_date=today + timedelta(days=2),
                    status="open",
                    origin="manual",
                    assigned_to=ada,
                    custom_fields={},
                ),
                Task(
                    title=f"{_PREFIX} Preventivo in ritardo",
                    due_date=today - timedelta(days=3),
                    status="open",
                    origin="manual",
                    assigned_to=ada,
                    custom_fields={},
                ),
                Task(
                    title=f"{_PREFIX} Inviare il contratto a Bianchi",
                    due_date=today + timedelta(days=2),
                    status="open",
                    origin="manual",
                    assigned_to=bruno,
                    custom_fields={},
                ),
                Task(
                    title=f"{_PREFIX} Gia fatta",
                    due_date=today + timedelta(days=1),
                    status="completed",
                    completed_at=today,
                    origin="manual",
                    assigned_to=ada,
                    custom_fields={},
                ),
                Task(
                    title=f"{_PREFIX} Senza data",
                    due_date=None,
                    status="open",
                    origin="manual",
                    assigned_to=ada,
                    custom_fields={},
                ),
                Task(
                    title=f"{_PREFIX} Fra due settimane",
                    due_date=today + timedelta(days=14),
                    status="open",
                    origin="manual",
                    assigned_to=ada,
                    custom_fields={},
                ),
            ]
        )
        session.commit()

    sender = RecordingSender()
    _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=sender)
    di_ada, di_bruno = sender.sent

    assert "Telefonare a Rossi" in di_ada.text
    assert "Inviare il contratto a Bianchi" not in di_ada.text
    assert "Inviare il contratto a Bianchi" in di_bruno.text
    assert "Telefonare a Rossi" not in di_bruno.text
    assert "Preventivo in ritardo — " in di_ada.text and "3 giorni di ritardo" in di_ada.text
    # Late before upcoming, and none of the three that do not belong in a week's view.
    assert di_ada.text.index("Preventivo in ritardo") < di_ada.text.index("Telefonare a Rossi")
    for outside in ("Gia fatta", "Senza data", "Fra due settimane"):
        assert outside not in di_ada.text
    assert f"{PUBLIC_URL}/app/today?from=digest" in di_ada.text
    # A person with nothing assigned gets no section, not an empty heading.
    assert "Da fare" in di_ada.text and "Da fare" in di_bruno.text


def test_to_long_list_of_late_task_is_cut_and_says_how_many_more(corpus: Corpus) -> None:
    """The mail never grows with the backlog: ten lines, then a count, then the link."""
    today = today_local(SETTINGS)
    ada = corpus.recipients[0]
    with session_factory(corpus.engine)() as session:
        session.add_all(
            Task(
                title=f"{_PREFIX} Arretrato {n:02d}",
                due_date=today - timedelta(days=1 + n),
                status="open",
                origin="manual",
                assigned_to=ada,
                custom_fields={},
            )
            for n in range(13)
        )
        session.commit()

    sender = RecordingSender()
    _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=sender)
    di_ada = sender.sent[0]

    assert di_ada.text.count("Arretrato") == 10
    assert "…e 3 altre in ritardo" in di_ada.text
    assert f"{PUBLIC_URL}/app/today?from=digest" in di_ada.text


def test_a_recipient_with_nothing_assigned_has_no_da_fare_section(corpus: Corpus) -> None:
    sender = RecordingSender()
    _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=sender)

    assert all("Da fare" not in mail.text for mail in sender.sent)


def test_the_timeline_entry_carries_counts_and_never_a_figure(corpus: Corpus) -> None:
    """The record of the week is a count of who and of how much there was to say. An
    amount, an invoice number or an address in the payload would put the space's money
    into a row read by anybody who can read the timeline."""
    _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=RecordingSender(),
    )

    lines = _task(corpus.engine)
    assert len(lines) == 1
    traccia = lines[0]
    riga = _line(corpus.engine, corpus.iso)
    assert riga is not None
    assert traccia.kind == _KIND
    assert traccia.entity_id == riga.id
    assert traccia.actor_type == "system"
    assert traccia.payload == {
        "week": corpus.iso,
        "recipients": 2,
        "sections": SECTIONS_ATTESE,
    }


def test_posthog_hears_once_per_recipient_after_the_commit(corpus: Corpus) -> None:
    """One event per person, under that person's own id, with the same section count the
    timeline recorded."""
    cattura = CapturaRecorded()
    _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=RecordingSender(),
        tracker=Tracker(cattura),
    )

    assert [nome for nome, _, _ in cattura.chiamate] == [DIGEST_SENT, DIGEST_SENT]
    assert [ident for _, ident, _ in cattura.chiamate] == [str(i) for i in corpus.recipients]
    for _, _, properties in cattura.chiamate:
        assert properties["week"] == corpus.iso
        assert properties["sections"] == SECTIONS_ATTESE


def test_a_tracker_that_refuses_changes_nothing(corpus: Corpus) -> None:
    """The capture is called after the commit, so its failure cannot reach the row."""
    sender = RecordingSender()
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=sender,
        tracker=Tracker(CapturaRotta()),
    )
    assert outcome.outcome == "sent"
    assert len(sender.sent) == 2
    assert _line(corpus.engine, corpus.iso) is not None


def test_without_a_resend_key_nothing_is_sent_and_nothing_is_recorded(corpus: Corpus) -> None:
    """`sender_from_settings` answers `None` on an installation with no key: there is no
    transport, so no mail was attempted and no week happened.

    This used to record the row, the timeline entry and the PostHog events as though the
    report had been delivered -- which made the week `already_sent` for ever and reported N
    deliveries of a mail nobody was ever sent. It is the same nothing `send_rejected`
    leaves, and for the same reason: the run after the key is configured has to send it.
    """
    cattura = CapturaRecorded()
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=None,
        tracker=Tracker(cattura),
    )
    assert outcome == DigestOutcome(
        slug=SLUG, outcome="skipped", week=corpus.iso, reason="send_not_configured"
    )
    assert _line(corpus.engine, corpus.iso) is None
    assert _task(corpus.engine) == []
    assert cattura.chiamate == []

    # And the run after the key is configured sends the week, to everybody.
    configured = RecordingSender()
    ritentato = _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=configured)
    assert ritentato.outcome == "sent"
    assert [mail.to for mail in configured.sent] == [corpus.owner, corpus.collega]


# --- what the provider refused --------------------------------------------------------


def test_only_the_addresses_the_provider_took_are_recorded(corpus: Corpus) -> None:
    """`EmailSender.send` answers `False` and does not raise, so a refusal is silent
    unless it is read. A report that never left must not appear in `sent_to`, must not
    be counted, and must not be reported to PostHog as delivered."""
    sender = SenderSelettivo(corpus.collega)
    cattura = CapturaRecorded()
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=sender,
        tracker=Tracker(cattura),
    )

    assert outcome == DigestOutcome(slug=SLUG, outcome="sent", week=corpus.iso, recipients=1)
    assert [mail.to for mail in sender.sent] == [corpus.owner]
    riga = _line(corpus.engine, corpus.iso)
    assert riga is not None
    assert riga.sent_to == [corpus.owner]
    assert [traccia.payload["recipients"] for traccia in _task(corpus.engine)] == [1]
    assert [ident for _, ident, _ in cattura.chiamate] == [str(corpus.recipients[0])]


def test_a_week_nobody_received_is_left_to_be_retried(corpus: Corpus) -> None:
    """Every address refused. Writing the row anyway would make the week `already_sent`
    for ever on the strength of mails that never arrived, and Monday's outage would cost
    the space its report permanently. Nothing is written, so a rerun tries again."""
    sender = SenderSelettivo(corpus.owner, corpus.collega)
    cattura = CapturaRecorded()
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=sender,
        tracker=Tracker(cattura),
    )

    assert outcome == DigestOutcome(
        slug=SLUG, outcome="skipped", week=corpus.iso, reason="send_rejected"
    )
    assert sender.sent == []
    assert cattura.chiamate == []
    assert _line(corpus.engine, corpus.iso) is None
    assert _task(corpus.engine) == []

    # And the retry, against a provider that has come back, sends the whole week.
    of_new = RecordingSender()
    ritentato = _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=of_new)
    assert ritentato.outcome == "sent"
    assert [mail.to for mail in of_new.sent] == [corpus.owner, corpus.collega]


# --- once per week --------------------------------------------------------------------


def test_the_second_run_of_the_same_week_sends_nothing(corpus: Corpus) -> None:
    """A cron that runs twice, or two hosts that both wake up, must send once."""
    first = RecordingSender()
    _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=first)
    second = RecordingSender()
    cattura = CapturaRecorded()
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=second,
        tracker=Tracker(cattura),
    )

    assert outcome == DigestOutcome(slug=SLUG, outcome="already_sent", week=corpus.iso)
    assert second.sent == []
    assert cattura.chiamate == []
    assert len(_task(corpus.engine)) == 1


def test_forza_sends_again_and_moves_the_row_instead_of_writing_a_second(
    corpus: Corpus,
) -> None:
    """`week` is unique, so a resend has to be an update. The row keeps saying who
    received it and when it last went out."""
    _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=RecordingSender(),
    )
    before = _line(corpus.engine, corpus.iso)
    assert before is not None

    of_new = RecordingSender()
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=of_new,
        forza=True,
    )

    assert outcome.outcome == "sent"
    assert [mail.to for mail in of_new.sent] == [corpus.owner, corpus.collega]
    after = _line(corpus.engine, corpus.iso)
    assert after is not None
    assert after.id == before.id
    assert after.sent_to == [corpus.owner, corpus.collega]
    assert after.occurred_at >= before.occurred_at
    # Two runs, two timeline entries, one row: the record of *what happened* is the
    # timeline's business and the record of *this week* is the row's.
    assert len(_task(corpus.engine)) == 2


# --- the runs that write nothing ------------------------------------------------------


def test_to_space_with_only_an_open_task_still_gets_its_from_fare(db_engine: Engine) -> None:
    """An attivita needs no customer, deal, invoice or hour, so a space of nothing but
    to-dos is not empty: its owner is mailed the week's «Da fare», and a space whose
    only attivita is closed hears nothing (REB-726, Greptile on #635)."""
    factory = session_factory(db_engine)
    today = today_local(SETTINGS)
    with factory() as session:
        _require_empty(session)
        owner = User(
            email=f"{_PREFIX.lower()}-solo-{uuid7()}@example.test",
            password_hash="x",
            nome=f"{_PREFIX} Solo",
            role="admin",
        )
        session.add(owner)
        session.flush()
        address = owner.email
        session.add(
            Task(
                title=f"{_PREFIX} Chiamare Rossi",
                due_date=today - timedelta(days=1),
                status="open",
                origin="manual",
                assigned_to=owner.id,
                custom_fields={},
            )
        )
        session.commit()
    week = _week_scorsa()
    sender = RecordingSender()
    try:
        outcome = _run(db_engine, week=week, owner=address, sender=sender)
        assert outcome == DigestOutcome(
            slug=SLUG, outcome="sent", week=iso_week(week[0]), recipients=1
        )
        assert "Chiamare Rossi" in sender.sent[0].text

        with factory() as session:
            session.execute(
                update(Task)
                .where(Task.title == f"{_PREFIX} Chiamare Rossi")
                .values(status="completed", completed_at=today)
            )
            # The first run's timeline entry goes with its row, before the row does:
            # `_pulisci` finds an entry through the week's `digests` id.
            session.execute(delete(Activity).where(Activity.entity_type == _ENTITY))
            session.execute(delete(Digest).where(Digest.week == iso_week(week[0])))
            session.commit()
        closed = _run(db_engine, week=week, owner=address, sender=RecordingSender())
        assert closed.outcome == "empty"
    finally:
        _pulisci(factory, iso_week(week[0]))


def test_an_empty_space_hears_nothing(db_engine: Engine) -> None:
    """§2: «silenzio per gli spazi vuoti». No customer, no deal, no invoice, no hour --
    and so no mail, no row, and nothing for PostHog to hear about either."""
    factory = session_factory(db_engine)
    with factory() as session:
        _require_empty(session)
        owner = User(
            email=f"{_PREFIX.lower()}-solo-{uuid7()}@example.test",
            password_hash="x",
            nome=f"{_PREFIX} Solo",
            role="admin",
        )
        session.add(owner)
        session.flush()
        address = owner.email
        session.commit()
    week = _week_scorsa()
    sender = RecordingSender()
    cattura = CapturaRecorded()
    try:
        outcome = _run(
            db_engine,
            week=week,
            owner=address,
            sender=sender,
            tracker=Tracker(cattura),
        )
        assert outcome == DigestOutcome(slug=SLUG, outcome="empty", week=iso_week(week[0]))
        assert sender.sent == []
        assert cattura.chiamate == []
        assert _line(db_engine, iso_week(week[0])) is None
        assert _task(db_engine) == []
    finally:
        _pulisci(factory, iso_week(week[0]))


def test_a_space_emptied_by_soft_deletion_hears_nothing(db_engine: Engine) -> None:
    """Deleting in this product sets `deleted_at`; core applies no global filter over it.

    A probe that counted rows would find a space whose customer, deal and hours were all
    deleted last year, decide it was not empty, and mail it a report with every section
    blank -- the mail §2's «silenzio per gli spazi vuoti» exists to prevent.

    A customer, a deal and a time entry, and deliberately no invoice:
    `ck_invoices_no_delete_once_consumed` forbids soft-deleting an invoice that has taken
    a number, so a space that has ever issued one is never empty again by this test's
    definition -- which is the register being right, not this probe being wrong.
    """
    factory = session_factory(db_engine)
    week = _week_scorsa()
    with factory() as session:
        _require_empty(session)
        when = datetime.now(UTC)
        stage = PipelineStage(
            nome=f"{_PREFIX} Fase", position=0, default_probability=10, type="open"
        )
        customer = Customer(
            legal_name=f"{_PREFIX} Cancellato",
            country="IT",
            custom_fields={},
            deleted_at=when,
        )
        owner = User(
            email=f"{_PREFIX.lower()}-resto-{uuid7()}@example.test",
            password_hash="x",
            nome=f"{_PREFIX} Resto",
            role="admin",
        )
        session.add_all([stage, customer, owner])
        session.flush()
        deal = Deal(
            nome=f"{_PREFIX} Deal cancellato",
            customer_id=customer.id,
            pipeline_stage_id=stage.id,
            probability=stage.default_probability,
            expected_value=Decimal("100.00"),
            custom_fields={},
            deleted_at=when,
        )
        session.add(deal)
        session.flush()
        session.add(
            TimeEntry(
                deal_id=deal.id,
                user_id=owner.id,
                data=week[0],
                hours=Decimal("2.00"),
                description=f"{_PREFIX} ore cancellate",
                billable=True,
                applied_rate=Decimal("50.000000"),
                rate_source="manual",
                applied_cost=None,
                cost_source="absent",
                invoice_line_id=None,
                custom_fields={},
                deleted_at=when,
            )
        )
        address = owner.email
        session.commit()

    sender = RecordingSender()
    try:
        outcome = _run(db_engine, week=week, owner=address, sender=sender)
        assert outcome == DigestOutcome(slug=SLUG, outcome="empty", week=iso_week(week[0]))
        assert sender.sent == []
        assert _line(db_engine, iso_week(week[0])) is None
    finally:
        _pulisci(factory, iso_week(week[0]))


def test_a_rehearsal_builds_the_report_and_leaves_nothing_behind(corpus: Corpus) -> None:
    """`--dry-run` answers what *would* go out, with the count, so the operator can read
    it before letting it go. Nothing is sent, written, recorded or tracked.

    `sent` with the count, and not the `skipped` an installation with no key gets: a
    rehearsal *chose* not to send, and the operator asking for one is reading the answer."""
    sender = RecordingSender()
    cattura = CapturaRecorded()
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=sender,
        tracker=Tracker(cattura),
        dry_run=True,
    )

    assert outcome == DigestOutcome(slug=SLUG, outcome="sent", week=corpus.iso, recipients=2)
    assert sender.sent == []
    assert cattura.chiamate == []
    assert _line(corpus.engine, corpus.iso) is None
    assert _task(corpus.engine) == []


def test_the_report_is_built_with_no_transaction_in_progress(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reads that decide whether to send autobegin a transaction, and the operational
    dashboard refuses to open its `REPEATABLE READ` snapshot on a session that already
    has one. So whatever `DigestRun` does between its reads and `DigestService.build`, the
    session has to be free of a transaction when `build` starts, for every scope it builds
    a report in. Asserted at that call rather than through the outcome, which an
    over-broad `except` would turn into a quiet `skipped`."""
    from pigrocrm.core.digest.service import DigestService

    in_transaction: list[bool] = []
    build = DigestService.build

    def recording_build(self: DigestService, *args: Any, **kwargs: Any) -> WeeklyDigest:
        in_transaction.append(self.session.in_transaction())
        return build(self, *args, **kwargs)

    monkeypatch.setattr(DigestService, "build", recording_build)
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=RecordingSender(),
        dry_run=True,
    )

    assert outcome.outcome == "sent"
    assert in_transaction != [] and not any(in_transaction)


def test_a_space_where_nobody_wants_it_is_left_alone(corpus: Corpus) -> None:
    """Everyone switched it off. There is something to report and nobody to report it to,
    which is a different answer from «this space is empty» and leaves no row either."""
    with session_factory(corpus.engine)() as session:
        for user in session.execute(select(User)).scalars():
            user.weekly_digest = False
        session.commit()

    sender = RecordingSender()
    outcome = _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=sender)

    assert outcome == DigestOutcome(slug=SLUG, outcome="no_recipient", week=corpus.iso)
    assert sender.sent == []
    assert _line(corpus.engine, corpus.iso) is None


def test_an_unknown_owner_is_skipped(corpus: Corpus) -> None:
    """The actor is the space's owner. Without one there is nobody to read as, and a
    `system` actor with an invented role would be an escalation."""
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner="nessuno@example.test",
        sender=RecordingSender(),
    )
    assert outcome.outcome == "skipped"
    assert outcome.reason
    assert _line(corpus.engine, corpus.iso) is None


def test_a_deactivated_owner_is_skipped(corpus: Corpus) -> None:
    """Deactivating somebody is, everywhere else in this product, the moment the CRM stops
    acting on their behalf. A cron that kept reading their space would be the exception."""
    with session_factory(corpus.engine)() as session:
        user = session.execute(select(User).where(User.email == corpus.owner)).scalar_one()
        user.active = False
        session.commit()

    sender = RecordingSender()
    outcome = _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=sender)
    assert outcome.outcome == "skipped"
    assert sender.sent == []
    assert _line(corpus.engine, corpus.iso) is None


def test_a_send_that_blows_up_names_the_type_and_writes_nothing(corpus: Corpus) -> None:
    """The motive is the exception's *type*: its message can carry the address the send
    failed on, and this string is printed in a log and returned to a command."""
    cattura = CapturaRecorded()
    outcome = _run(
        corpus.engine,
        week=corpus.week,
        owner=corpus.owner,
        sender=SenderCheEsplode(),
        tracker=Tracker(cattura),
    )

    assert outcome.outcome == "skipped"
    assert outcome.reason == "RuntimeError"
    assert "connessione" not in outcome.reason
    assert cattura.chiamate == []
    assert _line(corpus.engine, corpus.iso) is None
    assert _task(corpus.engine) == []


# --- one report per distinct scope (REB-633, spec 2026-10-03 §4) ----------------------


def test_to_scoped_owner_does_not_empty_the_week_for_everyone_else(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The space's emptiness is asked as the space, not as the titolare: Ada scoped to an
    azienda with no records still has Bruno's unscoped report built and sent, and her
    own is built inside her scope (Greptile on PR #513)."""
    from sqlalchemy import text

    from pigrocrm.core.auth.models import UserLegalEntity
    from pigrocrm.core.db.scope import SCOPE_SETTING
    from pigrocrm.core.digest.service import DigestService
    from pigrocrm.core.legal_entities.models import LegalEntity

    factory = session_factory(corpus.engine)
    with factory() as session:
        empty = LegalEntity(nome=f"{_PREFIX} vuota", legal_name=f"{_PREFIX} Vuota", country="GB")
        session.add(empty)
        session.flush()
        ada = session.get(User, corpus.recipients[0])
        assert ada is not None
        ada.limited_scope = True
        session.add(UserLegalEntity(user_id=ada.id, legal_entity_id=empty.id))
        session.commit()
        empty_id = empty.id

    bound: list[str] = []
    original = DigestService.build

    def _recording(self: DigestService, actor: Any, week: tuple[date, date]) -> WeeklyDigest:
        digest = original(self, actor, week)
        bound.append(
            self.session.execute(
                text(f"SELECT current_setting('{SCOPE_SETTING}', true)")
            ).scalar_one()
        )
        return digest

    monkeypatch.setattr(DigestService, "build", _recording)
    sender = RecordingSender()
    try:
        outcome = _run(corpus.engine, week=corpus.week, owner=corpus.owner, sender=sender)
        assert outcome == DigestOutcome(slug=SLUG, outcome="sent", week=corpus.iso, recipients=2)
        assert bound == [str(empty_id), "*"]
        assert [mail.to for mail in sender.sent] == [corpus.owner, corpus.collega]
    finally:
        with factory() as session:
            session.execute(delete(LegalEntity).where(LegalEntity.id == empty_id))
            session.commit()


def test_a_scoped_recipient_gets_a_report_built_inside_their_scope(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bruno may see one azienda, Ada the whole space: the report is rendered twice, once
    with `*` bound and once with Bruno's azienda, each sent to its own group, and the
    session is handed back inside the titolare's scope."""
    from sqlalchemy import text

    from pigrocrm.core.auth.models import UserLegalEntity
    from pigrocrm.core.db.scope import SCOPE_SETTING
    from pigrocrm.core.digest.service import DigestService

    factory = session_factory(corpus.engine)
    with factory() as session:
        legal_entity_id = session.execute(select(Customer.legal_entity_id)).scalars().first()
        assert legal_entity_id is not None
        bruno = session.get(User, corpus.recipients[1])
        assert bruno is not None
        bruno.limited_scope = True
        session.add(UserLegalEntity(user_id=bruno.id, legal_entity_id=legal_entity_id))
        session.commit()

    bound: list[str] = []
    original = DigestService.build
    probed: list[str] = []
    original_probe = DigestRun._space_empty

    def _recording_probe(self: DigestRun) -> bool:
        probed.append(
            self.session.execute(
                text(f"SELECT current_setting('{SCOPE_SETTING}', true)")
            ).scalar_one()
        )
        return original_probe(self)

    monkeypatch.setattr(DigestRun, "_space_empty", _recording_probe)

    def _recording(self: DigestService, actor: Any, week: tuple[date, date]) -> WeeklyDigest:
        digest = original(self, actor, week)
        bound.append(
            self.session.execute(
                text(f"SELECT current_setting('{SCOPE_SETTING}', true)")
            ).scalar_one()
        )
        return digest

    monkeypatch.setattr(DigestService, "build", _recording)
    sender = RecordingSender()
    with factory() as session:
        run = DigestRun(session, SETTINGS, sender=sender, tracker=None, public_url=PUBLIC_URL)
        outcome = run.send_for_space(SLUG, corpus.owner, corpus.week)
        assert not session.in_transaction()
        back = session.execute(
            text(f"SELECT current_setting('{SCOPE_SETTING}', true)")
        ).scalar_one()

    assert outcome.outcome == "sent" and outcome.recipients == 2
    # The emptiness probe, the first read of a policied table, already ran as «tutte».
    assert probed == ["*"]
    assert bound == ["*", str(legal_entity_id)]
    assert [mail.to for mail in sender.sent] == [corpus.owner, corpus.collega]
    # The titolare's scope is what the session carries once the groups are done.
    assert back == "*"
