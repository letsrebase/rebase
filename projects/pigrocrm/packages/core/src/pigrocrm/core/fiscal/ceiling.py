"""Ceiling evaluation, the rivalsa invoice line, and the tagged-figure split
between them -- REB-361, ported against REB-344 §8-9 and REB-352 §2/§6.

Three things live here, all reading or writing invoices through a `Session`, which
is why none of them belong in `pack.py` (pure data, no database access at all):

1. **Ceiling evaluation.** `evaluate_pack` sums `Invoice.imponibile` across every
   invoice *paid* within a calendar year (REB-344 §8's `cash_received_calendar_year`
   basis: `stato_pagamento = 'incassato'`, by `data_incasso` falling back to
   `data_emissione` the same way `AnalyticsRepository.monthly_incassato` already
   does) and compares it against each of the pack's own ceilings. A rivalsa line is
   one more VAT-grouped line on the same invoice and is therefore already inside
   `imponibile` the moment it exists -- no extra step, exactly as REB-352 §2's "no
   code change is needed for the ceiling side" says.

2. **The rivalsa invoice line.** `rivalsa_line_for_contract` is the "new step" REB-344
   §9 describes for whatever assembles a contract-linked invoice's lines: given the
   contract's own election (`contracts.applies_social_charge`) and the fee subtotal
   already assembled, it returns the ordinary `InvoiceLineIn` to append, or `None`.
   No new schema: the line goes through the exact same `_computed_lines`/
   `RegimeStrategy.resolve_line_vat` path every other line does, so its `natura`/
   `riferimento_normativo` are whatever the regime already computes for a zero-rate
   domestic or foreign line -- identical to a fee line's own. What marks it as *this*
   charge is `descrizione`, the one column the regime never touches and never
   overwrites (see `pack.py::StatutoryCharge.descrizione_riga`'s own docstring for
   why that, and not `natura`/`riferimento_normativo`, is the tag).

3. **The tagged, subtractable figure.** REB-352 §2 names a real tension: the rivalsa
   amount counts toward a ceiling's revenue in full but is not taxable income, and
   `estimate_income` has exactly one `ricavi` parameter. §6 resolves it: the amount is
   carried as its own tagged figure at the point revenue is summed, not a second
   parameter on `estimate_income` -- so its signature stays stable for every other
   caller. `excluded_from_coefficiente_base` is that tagged figure (identified by
   `descrizione_riga`, the same tag construction wrote); `taxable_ricavi` is what
   `AnalyticsService.get_fiscal_estimate` passes as `ricavi` instead of the raw
   `annual_revenue` figure, so the coefficiente base and the ceiling's own revenue sum
   can diverge by exactly the rivalsa amount, as designed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.fiscal.pack import (
    RIVALSA_INPS_CHARGE_ID,
    Ceiling,
    FiscalPack,
    known_charge_markers,
)
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceLineIn
from pigrocrm.core.money import ZERO_MONEY, round_money


# The one definition of "an invoice that is revenue", duplicated rather than imported
# from `analytics/repository.py`'s own `_revenue_filter`: that function is a private,
# underscore-prefixed detail of a package `fiscal` should not reach across (`analytics`
# already imports `fiscal`'s `FiscalProfileService`, and importing its *service*
# module back from here would be the real cycle -- reaching only into
# `analytics.repository` happens not to cycle today, but a private helper in another
# package's repository is not a contract this module should depend on regardless).
# Three comparisons cost nothing to rebuild -- the same reasoning `_revenue_filter`'s
# own docstring gives for not sharing a module-level tuple either.
def _azienda_filter(azienda_id: UUID | None) -> tuple[ColumnElement[bool], ...]:
    """One azienda's register, or every one (REB-630, spec 2026-10-03 §1.9). A ceiling
    exists on one pack and a pack is one azienda's, so every caller on the service side
    passes an id; `None` is kept for the arithmetic's own tests, which built their
    corpus before the column existed and still describe one azienda."""
    return () if azienda_id is None else (Invoice.azienda_id == azienda_id,)


def _issued_invoice_filter(
    anno: int, azienda_id: UUID | None = None
) -> tuple[ColumnElement[bool], ...]:
    return (
        Invoice.anno == anno,
        Invoice.tipo == "fattura",
        Invoice.stato == "emessa",
        Invoice.deleted_at.is_(None),
        *_azienda_filter(azienda_id),
    )


def _paid_calendar_year_filter(
    anno: int, azienda_id: UUID | None = None
) -> tuple[ColumnElement[bool], ...]:
    """`cash_received_calendar_year` (REB-344 §8): paid, by the year of `data_incasso`
    falling back to `data_emissione` when a paid invoice never recorded the exact
    collection date -- the identical fallback `AnalyticsRepository.monthly_incassato`
    already uses for the same reason."""
    first, next_year = date(anno, 1, 1), date(anno + 1, 1, 1)
    return (
        Invoice.tipo == "fattura",
        Invoice.stato == "emessa",
        Invoice.deleted_at.is_(None),
        Invoice.stato_pagamento == "incassato",
        # Half-open ranges so the date columns' indexes stay usable; the second branch
        # is the `coalesce(data_incasso, data_emissione)` fallback.
        or_(
            (Invoice.data_incasso >= first) & (Invoice.data_incasso < next_year),
            Invoice.data_incasso.is_(None)
            & (Invoice.data_emissione >= first)
            & (Invoice.data_emissione < next_year),
        ),
        *_azienda_filter(azienda_id),
    )


def paid_revenue_for_calendar_year(
    session: Session, anno: int, azienda_id: UUID | None = None
) -> Decimal:
    """Sigma `Invoice.imponibile` of every invoice paid within `anno` -- the ceiling's
    own basis, in full, rivalsa included (REB-352 §2) -- of one azienda when named
    (REB-630): a ceiling is a fact about one azienda's receipts, never the space's."""
    return round_money(
        Decimal(
            session.execute(
                select(func.coalesce(func.sum(Invoice.imponibile), 0)).where(
                    *_paid_calendar_year_filter(anno, azienda_id)
                )
            ).scalar_one()
        )
    )


@dataclass(frozen=True)
class CeilingStatus:
    """One ceiling, evaluated against a calendar year's paid revenue."""

    ceiling: Ceiling
    ricavi: Decimal
    residuo: Decimal
    superata: bool
    livello_allerta: str | None


