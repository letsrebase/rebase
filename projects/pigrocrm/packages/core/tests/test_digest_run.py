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
from fakes.azienda_fixtures import committed_default_azienda, remove_azienda
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.digest.models import Digest
from pigrocrm.core.digest.run import DigestOutcome, DigestRun, sezioni_con_righe
from pigrocrm.core.digest.schemas import WeeklyDigest
from pigrocrm.core.digest.service import iso_week
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.mail import Mail, RecordingSender
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.telemetry import DIGEST_SENT, Tracker
from pigrocrm.core.timetracking.models import TimeEntry

SETTINGS = Settings(_env_file=None, timezone="Europe/Rome")  # type: ignore[call-arg]
PUBLIC_URL = "https://esempio.pigrocrm.test"
SLUG = "studio-esempio"

_PREFIX = "DIGESTRUN"
_ENTITA = "digest"
_KIND = "digest.inviato"

# The five sections this corpus fills: «da emettere» (six unbilled hours), «emesse» (the
# invoice of the week), «le ore», «in pipeline» (one open deal in one stage) and «da
# sistemare» (the invoice hangs off an open deal, so «fatturato ma non vinto» fires).
# «Da incassare» and «Incassate» stay empty -- the invoice is due far in the future and
# nobody has paid it.
SEZIONI_ATTESE = 5


class Corpus(NamedTuple):
    engine: Engine
    settimana: tuple[date, date]
    iso: str
    titolare: str
    collega: str
    # The two ids the tracker must be told about, in the order `list_all` answers.
    destinatari: tuple[UUID, ...]


@dataclass
class CapturaRegistrata:
    """A real `Tracker` capture that appends instead of talking to PostHog."""

    chiamate: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        self.chiamate.append((event, distinct_id, properties))


class CapturaRotta:
    """A capture that raises, so `digest_sent` answers `False`. The SDK's failure is not
    the digest's: the mail has already gone and the row is already committed."""

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        raise RuntimeError("posthog irraggiungibile")


class MittenteCheEsplode:
    """The one thing an `EmailSender` promises never to do. `DigestRun` must survive it
    anyway: nothing of that week may be half-written."""

    def send(self, mail: Mail) -> bool:
        raise RuntimeError("il provider ha chiuso la connessione")


class MittenteSelettivo:
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


def _settimana_scorsa() -> tuple[date, date]:
    """The last complete week, computed here and not through `previous_week`: a fixture
    that derived its window from a function under test would move with that function."""
    oggi = today_local(SETTINGS)
    lunedi = oggi - timedelta(days=oggi.isoweekday() - 1 + 7)
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
    da, a = _settimana_scorsa()
    with factory() as session:
        _require_empty(session)
        azienda_id = committed_default_azienda(session)
        aperto = PipelineStage(
            nome=f"{_PREFIX} Aperto", posizione=0, probabilita_default=20, tipo="open"
        )
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        session.add_all([aperto, customer])
        session.flush()

        deal = Deal(
            nome=f"{_PREFIX} Deal",
            customer_id=customer.id,
            pipeline_stage_id=aperto.id,
            probabilita=aperto.probabilita_default,
            valore_previsto=Decimal("5000.00"),
            custom_fields={},
        )
        session.add(deal)
        session.flush()

        def _utente(nome: str, *, attivo: bool = True, digest: bool = True) -> User:
            user = User(
                email=f"{_PREFIX.lower()}-{nome.lower()}-{uuid7()}@example.test",
                password_hash="x",
                nome=f"{_PREFIX} {nome}",
                ruolo="admin" if nome == "Ada" else "collaboratore",
                attivo=attivo,
                digest_settimanale=digest,
            )
            session.add(user)
            session.flush()
            return user

        # `list_all` orders by `nome`, and the run keeps that order, so Ada comes before
        # Bruno in `inviato_a` and in the tracker's calls.
        titolare = _utente("Ada")
        collega = _utente("Bruno")
        _utente("Carla", digest=False)
        _utente("Dario", attivo=False)

        # Issued inside the week, on an *open* deal, so «fatturato ma non vinto» fires and
        # the report has a «da sistemare» section. Due far enough ahead that it is neither
        # overdue nor inside the seven-day «in scadenza» window, whenever this runs.
        session.add(
            Invoice(
                customer_id=customer.id,
                deal_id=deal.id,
                tipo="fattura",
                stato="emessa",
                stato_pagamento="da_incassare",
                anno=2026,
                numero=1,
                imponibile=Decimal("1000.00"),
                imposta=Decimal("0.00"),
                bollo=Decimal("0.00"),
                totale=Decimal("1000.00"),
                data_emissione=da + timedelta(days=1),
                data_scadenza=a + timedelta(days=120),
                data_incasso=None,
                tipo_documento="TD01",
                divisa="EUR",
                custom_fields={},
            )
        )
        session.add(
            TimeEntry(
                deal_id=deal.id,
                user_id=titolare.id,
                data=da + timedelta(days=1),
                ore=Decimal("6.00"),
                descrizione=f"{_PREFIX} lavorazione",
                fatturabile=True,
                tariffa_applicata=Decimal("50.000000"),
                tariffa_origine="manuale",
                costo_applicato=None,
                costo_origine="assente",
                invoice_line_id=None,
                custom_fields={},
            )
        )
        indirizzi = (titolare.email, collega.email)
        ids = (titolare.id, collega.id)
        session.commit()
    try:
        yield Corpus(db_engine, (da, a), iso_week(da), indirizzi[0], indirizzi[1], ids)
    finally:
        _pulisci(factory, iso_week(da), azienda_id)


