"""Spec §9's own call site: the unbilled-`work_unit`-to-`InvoiceLine` assembly step.

This is the foundations half REB-359 owns -- fee lines only, for a day with no
`applies_social_charge` awareness at all (that election, and the rivalsa line itself,
is a later issue's job, appended by a separate step onto whatever this one produces).
No schema change to `Invoice`/`InvoiceLine`: an approved, worked day becomes an
ordinary `InvoiceLine` through `InvoiceService.replace_lines`, the same path every
other line on this schema already goes through.

Mirrors `AnalyticsService.bind_time_to_invoice` (`analytics/service.py`) in shape --
one line per rate card per month, for legibility, with the resulting line ids bound
back positionally -- but gathers automatically (`stato = 'lavorato' AND
invoice_line_id IS NULL`), the same predicate shape `won_with_unbilled_hours_predicate`
(`timetracking/repository.py`) already gives `TimeEntry`, rather than an explicit
caller-chosen id list: an approved, worked day is billable by definition the moment
nothing already covers it -- unlike an hour, invoicing which is `bind_time_to_invoice`'s
own commercial choice, a day was already the commercial decision when it moved to
`'lavorato'`.

`assemble_unbilled_work_units_into_new_invoice` is the convenience path for the common
case: a contract with nothing else being assembled onto the same invoice. A caller that
also needs to append a rivalsa line or a rebillable expense to the *same* invoice
should use `unbilled_work_unit_lines` and `bind_work_units` directly around its own
single `replace_lines` call instead -- `InvoiceLineIn` deliberately carries no
`natura`/`riferimento_normativo` (`invoices/schemas.py`), so an existing rivalsa line
read back and round-tripped through a second, independent `replace_lines` call would
silently lose the very fields that make it a rivalsa line.
"""

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.contracts.repository import RateCardRepository
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn, InvoiceRead
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.timetracking.locks import period_label
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.repository import WorkUnitRepository

ENTITY = "work_unit"


def unbilled_work_unit_lines(
    session: Session, contract_id: UUID
) -> tuple[list[InvoiceLineIn], list[list[UUID]]]:
    """`(righe, gruppi)`: one `InvoiceLineIn` per `(rate_card, month)` group of
    unbilled, worked days, and the ids each line prices, in the same order --
    positionally zippable with the line ids `replace_lines` produces
    (`bind_work_units`), the same mechanism `bind_time_to_invoice` uses for
    `TimeEntry` since `InvoiceService._computed_lines` numbers `numero_linea` from
    the order `righe` was built in.

    Raises `ValidationFailed` if any gathered day has no rate card covering its own
    `data` -- inventing a price here would be a commercial decision this step does
    not own, the same refusal `bind_time_to_invoice` gives an unpriced `TimeEntry`.
    """
    work_units = WorkUnitRepository(session).unbilled_for_contract(contract_id)
    if not work_units:
        return [], []

    cards = RateCardRepository(session).list_for_contract(contract_id)

    groups: dict[tuple[UUID, int, int], list[WorkUnit]] = defaultdict(list)
    uncovered: list[WorkUnit] = []
    for work_unit in work_units:
        card = next(
            (
                c
                for c in cards
                if c.valido_da <= work_unit.data
                and (c.valido_a is None or work_unit.data <= c.valido_a)
            ),
            None,
        )
        if card is None:
            uncovered.append(work_unit)
            continue
        groups[(card.id, work_unit.data.year, work_unit.data.month)].append(work_unit)

    if uncovered:
        raise ValidationFailed(
            ENTITY,
            "data",
            f"{len(uncovered)} giornate non hanno una rate_card in vigore alla propria data",
            expected="una rate_card del contratto valida su ogni data selezionata: "
            + ", ".join(str(w.id) for w in uncovered),
        )

    cards_by_id = {card.id: card for card in cards}
    righe: list[InvoiceLineIn] = []
    gruppi: list[list[UUID]] = []
    for (card_id, anno, mese), members in sorted(
        groups.items(), key=lambda item: (item[0][1], item[0][2], str(item[0][0]))
    ):
        card = cards_by_id[card_id]
        quantita: Decimal = sum((m.quantita for m in members), start=Decimal("0"))
        righe.append(
            InvoiceLineIn(
                descrizione=f"Attività {period_label(anno, mese)} - {quantita} {card.unita}",
                quantita=quantita,
                unita_misura=card.unita,
                prezzo_unitario=card.importo,
                # `aliquota_iva` deliberately omitted: `None` means "the regime's
                # answer" -- `natura`/`riferimento_normativo` are not on this schema
                # at all (see the module docstring).
            )
        )
        gruppi.append([m.id for m in members])
    return righe, gruppi


def bind_work_units(session: Session, gruppi: list[list[UUID]], line_ids: list[UUID]) -> None:
    """Sets `invoice_line_id` on every day in each group to its assembled line's id,
    positionally -- called only after `InvoiceService.replace_lines` has returned,
    so the ids being bound are real, persisted lines."""
    for member_ids, line_id in zip(gruppi, line_ids, strict=True):
        for work_unit_id in member_ids:
            work_unit = session.get(WorkUnit, work_unit_id)
            assert work_unit is not None, f"work_unit {work_unit_id} vanished mid-assembly"
            work_unit.invoice_line_id = line_id


def assemble_unbilled_work_units_into_new_invoice(
    session: Session, storage: DocumentStorage, contract_id: UUID, actor: Actor
) -> InvoiceRead:
    """The end-to-end common case: every unbilled, worked day on `contract_id`
    becomes one line each (grouped by rate card and month) on a fresh draft invoice
    for the contract's own customer, through `InvoiceService.create` then
    `replace_lines` -- mirroring `bind_time_to_invoice`'s own two-call shape -- and
    each day is bound to the line it produced.
    """
    contract = session.get(Contract, contract_id)
    if contract is None or contract.deleted_at is not None:
        raise NotFound("contract", contract_id)

    righe, gruppi = unbilled_work_unit_lines(session, contract_id)
    if not righe:
        raise ValidationFailed(
            ENTITY,
            "contract_id",
            "nessuna giornata lavorata e non ancora fatturata su questo contratto",
            expected=(
                f"almeno un work_unit 'lavorato' senza invoice_line_id sul contratto {contract_id}"
            ),
        )

    service = InvoiceService(session, storage)
    invoice = service.create(InvoiceCreate(customer_id=contract.customer_id, righe=[]), actor)
    invoice = service.replace_lines(invoice.id, righe, actor)

    line_ids = list(
        session.execute(
            select(InvoiceLine.id)
            .where(InvoiceLine.invoice_id == invoice.id)
            .order_by(InvoiceLine.numero_linea)
        ).scalars()
    )
    bind_work_units(session, gruppi, line_ids)
    session.commit()
    return invoice


__all__ = [
    "assemble_unbilled_work_units_into_new_invoice",
    "bind_work_units",
    "unbilled_work_unit_lines",
]