def evaluate_ceiling(ceiling: Ceiling, ricavi: Decimal) -> CeilingStatus:
    """`ceiling` against an already-summed `ricavi` -- the pure half, so a caller
    that already has the figure (the "would this fit?" simulator, REB-352 §1.4)
    never re-reads the database to ask a question about a synthetic addition."""
    livello: str | None = None
    for level in ceiling.livelli_allerta:
        if ricavi >= ceiling.soglia * level.rapporto:
            livello = level.etichetta
    return CeilingStatus(
        ceiling=ceiling,
        ricavi=ricavi,
        residuo=ceiling.soglia - ricavi,
        superata=ricavi >= ceiling.soglia,
        livello_allerta=livello,
    )


def evaluate_pack(
    pack: FiscalPack, session: Session, anno: int, azienda_id: UUID | None = None
) -> list[CeilingStatus]:
    """Every ceiling `pack` declares, evaluated against `anno`'s paid revenue -- one
    query shared by every ceiling, since `IT_FLAT_RATE_PACK`'s own two share the same
    `all_clients` perimeter and `cash_received_calendar_year` basis. `azienda_id` is
    the azienda whose profile points at `pack` (REB-630)."""
    ricavi = paid_revenue_for_calendar_year(session, anno, azienda_id)
    return [evaluate_ceiling(ceiling, ricavi) for ceiling in pack.ceilings]