def _pulisci(factory: Any, iso: str, azienda_id: UUID | None = None) -> None:
    """The fixture's own rows *and* whatever the run committed on top of them.

    Scoped to what this file can have written: the prefix for the corpus, and the one ISO
    week the runs are given for the `digests` row and the timeline entries hanging off it.
    A blanket `delete(Digest)` would take another file's row with it, and the whole point
    of `_require_empty` is that nobody's leftovers are anybody else's business.
    """
    with factory() as session:
        corpus_customers = select(Customer.id).where(Customer.ragione_sociale.like(f"{_PREFIX} %"))
        corpus_deals = select(Deal.id).where(Deal.nome.like(f"{_PREFIX} %"))
        settimane = select(Digest.id).where(Digest.settimana == iso)
        session.execute(
            delete(Activity).where(
                Activity.entity_type == _ENTITA, Activity.entity_id.in_(settimane)
            )
        )
        session.execute(delete(Activity).where(Activity.entity_id.in_(corpus_deals)))
        session.execute(delete(Digest).where(Digest.settimana == iso))
        session.execute(delete(TimeEntry).where(TimeEntry.deal_id.in_(corpus_deals)))
        session.execute(delete(Invoice).where(Invoice.customer_id.in_(corpus_customers)))
        session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
        session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
        session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
        session.execute(delete(User).where(User.nome.like(f"{_PREFIX} %")))
        remove_azienda(session, azienda_id)
        session.commit()


