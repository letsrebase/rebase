"""The predicates and the row shapes the receivables, the dashboards and the drill-throughs
share, so the card on a page and the list behind it are the same query by construction.

Read by `InvoiceRepository` (which runs them), `DealRepository` (the invoiced-but-not-won
drill-through) and the dashboard tests.
"""

from datetime import date
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import ColumnElement

from pigrocrm.core.db import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage


def _issued_filter() -> tuple[ColumnElement[bool], ...]:
    """The one definition of "an invoice that exists as a fiscal document".

    Identical to `AnalyticsRepository._revenue_filter` on purpose and not imported from it:
    that function answers "which invoices are revenue" and this one answers "which invoices
    are documents somebody owes on". They coincide today because both mean "a `fattura`
    that was issued and not annulled", and the day they stop coinciding a shared alias
    would silently pick a side -- the same reasoning `analytics/schemas.py` records for
    `DealStato`.
    """
    return (
        Invoice.deleted_at.is_(None),
        Invoice.tipo == "fattura",
        Invoice.stato == "emessa",
    )


def _azienda_filter(azienda_id: UUID | None) -> tuple[ColumnElement[bool], ...]:
    """One azienda's register, or every azienda's (REB-630, spec 2026-10-03 §1.9): the
    one predicate the dashboard sums below add when the sidebar names an azienda, so
    «tutte» stays their unfiltered sum by construction."""
    return () if azienda_id is None else (Invoice.azienda_id == azienda_id,)


def _receivable_filter() -> tuple[ColumnElement[bool], ...]:
    """An issued invoice nobody has paid. The base of both "Da incassare" and "Scaduto",
    which is what makes the second a subset of the first rather than a figure of its own
    (§5.2)."""
    return (*_issued_filter(), Invoice.stato_pagamento == "da_incassare")


def _overdue_predicate() -> tuple[ColumnElement[bool], ...]:
    """A receivable past its due date, strictly.

    Due today is due today, so `<` and not `<=`. A null `data_scadenza` is never overdue,
    which Postgres's three-valued logic gives for free -- the explicit `IS NOT NULL` is
    there because relying on that silently is how the opposite gets implemented by
    accident.

    `today_local()` and not `CURRENT_DATE`: `CURRENT_DATE` is the *database server's* day,
    and every date in this product is a day in the emitter's zone (`db/clock.py`). Read at
    call time, never bound once at import, so a process that outlives midnight in Rome does
    not keep answering yesterday.
    """
    return (
        *_receivable_filter(),
        Invoice.data_scadenza.is_not(None),
        Invoice.data_scadenza < today_local(),
    )


# The six ageing buckets of the scadenziario (slice 8 §2.1, REB-329), in the order the
# page draws them. Six and not five: `data_scadenza` is nullable and a NULL is not today.
FASCE_SCADENZIARIO: tuple[str, ...] = (
    "scaduto",
    "entro_30",
    "da_31_a_60",
    "da_61_a_90",
    "oltre_90",
    "senza_scadenza",
)


class FasciaRow(NamedTuple):
    codice: str
    da: date | None
    a: date | None
    importo: Decimal
    numero: int
    # The bucket's share of the largest bucket, in [0, 1], so a page can scale a bar
    # without turning an amount string into a number (slice 6 criterion 14).
    quota: float


class MeseRow(NamedTuple):
    mese: date  # the first day
    importo: Decimal
    numero: int
    quota: float


class ClienteRow(NamedTuple):
    customer_id: UUID
    ragione_sociale: str
    importo: Decimal
    numero: int
    scaduto: Decimal
    quota: float


class ScadutaRow(NamedTuple):
    invoice_id: UUID
    numero: str
    customer_id: UUID
    cliente: str
    data_scadenza: date
    giorni_di_ritardo: int
    importo: Decimal
    solleciti_inviati: int
    ultimo_sollecito_il: date | None


def invoiced_not_won_predicate() -> tuple[ColumnElement[bool], ...]:
    """A deal with an issued invoice that is still sitting on an `open` stage (§6.2).

    `tipo = 'open'`, not `tipo <> 'won'`. The drill-through is a list of deals to go and
    win -- almost always the stage somebody forgot to move -- and an invoiced deal parked
    on `perso` is a different problem with a different remedy, which this signal must not
    silently absorb.

    Written as a predicate rather than inlined because slice 6 §6.2's card and its
    drill-through must be the *same* predicate: `DealRepository.list` filters on this same
    tuple, so the count on the card and the length of the list behind it cannot drift.

    Public, unlike `_issued_filter` and `_overdue_predicate` beside it, because its second
    caller lives in another module: `deals/repository.py` imports it for the
    `fatturato_non_vinto` drill-through. A leading underscore reached across a package
    boundary is a worse signal than a public name.
    """
    return (
        *_issued_filter(),
        Deal.deleted_at.is_(None),
        PipelineStage.tipo == "open",
    )