def rivalsa_line_for_contract(
    pack: FiscalPack, *, applies_social_charge: bool, fee_subtotal: Decimal
) -> InvoiceLineIn | None:
    """The rivalsa line to append after a contract-linked invoice's fee lines, or
    `None` when the contract has not elected it (`contracts.applies_social_charge`,
    REB-344 §3/§9). `fee_subtotal` is the fee lines already assembled -- the base the
    charge's own rate applies to, which never widens beyond the fee alone because
    PigroCRM never recharges stamp duty to the client (REB-344 §9's own note; mastro's
    `chargeSlotOrder` would widen it the day that ever changes)."""
    if not applies_social_charge:
        return None
    try:
        charge = pack.charge(RIVALSA_INPS_CHARGE_ID)
    except KeyError:
        # A pack with no such charge (`non-it`, REB-619): the contract's election names
        # an Italian charge a foreign azienda does not owe, so there is no line to add.
        return None
    importo = round_money(fee_subtotal * charge.aliquota)
    return InvoiceLineIn(
        descrizione=charge.descrizione_riga,
        quantita=Decimal("1.000000"),
        prezzo_unitario=importo,
        aliquota_iva=Decimal("0.00"),
    )


# A statutory charge's line is recognised by its `descrizione` alone. No structural
# signature can replace that without a new column: the line is an ordinary zero-VAT line
# whose `natura`/`riferimento_normativo` are the regime's single pair, shared by every
# zero-rate fee line, with a quantity of one and no unit like any flat fee. These two
# helpers are the only place that reads the text marker, so the day `invoice_lines`
# gains a charge column both change together and nothing else does.
def charge_line_clause(markers: Iterable[str]) -> ColumnElement[bool]:
    """SQL predicate: `InvoiceLine` rows carrying one of the charge `markers`."""
    return InvoiceLine.descrizione.in_(tuple(markers))


def is_statutory_charge_line(line: InvoiceLine) -> bool:
    """Whether `line` is the line of a statutory charge any shipped pack declares,
    whatever pack the azienda is on today: an invoice keeps the lines it was issued
    with after the profile moves to another pack."""
    return line.descrizione in known_charge_markers()


def excluded_from_coefficiente_base(
    pack: FiscalPack, session: Session, anno: int, azienda_id: UUID | None = None
) -> Decimal:
    """Sigma `InvoiceLine.prezzo_totale` of every line the pack tags as counting
    toward a ceiling but not toward the coefficiente base (REB-352 §2/§6) -- issued
    invoices of `anno`, the same universe `AnalyticsRepository.annual_revenue` sums,
    identified by `descrizione_riga`, the one stable marker available without a new
    `InvoiceLine`/`Invoice` column (see `pack.py::StatutoryCharge`'s own docstring)."""
    marcatori = tuple(
        charge.descrizione_riga for charge in pack.charges if not charge.conta_per_il_coefficiente
    )
    if not marcatori:
        return ZERO_MONEY
    return round_money(
        Decimal(
            session.execute(
                select(func.coalesce(func.sum(InvoiceLine.prezzo_totale), 0))
                .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
                .where(
                    *_issued_invoice_filter(anno, azienda_id),
                    charge_line_clause(marcatori),
                )
            ).scalar_one()
        )
    )


def taxable_ricavi(
    pack: FiscalPack,
    session: Session,
    anno: int,
    ricavi_totali: Decimal,
    azienda_id: UUID | None = None,
) -> Decimal:
    """`ricavi_totali` minus the pack's tagged, non-taxable statutory charges --
    REB-352 §6's resolved decision, applied. `ricavi_totali` is the caller's own
    already-computed figure (`AnalyticsRepository.annual_revenue`) rather than one
    this function recomputes, so the ceiling's revenue sum and the coefficiente base
    can never read a different universe of invoices by accident -- they diverge by
    exactly the tagged amount, and by nothing else."""
    return ricavi_totali - excluded_from_coefficiente_base(pack, session, anno, azienda_id)


__all__ = [
    "CeilingStatus",
    "charge_line_clause",
    "evaluate_ceiling",
    "evaluate_pack",
    "excluded_from_coefficiente_base",
    "is_statutory_charge_line",
    "paid_revenue_for_calendar_year",
    "rivalsa_line_for_contract",
    "taxable_ricavi",
]