def _esegui(
    engine: Engine,
    *,
    settimana: tuple[date, date],
    titolare: str,
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
        esito = run.send_for_space(SLUG, titolare, settimana, forza=forza, dry_run=dry_run)
        assert not session.in_transaction(), (
            f"«{esito.esito}» left a transaction open on the session it was handed"
        )
        return esito


class Riga(NamedTuple):
    id: UUID
    inviato_a: list[str]
    occurred_at: datetime


def _riga(engine: Engine, iso: str) -> Riga | None:
    with session_factory(engine)() as session:
        row = session.execute(select(Digest).where(Digest.settimana == iso)).scalar_one_or_none()
        return None if row is None else Riga(row.id, list(row.inviato_a), row.occurred_at)


class Traccia(NamedTuple):
    kind: str
    entity_id: UUID
    actor_id: UUID | None
    actor_type: str
    payload: dict[str, Any]


def _attivita(engine: Engine) -> list[Traccia]:
    with session_factory(engine)() as session:
        rows = (
            session.execute(select(Activity).where(Activity.entity_type == _ENTITA)).scalars().all()
        )
        return [
            Traccia(r.kind, r.entity_id, r.actor_id, r.actor_type, dict(r.payload)) for r in rows
        ]


# --- the count of sections that had something in them ---------------------------------


def _digest_vuoto(**sovrascritture: Any) -> WeeklyDigest:
    campi: dict[str, Any] = {
        "settimana": "2026-W37",
        "da": date(2026, 9, 7),
        "a": date(2026, 9, 13),
        "scadute": [],
        "in_scadenza": [],
        "vinti_da_fatturare": 0,
        "ore_non_fatturate": Decimal("0.00"),
        "valore_maturato": Decimal("0.00"),
        "emesse": [],
        "totale_mese_corrente": Decimal("0.00"),
        "totale_mese_precedente": Decimal("0.00"),
        "incassate": [],
        "ore": None,
        "pipeline": [],
        "deal_mossi": [],
        "offerte_in_attesa": [],
        "segnali": [],
    }
    return WeeklyDigest(**(campi | sovrascritture))


def _fattura() -> dict[str, Any]:
    return {
        "invoice_id": uuid7(),
        "numero": "2026/1",
        "cliente": "Cliente",
        "importo": Decimal("100.00"),
        "data": date(2026, 9, 8),
        "stato": "emessa",
        "stato_pagamento": "da_incassare",
    }


def test_no_section_has_rows_in_an_empty_week() -> None:
    assert sezioni_con_righe(_digest_vuoto()) == 0


@pytest.mark.parametrize(
    "sovrascrittura",
    [
        {"scadute": [_fattura()]},
        {"in_scadenza": [_fattura()]},
        {"vinti_da_fatturare": 1},
        {"ore_non_fatturate": Decimal("0.25")},
        # The third of «Da emettere»'s figures. `digest_mail` prints that section's second
        # line on `ore_non_fatturate or valore_maturato`, so accrued value with no unbilled
        # hours behind it is still a section the recipient saw.
        {"valore_maturato": Decimal("1.00")},
        {"emesse": [_fattura()]},
        {"incassate": [_fattura()]},
        {"pipeline": [{"stage_nome": "Aperto", "numero": 1, "valore_totale": Decimal("1.00")}]},
        {
            "deal_mossi": [
                {
                    "deal_id": uuid7(),
                    "titolo": "D",
                    "stage_nome": "Vinto",
                    "quando": date(2026, 9, 8),
                }
            ]
        },
        {"offerte_in_attesa": [{"document_id": uuid7(), "titolo": "O", "giorni": 3}]},
        {"segnali": [{"codice": "c", "etichetta": "e", "conteggio": 1, "collegamento": "/x"}]},
    ],
)
def test_one_filled_list_lights_exactly_one_section(sovrascrittura: dict[str, Any]) -> None:
    """Eleven ways to fill one of the seven sections. Each lights one and only one, which
    is what makes the count a count of *sections* rather than of lists."""
    assert sezioni_con_righe(_digest_vuoto(**sovrascrittura)) == 1


def test_the_three_figures_of_da_emettere_are_one_section() -> None:
    """«Da emettere» is one heading over two lines and three figures. `digest_mail` prints
    it when any of the three is set, and this count has to agree or PostHog is told about
    a section the recipient never saw -- or not told about one they did."""
    for campi in (
        {"vinti_da_fatturare": 2},
        {"ore_non_fatturate": Decimal("3.00")},
        {"valore_maturato": Decimal("150.00")},
        {
            "vinti_da_fatturare": 2,
            "ore_non_fatturate": Decimal("3.00"),
            "valore_maturato": Decimal("150.00"),
        },
    ):
        assert sezioni_con_righe(_digest_vuoto(**campi)) == 1, campi


def test_the_two_halves_of_a_section_still_count_once() -> None:
    """«Da incassare» is two lists and one section: a week with both must not be reported
    as having had more to say than a week with one."""
    doppia = _digest_vuoto(scadute=[_fattura()], in_scadenza=[_fattura()])
    assert sezioni_con_righe(doppia) == 1


def test_a_week_with_everything_lights_all_seven() -> None:
    pieno = _digest_vuoto(
        scadute=[_fattura()],
        vinti_da_fatturare=2,
        emesse=[_fattura()],
        incassate=[_fattura()],
        ore={
            "da": date(2026, 9, 7),
            "a": date(2026, 9, 13),
            "giorni": [],
            "giorni_senza_ore": [],
            "ore_totali": Decimal("6.00"),
        },
        pipeline=[{"stage_nome": "Aperto", "numero": 1, "valore_totale": Decimal("1.00")}],
        segnali=[{"codice": "c", "etichetta": "e", "conteggio": 1, "collegamento": "/x"}],
    )
    assert sezioni_con_righe(pieno) == 7


# --- the send -------------------------------------------------------------------------


def test_the_report_goes_to_everyone_who_asked_for_it_and_to_nobody_else(
    corpus: Corpus,
) -> None:
    """§3.3: the recipients are the active users who have not switched the report off.
    Carla asked not to receive it and Dario has been deactivated; neither is written to,
    and neither appears in the row that says who was."""
    sender = RecordingSender()
    esito = _esegui(
        corpus.engine, settimana=corpus.settimana, titolare=corpus.titolare, sender=sender
    )

    assert esito == DigestOutcome(slug=SLUG, esito="inviato", settimana=corpus.iso, destinatari=2)
    assert [mail.to for mail in sender.sent] == [corpus.titolare, corpus.collega]
    assert all(mail.subject for mail in sender.sent)
    # The real mail, not a stub: the week's own invoice is in the body it carries.
    assert all("2026/1" in (mail.html or "") for mail in sender.sent)

    riga = _riga(corpus.engine, corpus.iso)
    assert riga is not None
    assert riga.inviato_a == [corpus.titolare, corpus.collega]


def test_the_timeline_entry_carries_counts_and_never_a_figure(corpus: Corpus) -> None:
    """The record of the week is a count of who and of how much there was to say. An
    amount, an invoice number or an address in the payload would put the space's money
    into a row read by anybody who can read the timeline."""
    _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=RecordingSender(),
    )

    righe = _attivita(corpus.engine)
    assert len(righe) == 1
    traccia = righe[0]
    riga = _riga(corpus.engine, corpus.iso)
    assert riga is not None
    assert traccia.kind == _KIND
    assert traccia.entity_id == riga.id
    assert traccia.actor_type == "system"
    assert traccia.payload == {
        "settimana": corpus.iso,
        "destinatari": 2,
        "sezioni": SEZIONI_ATTESE,
    }


