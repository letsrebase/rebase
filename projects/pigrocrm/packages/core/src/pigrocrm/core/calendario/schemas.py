import re
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from pigrocrm.core.attivita.schemas import AttivitaRead

# `AAAA-MM`, and validated as a *pattern* before anything is parsed: the month reaches
# `date(anno, mese, 1)`, and a caller who sends `2026-13` should read «non è un mese»
# rather than a `ValueError` from the standard library. The years are the same bounds
# `invoices/schemas.py` puts on `anno`, for the same reason -- a calendar of the year
# 9999 is a typo, not a request.
MESE_PATTERN = r"^\d{4}-(?:0[1-9]|1[0-2])$"
MESE_RE = re.compile(MESE_PATTERN)
# How many undated commitments travel with a month. See
# `AttivitaRepository.senza_scadenza` for why there is a cap at all.
SENZA_SCADENZA_LIMIT = 50


class DayDealHours(BaseModel):
    """The hours of one deal on one day, with the labels a grid needs to draw them.

    `deal_nome` and `cliente` are resolved once for the whole month, not per row: a
    month with sixty entries costs two label queries and never sixty. `cliente` is
    nullable because a deal's customer is nullable nowhere -- but a soft-deleted
    customer still answers with its name, deliberately: archiving a client did not
    unhappen the work.
    """

    deal_id: UUID
    deal_nome: str
    cliente: str | None = None
    ore: Decimal


class DueInvoice(BaseModel):
    """An issued, unpaid invoice falling due on a day of the month being read.

    Read-only in the calendar, and the spec says why (§2): the due date is a property of
    the invoice, decided when the document was issued or imported, and changing it from
    a view that shows neither the number nor the amount would be changing a fiscal
    document from the wrong place.

    `numero` is the number as a person reads it -- «3/2026» -- assembled here because the
    two columns that make it are `anno` and `numero` and a client should not have to know
    the format. Nullable in the model, never in practice for these rows: only an issued
    invoice is here, and an issued invoice has a number.
    """

    id: UUID
    numero: str | None = None
    cliente: str | None = None
    totale: Decimal
    data_scadenza: date
    # `True` when the due date is strictly in the past, by the emitter's clock -- the
    # same `<` as `_overdue_predicate`: due today is due today, not late.
    in_ritardo: bool


class DayWorkUnit(BaseModel):
    """One contract day on the calendar: which contract, how much of a day, and the state
    a cell marks it with. `stato` is the ledger's own word (`lavorato_senza_approvazione`
    among them), so the cell can say a day was worked without the approval its contract
    requires rather than drawing it like any other."""

    id: UUID
    contract_id: UUID
    contract_titolo: str
    customer_nome: str
    quantita: Decimal
    stato: str


class CalendarDay(BaseModel):
    """One day that has something on it.

    A day with nothing is **not** in the response (§6): the client knows how many days
    the month has and draws the empty ones, and thirty-one empty objects per request
    would be noise on every read.
    """

    giorno: date
    ore: Decimal
    per_deal: list[DayDealHours] = Field(default_factory=list)
    attivita: list[AttivitaRead] = Field(default_factory=list)
    fatture: list[DueInvoice] = Field(default_factory=list)
    giornate: list[DayWorkUnit] = Field(default_factory=list)


class CalendarMonth(BaseModel):
    """One month, in one read.

    `da` and `a` are the first and last day of the month, computed server-side so the
    client never has to agree with the server about how long February is. `oggi` travels
    too, because «today» is a day in the emitter's zone (`db/clock.py`) and a browser in
    another timezone would mark the wrong cell.

    `attivita_senza_scadenza` sits outside `giorni` and that is the whole point: an
    activity with no date cannot be drawn in a cell, is not late and is not for today.
    The section under the grid is where it belongs -- a NULL is not zero days.
    """

    mese: str = Field(pattern=MESE_PATTERN)
    da: date
    a: date
    oggi: date
    ore_totali: Decimal
    giorni: list[CalendarDay] = Field(default_factory=list)
    attivita_senza_scadenza: list[AttivitaRead] = Field(default_factory=list)
