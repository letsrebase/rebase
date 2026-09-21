"""**Criterion 2.** Every card that has a link equals the rows its link actually returns.

One predicate, two reads. The failure this file exists to catch is the one where a card and
the list behind it are computed by two queries that quietly disagree -- a filter dropped on
one side, a soft-deleted row counted on one side, a join written as outer on one side. So
every assertion here compares the card's figure with the **drill-through's own result set**,
never with a number this file recomputed: an expectation recomputed in the test shares the
card's bug and agrees with it.

The corpus is built so that each of those divergences would show. Every linked card is
non-empty (an empty card equals an empty list trivially), every filter has at least one row
that differs from the wanted set in exactly that one filter, and every side has a
soft-deleted row.

Where a filter did not already exist -- §6.2's inconsistency signal -- the predicate is
extracted to one module-level function in `documents/repository.py` and both the `COUNT` and
the `SELECT` call it, which is what makes §7.2 mechanical rather than aspirational. Two
hand-copied predicates agree until one is edited.

This is also what makes the cache safe (§7.2): the card and its drill-through cannot say
different things about the same data, so a divergence can only ever be the age of the cached
dashboard response -- and then the list wins and the card refreshes.

Like `test_dashboard_commercial.py` and `test_dashboard_snapshot.py`, this file builds its
own committed sessions: `DashboardService` sets the isolation level, and `db_session` holds
an outer transaction open. The teardown empties `pipeline_stages` for the same reason those
files do.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import CommercialDashboard, PeriodoQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.documents.schemas import DocumentListQuery
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.timetracking.repository import TimeEntryRepository

READONLY = Actor(id=uuid7(), type="user", role="readonly")
SEED = Actor(id=None, type="system", role="admin")
_PREFIX = "DRILL"
# Comfortably above every cardinality this corpus produces, so `len(rows)` is the whole
# answer and not a page of it. `DocumentRepository.list` deliberately fetches `limit + 1`
# rows to detect a next page; at this size that extra row never exists.
_ALL = 200


class Seeded(NamedTuple):
    engine: Engine
    stage_ids: dict[str, UUID]


def _cancellato() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def seeded(db_engine: Engine) -> Iterator[Seeded]:
    factory = session_factory(db_engine)
    with factory() as session:
        stages = {s.code: s for s in PipelineService(session).seed_defaults(SEED) if s.code}
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        session.add(customer)
        session.flush()

        def deal(nome: str, stage: str, **extra: object) -> Deal:
            row = Deal(
                nome=f"{_PREFIX} {nome}",
                customer_id=customer.id,
                pipeline_stage_id=stages[stage].id,
                valore_previsto=Decimal("1000.00"),
                probabilita=50,
                custom_fields={},
                **extra,
            )
            session.add(row)
            return row

        # Four in `lead` and two in `contattato`: the stage card must equal the deals of
        # *its* stage, so a drill-through that dropped `stage_id` would return six.
        aperti = [deal(f"aperto {index}", "lead") for index in range(4)]
        for index in range(2):
            deal(f"contattato {index}", "contattato")
        # Soft-deleted, in `lead`. Counted by neither side; counted by a side that forgot
        # `deleted_at IS NULL`.
        rimosso = deal("aperto cancellato", "lead", deleted_at=_cancellato())
        vinto = deal("vinto", "vinto", chiuso_il=today_local())
        perso = deal("perso", "perso", chiuso_il=today_local())
        session.flush()

        def document(nome: str, *, tipo: str = "offerta", **extra: object) -> None:
            session.add(
                Document(
                    tipo=tipo,
                    titolo=f"{_PREFIX} {nome}",
                    stato_dal=today_local(),
                    versione_corrente=1,
                    custom_fields={},
                    **extra,
                )
            )

        # -- the "offerte in attesa" card -----------------------------------------
        for index in range(3):
            document(f"inviata {index}", customer_id=customer.id, stato="inviata")
        document(
            "inviata cancellata", customer_id=customer.id, stato="inviata", deleted_at=_cancellato()
        )
        # Differs from the wanted set in `stato` only, and the next one in `tipo` only:
        # each exists so that dropping one filter on one side shows up as a divergence
        # rather than as two matching numbers.
        document("bozza", customer_id=customer.id, stato="bozza")
        document("contratto inviato", customer_id=customer.id, tipo="contratto", stato="inviata")

        # -- §6.2's signal card: accepted offer, deal not won ---------------------
        document("accettata 0", deal_id=aperti[0].id, stato="accettata")
        document("accettata 1", deal_id=aperti[1].id, stato="accettata")
        # A lost deal with an accepted offer is the signal too: the predicate is
        # `tipo != 'won'`, not `tipo == 'open'`. Written down because the two differ only
        # on this row.
        document("accettata persa", deal_id=perso.id, stato="accettata")
        # The three exclusions, one per clause of the predicate.
        document("accettata ok", deal_id=vinto.id, stato="accettata")
        document("accettata su cliente", customer_id=customer.id, stato="accettata")
        document(
            "accettata cancellata",
            deal_id=aperti[2].id,
            stato="accettata",
            deleted_at=_cancellato(),
        )
        document("accettata deal cancellato", deal_id=rimosso.id, stato="accettata")
        session.commit()
        stage_ids = {code: stage.id for code, stage in stages.items()}
    try:
        yield Seeded(db_engine, stage_ids)
    finally:
        with factory() as session:
            session.execute(delete(Document).where(Document.titolo.like(f"{_PREFIX} %")))
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage))
            session.commit()


def _dashboard(engine: Engine) -> CommercialDashboard:
    with session_factory(engine)() as session:
        return DashboardService(session).get_commercial_dashboard(PeriodoQuery(), READONLY)


def _documents(engine: Engine, query: DocumentListQuery) -> list[Document]:
    with session_factory(engine)() as session:
        return DocumentRepository(session).list(query)


# -- card: deals aperti per stage -> /app/deal/list?stage_id=... -----------------


def test_the_open_deals_card_equals_its_deal_list(seeded: Seeded) -> None:
    """Per stage, against the rows the link returns.

    Both stages are checked, and they differ: a drill-through that ignored `stage_id`
    would return the same six deals for each and fail on both, while a card that
    ignored the stage would fail on neither if only one stage were asserted.
    """
    dashboard = _dashboard(seeded.engine)
    for code, expected in (("lead", 4), ("contattato", 2)):
        card = next(row for row in dashboard.pipeline if row.stage_code == code)
        with session_factory(seeded.engine)() as session:
            page = DealService(session).list(
                DealListQuery(stage_id=seeded.stage_ids[code], limit=_ALL), READONLY
            )
        assert card.numero == len(page.items), f"stage {code}"
        assert card.numero == expected, f"stage {code}: the corpus itself has drifted"
        assert all(str(item.pipeline_stage_id) == card.stage_id for item in page.items)


def test_the_open_deals_card_excludes_the_soft_deleted_deal_on_both_sides(
    seeded: Seeded,
) -> None:
    """The divergence this criterion is really about: a row one side filters and the other
    does not. `DRILL aperto cancellato` sits in `lead`, so a card counting it would read 5
    against a list of 4 -- and a *list* including it would read 5 against a card of 4."""
    card = next(row for row in _dashboard(seeded.engine).pipeline if row.stage_code == "lead")
    with session_factory(seeded.engine)() as session:
        page = DealService(session).list(
            DealListQuery(stage_id=seeded.stage_ids["lead"], limit=_ALL), READONLY
        )
    nomi = {item.nome for item in page.items}
    assert f"{_PREFIX} aperto cancellato" not in nomi
    assert card.numero == len(nomi)


# -- card: offerte in attesa -> /app/documents?tipo=offerta&stato=inviata ---------


def test_the_pending_offers_card_equals_its_document_list(seeded: Seeded) -> None:
    """The total, the preview the card itself renders, and the list behind the link: three
    renderings of one predicate, asserted as the same set of rows and not merely as the
    same integer."""
    dashboard = _dashboard(seeded.engine)
    rows = _documents(seeded.engine, DocumentListQuery(tipo="offerta", stato="inviata", limit=_ALL))
    assert dashboard.offerte_in_attesa_totale == len(rows) == 3
    assert {offer.document_id for offer in dashboard.offerte_in_attesa} == {
        str(row.id) for row in rows
    }


def test_the_pending_offers_list_drops_the_row_that_differs_only_in_tipo_or_stato(
    seeded: Seeded,
) -> None:
    """Each filter, isolated. `DRILL contratto inviato` differs from a wanted row only in
    `tipo`, `DRILL bozza` only in `stato`, `DRILL inviata cancellata` only in
    `deleted_at`. A side that dropped any one of the three would return four rows against
    a card of three."""
    titoli = {
        row.titolo
        for row in _documents(
            seeded.engine, DocumentListQuery(tipo="offerta", stato="inviata", limit=_ALL)
        )
    }
    assert titoli == {f"{_PREFIX} inviata {index}" for index in range(3)}


# -- card: offerta accettata, deal non vinto -> ?solo_deal_non_vinto=true ---------


def test_the_signal_card_equals_its_filtered_document_list(seeded: Seeded) -> None:
    """The card and the list share one predicate function, so this cannot drift.

    Asserted as a set of titles rather than as a count: two queries returning three rows
    each can still be returning different three rows, and a count-only assertion is
    exactly how that goes unnoticed.
    """
    count = _dashboard(seeded.engine).offerte_accettate_deal_non_vinto
    rows = _documents(seeded.engine, DocumentListQuery(solo_deal_non_vinto=True, limit=_ALL))
    assert count == len(rows)
    assert {row.titolo for row in rows} == {
        f"{_PREFIX} accettata 0",
        f"{_PREFIX} accettata 1",
        f"{_PREFIX} accettata persa",
    }
    assert all(row.stato == "accettata" for row in rows)


@pytest.mark.parametrize(
    ("escluso", "perche"),
    [
        ("accettata ok", "il deal è vinto"),
        ("accettata su cliente", "non ha un deal"),
        ("accettata cancellata", "il documento è cancellato"),
        ("accettata deal cancellato", "il deal è cancellato"),
    ],
)
def test_the_signal_filter_excludes_each_row_it_must(
    seeded: Seeded, escluso: str, perche: str
) -> None:
    """The negative half, one clause of the predicate at a time.

    Without it, a filter that returned every accepted offer would still pass the equality
    test above whenever the count was wrong in the same way -- which is precisely what
    "two calculations that agree" means.
    """
    titoli = {
        row.titolo
        for row in _documents(
            seeded.engine, DocumentListQuery(solo_deal_non_vinto=True, limit=_ALL)
        )
    }
    assert f"{_PREFIX} {escluso}" not in titoli, perche


def test_the_signal_filter_composes_with_the_other_filters(seeded: Seeded) -> None:
    """It is an additional predicate, not a replacement for the query. A filter that
    silently dropped `stato` would make the drill-through a different question, and a
    filter that replaced the statement would return the accepted offers here."""
    assert (
        _documents(
            seeded.engine, DocumentListQuery(solo_deal_non_vinto=True, stato="inviata", limit=_ALL)
        )
        == []
    )
    scoped = _documents(
        seeded.engine,
        DocumentListQuery(solo_deal_non_vinto=True, deal_id=None, tipo="offerta", limit=_ALL),
    )
    assert len(scoped) == 3


def test_the_count_and_the_list_call_the_same_predicate_function() -> None:
    """The mechanical half of §7.2, asserted on the source of each method rather than on
    the module as a whole: two hand-copied predicates agree until one of them is edited,
    and a module-wide `count(...)` would be satisfied by a definition nobody calls."""
    for method in (
        DocumentRepository.count_accepted_with_unwon_deal,
        DocumentRepository.list,
    ):
        assert "_accepted_with_unwon_deal_predicate()" in inspect.getsource(method), (
            f"{method.__name__} must call the shared predicate, not restate it"
        )


# -- criterion 2 on the operational dashboard's three signals ---------------------
#
# The counts and their drill-throughs are compared row for row in
# `test_dashboard_operational.py`, which owns the corpus each signal needs. What is missing
# there, and is the mechanical half §7.2 actually asks for, is this: that neither side
# *restates* the predicate. Two hand-copied predicates agree on every corpus anybody thinks
# to write and diverge the first time one of them is edited -- which is a defect no
# row-for-row comparison can anticipate, because the corpus that would show it does not
# exist until after the divergence.
#
# Each of the three lives in the repository of the table it filters and has exactly two
# callers, one per side of the card. The pairs are asserted by name so that a fourth caller
# added later has to be added here too, and a predicate deleted in favour of an inline
# `where` fails on the source of the very method that inlined it.
_SHARED_PREDICATES = [
    (
        "invoiced_not_won_predicate()",
        (InvoiceRepository.count_deals_invoiced_not_won, DealRepository.list),
    ),
    (
        "won_with_unbilled_hours_predicate()",
        (TimeEntryRepository.count_won_deals_to_invoice, DealRepository.list),
    ),
    (
        "_overdue_predicate()",
        (InvoiceRepository.count_scadute_non_incassate, InvoiceRepository.list),
    ),
]


@pytest.mark.parametrize(("predicate", "callers"), _SHARED_PREDICATES, ids=lambda v: str(v)[:40])
def test_each_signal_and_its_drill_through_call_the_same_predicate_function(
    predicate: str, callers: tuple[object, ...]
) -> None:
    """§7.2 on §6.2's three signals: the card and the list behind it are one predicate.

    Asserted on the source of each individual method, not on the module: a module-wide
    search would be satisfied by a definition nobody calls, which is the state the codebase
    would be in one edit after somebody inlined one of the two sides.
    """
    for method in callers:
        assert predicate in inspect.getsource(method), (
            f"{method.__qualname__} must call {predicate}, not restate it"
        )