def test_posthog_hears_once_per_recipient_after_the_commit(corpus: Corpus) -> None:
    """One event per person, under that person's own id, with the same section count the
    timeline recorded."""
    cattura = CapturaRegistrata()
    _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=RecordingSender(),
        tracker=Tracker(cattura),
    )

    assert [nome for nome, _, _ in cattura.chiamate] == [DIGEST_SENT, DIGEST_SENT]
    assert [ident for _, ident, _ in cattura.chiamate] == [str(i) for i in corpus.destinatari]
    for _, _, properties in cattura.chiamate:
        assert properties["settimana"] == corpus.iso
        assert properties["sezioni"] == SEZIONI_ATTESE


def test_a_tracker_that_refuses_changes_nothing(corpus: Corpus) -> None:
    """The capture is called after the commit, so its failure cannot reach the row."""
    sender = RecordingSender()
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=sender,
        tracker=Tracker(CapturaRotta()),
    )
    assert esito.esito == "inviato"
    assert len(sender.sent) == 2
    assert _riga(corpus.engine, corpus.iso) is not None


def test_without_a_resend_key_nothing_is_sent_and_nothing_is_recorded(corpus: Corpus) -> None:
    """`sender_from_settings` answers `None` on an installation with no key: there is no
    transport, so no mail was attempted and no week happened.

    This used to record the row, the timeline entry and the PostHog events as though the
    report had been delivered -- which made the week `gia_inviato` for ever and reported N
    deliveries of a mail nobody was ever sent. It is the same nothing `invio_rifiutato`
    leaves, and for the same reason: the run after the key is configured has to send it.
    """
    cattura = CapturaRegistrata()
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=None,
        tracker=Tracker(cattura),
    )
    assert esito == DigestOutcome(
        slug=SLUG, esito="saltato", settimana=corpus.iso, motivo="invio_non_configurato"
    )
    assert _riga(corpus.engine, corpus.iso) is None
    assert _attivita(corpus.engine) == []
    assert cattura.chiamate == []

    # And the run after the key is configured sends the week, to everybody.
    configurato = RecordingSender()
    ritentato = _esegui(
        corpus.engine, settimana=corpus.settimana, titolare=corpus.titolare, sender=configurato
    )
    assert ritentato.esito == "inviato"
    assert [mail.to for mail in configurato.sent] == [corpus.titolare, corpus.collega]


# --- what the provider refused --------------------------------------------------------


def test_only_the_addresses_the_provider_took_are_recorded(corpus: Corpus) -> None:
    """`EmailSender.send` answers `False` and does not raise, so a refusal is silent
    unless it is read. A report that never left must not appear in `inviato_a`, must not
    be counted, and must not be reported to PostHog as delivered."""
    sender = MittenteSelettivo(corpus.collega)
    cattura = CapturaRegistrata()
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=sender,
        tracker=Tracker(cattura),
    )

    assert esito == DigestOutcome(slug=SLUG, esito="inviato", settimana=corpus.iso, destinatari=1)
    assert [mail.to for mail in sender.sent] == [corpus.titolare]
    riga = _riga(corpus.engine, corpus.iso)
    assert riga is not None
    assert riga.inviato_a == [corpus.titolare]
    assert [traccia.payload["destinatari"] for traccia in _attivita(corpus.engine)] == [1]
    assert [ident for _, ident, _ in cattura.chiamate] == [str(corpus.destinatari[0])]


def test_a_week_nobody_received_is_left_to_be_retried(corpus: Corpus) -> None:
    """Every address refused. Writing the row anyway would make the week `gia_inviato`
    for ever on the strength of mails that never arrived, and Monday's outage would cost
    the space its report permanently. Nothing is written, so a rerun tries again."""
    sender = MittenteSelettivo(corpus.titolare, corpus.collega)
    cattura = CapturaRegistrata()
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=sender,
        tracker=Tracker(cattura),
    )

    assert esito == DigestOutcome(
        slug=SLUG, esito="saltato", settimana=corpus.iso, motivo="invio_rifiutato"
    )
    assert sender.sent == []
    assert cattura.chiamate == []
    assert _riga(corpus.engine, corpus.iso) is None
    assert _attivita(corpus.engine) == []

    # And the retry, against a provider that has come back, sends the whole week.
    di_nuovo = RecordingSender()
    ritentato = _esegui(
        corpus.engine, settimana=corpus.settimana, titolare=corpus.titolare, sender=di_nuovo
    )
    assert ritentato.esito == "inviato"
    assert [mail.to for mail in di_nuovo.sent] == [corpus.titolare, corpus.collega]


# --- once per week --------------------------------------------------------------------


def test_the_second_run_of_the_same_week_sends_nothing(corpus: Corpus) -> None:
    """A cron that runs twice, or two hosts that both wake up, must send once."""
    primo = RecordingSender()
    _esegui(corpus.engine, settimana=corpus.settimana, titolare=corpus.titolare, sender=primo)
    secondo = RecordingSender()
    cattura = CapturaRegistrata()
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=secondo,
        tracker=Tracker(cattura),
    )

    assert esito == DigestOutcome(slug=SLUG, esito="gia_inviato", settimana=corpus.iso)
    assert secondo.sent == []
    assert cattura.chiamate == []
    assert len(_attivita(corpus.engine)) == 1


def test_forza_sends_again_and_moves_the_row_instead_of_writing_a_second(
    corpus: Corpus,
) -> None:
    """`settimana` is unique, so a resend has to be an update. The row keeps saying who
    received it and when it last went out."""
    _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=RecordingSender(),
    )
    prima = _riga(corpus.engine, corpus.iso)
    assert prima is not None

    di_nuovo = RecordingSender()
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=di_nuovo,
        forza=True,
    )

    assert esito.esito == "inviato"
    assert [mail.to for mail in di_nuovo.sent] == [corpus.titolare, corpus.collega]
    dopo = _riga(corpus.engine, corpus.iso)
    assert dopo is not None
    assert dopo.id == prima.id
    assert dopo.inviato_a == [corpus.titolare, corpus.collega]
    assert dopo.occurred_at >= prima.occurred_at
    # Two runs, two timeline entries, one row: the record of *what happened* is the
    # timeline's business and the record of *this week* is the row's.
    assert len(_attivita(corpus.engine)) == 2


# --- the runs that write nothing ------------------------------------------------------


def test_an_empty_space_hears_nothing(db_engine: Engine) -> None:
    """§2: «silenzio per gli spazi vuoti». No customer, no deal, no invoice, no hour --
    and so no mail, no row, and nothing for PostHog to hear about either."""
    factory = session_factory(db_engine)
    with factory() as session:
        _require_empty(session)
        titolare = User(
            email=f"{_PREFIX.lower()}-solo-{uuid7()}@example.test",
            password_hash="x",
            nome=f"{_PREFIX} Solo",
            ruolo="admin",
        )
        session.add(titolare)
        session.flush()
        indirizzo = titolare.email
        session.commit()
    settimana = _settimana_scorsa()
    sender = RecordingSender()
    cattura = CapturaRegistrata()
    try:
        esito = _esegui(
            db_engine,
            settimana=settimana,
            titolare=indirizzo,
            sender=sender,
            tracker=Tracker(cattura),
        )
        assert esito == DigestOutcome(slug=SLUG, esito="vuoto", settimana=iso_week(settimana[0]))
        assert sender.sent == []
        assert cattura.chiamate == []
        assert _riga(db_engine, iso_week(settimana[0])) is None
        assert _attivita(db_engine) == []
    finally:
        _pulisci(factory, iso_week(settimana[0]))


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
    settimana = _settimana_scorsa()
    with factory() as session:
        _require_empty(session)
        quando = datetime.now(UTC)
        stage = PipelineStage(
            nome=f"{_PREFIX} Fase", posizione=0, probabilita_default=10, tipo="open"
        )
        customer = Customer(
            ragione_sociale=f"{_PREFIX} Cancellato",
            nazione="IT",
            custom_fields={},
            deleted_at=quando,
        )
        titolare = User(
            email=f"{_PREFIX.lower()}-resto-{uuid7()}@example.test",
            password_hash="x",
            nome=f"{_PREFIX} Resto",
            ruolo="admin",
        )
        session.add_all([stage, customer, titolare])
        session.flush()
        deal = Deal(
            nome=f"{_PREFIX} Deal cancellato",
            customer_id=customer.id,
            pipeline_stage_id=stage.id,
            probabilita=stage.probabilita_default,
            valore_previsto=Decimal("100.00"),
            custom_fields={},
            deleted_at=quando,
        )
        session.add(deal)
        session.flush()
        session.add(
            TimeEntry(
                deal_id=deal.id,
                user_id=titolare.id,
                data=settimana[0],
                ore=Decimal("2.00"),
                descrizione=f"{_PREFIX} ore cancellate",
                fatturabile=True,
                tariffa_applicata=Decimal("50.000000"),
                tariffa_origine="manuale",
                costo_applicato=None,
                costo_origine="assente",
                invoice_line_id=None,
                custom_fields={},
                deleted_at=quando,
            )
        )
        indirizzo = titolare.email
        session.commit()

    sender = RecordingSender()
    try:
        esito = _esegui(db_engine, settimana=settimana, titolare=indirizzo, sender=sender)
        assert esito == DigestOutcome(slug=SLUG, esito="vuoto", settimana=iso_week(settimana[0]))
        assert sender.sent == []
        assert _riga(db_engine, iso_week(settimana[0])) is None
    finally:
        _pulisci(factory, iso_week(settimana[0]))


def test_a_rehearsal_builds_the_report_and_leaves_nothing_behind(corpus: Corpus) -> None:
    """`--dry-run` answers what *would* go out, with the count, so the operator can read
    it before letting it go. Nothing is sent, written, recorded or tracked.

    `inviato` with the count, and not the `saltato` an installation with no key gets: a
    rehearsal *chose* not to send, and the operator asking for one is reading the answer."""
    sender = RecordingSender()
    cattura = CapturaRegistrata()
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=sender,
        tracker=Tracker(cattura),
        dry_run=True,
    )

    assert esito == DigestOutcome(slug=SLUG, esito="inviato", settimana=corpus.iso, destinatari=2)
    assert sender.sent == []
    assert cattura.chiamate == []
    assert _riga(corpus.engine, corpus.iso) is None
    assert _attivita(corpus.engine) == []


def test_a_space_where_nobody_wants_it_is_left_alone(corpus: Corpus) -> None:
    """Everyone switched it off. There is something to report and nobody to report it to,
    which is a different answer from «this space is empty» and leaves no row either."""
    with session_factory(corpus.engine)() as session:
        for user in session.execute(select(User)).scalars():
            user.digest_settimanale = False
        session.commit()

    sender = RecordingSender()
    esito = _esegui(
        corpus.engine, settimana=corpus.settimana, titolare=corpus.titolare, sender=sender
    )

    assert esito == DigestOutcome(slug=SLUG, esito="nessun_destinatario", settimana=corpus.iso)
    assert sender.sent == []
    assert _riga(corpus.engine, corpus.iso) is None


def test_an_unknown_owner_is_skipped(corpus: Corpus) -> None:
    """The actor is the space's owner. Without one there is nobody to read as, and a
    `system` actor with an invented role would be an escalation."""
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare="nessuno@example.test",
        sender=RecordingSender(),
    )
    assert esito.esito == "saltato"
    assert esito.motivo
    assert _riga(corpus.engine, corpus.iso) is None


def test_a_deactivated_owner_is_skipped(corpus: Corpus) -> None:
    """Deactivating somebody is, everywhere else in this product, the moment the CRM stops
    acting on their behalf. A cron that kept reading their space would be the exception."""
    with session_factory(corpus.engine)() as session:
        user = session.execute(select(User).where(User.email == corpus.titolare)).scalar_one()
        user.attivo = False
        session.commit()

    sender = RecordingSender()
    esito = _esegui(
        corpus.engine, settimana=corpus.settimana, titolare=corpus.titolare, sender=sender
    )
    assert esito.esito == "saltato"
    assert sender.sent == []
    assert _riga(corpus.engine, corpus.iso) is None


def test_a_send_that_blows_up_names_the_type_and_writes_nothing(corpus: Corpus) -> None:
    """The motive is the exception's *type*: its message can carry the address the send
    failed on, and this string is printed in a log and returned to a command."""
    cattura = CapturaRegistrata()
    esito = _esegui(
        corpus.engine,
        settimana=corpus.settimana,
        titolare=corpus.titolare,
        sender=MittenteCheEsplode(),
        tracker=Tracker(cattura),
    )

    assert esito.esito == "saltato"
    assert esito.motivo == "RuntimeError"
    assert "connessione" not in esito.motivo
    assert cattura.chiamate == []
    assert _riga(corpus.engine, corpus.iso) is None
    assert _attivita(corpus.engine) == []


# --- one report per distinct scope (REB-633, spec 2026-10-03 §4) ----------------------


def test_a_scoped_recipient_gets_a_report_built_inside_their_scope(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bruno may see one azienda, Ada the whole space: the report is rendered twice, once
    with `*` bound and once with Bruno's azienda, each sent to its own group, and the
    session is handed back inside the titolare's scope."""
    from sqlalchemy import text

    from pigrocrm.core.auth.models import UserAzienda
    from pigrocrm.core.db.scope import SCOPE_SETTING
    from pigrocrm.core.digest.service import DigestService

    factory = session_factory(corpus.engine)
    with factory() as session:
        azienda_id = session.execute(select(Customer.azienda_id)).scalars().first()
        assert azienda_id is not None
        bruno = session.get(User, corpus.destinatari[1])
        assert bruno is not None
        bruno.ambito_limitato = True
        session.add(UserAzienda(user_id=bruno.id, azienda_id=azienda_id))
        session.commit()

    bound: list[str] = []
    original = DigestService.build
    probed: list[str] = []
    original_probe = DigestRun._spazio_vuoto

    def _recording_probe(self: DigestRun) -> bool:
        probed.append(
            self.session.execute(
                text(f"SELECT current_setting('{SCOPE_SETTING}', true)")
            ).scalar_one()
        )
        return original_probe(self)

    monkeypatch.setattr(DigestRun, "_spazio_vuoto", _recording_probe)

    def _recording(self: DigestService, actor: Any, settimana: tuple[date, date]) -> WeeklyDigest:
        digest = original(self, actor, settimana)
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
        esito = run.send_for_space(SLUG, corpus.titolare, corpus.settimana)
        assert not session.in_transaction()
        back = session.execute(
            text(f"SELECT current_setting('{SCOPE_SETTING}', true)")
        ).scalar_one()

    assert esito.esito == "inviato" and esito.destinatari == 2
    # The emptiness probe, the first read of a policied table, already ran as «tutte».
    assert probed == ["*"]
    assert bound == ["*", str(azienda_id)]
    assert [mail.to for mail in sender.sent] == [corpus.titolare, corpus.collega]
    # The titolare's scope is what the session carries once the groups are done.
    assert back == "*"
