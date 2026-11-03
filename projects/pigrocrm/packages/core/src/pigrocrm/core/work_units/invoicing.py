"""Spec §9's own call site: the unbilled-`work_unit`-to-`InvoiceLine` assembly step.

This is the foundations half REB-359 owns -- fee lines only, for a day with no
`applies_social_charge` awareness at all (that election, and the rivalsa line itself,
is a later issue's job, appended by a separate step onto whatever this one produces).
No schema change to `Invoice`/`InvoiceLine`: an approved, worked day becomes an
ordinary `InvoiceLine` on a draft that `invoices.assembly.assemble_draft` creates, the
same lines every other draft carries.

Mirrors `TimeBilling.bind_to_invoice` (`timetracking/billing.py`) in shape --
one line per rate card per month, for legibility, with the resulting line ids bound
back positionally -- but gathers automatically (`stato = 'lavorato' AND
invoice_line_id IS NULL`), the same predicate shape `won_with_unbilled_hours_predicate`
(`timetracking/repository.py`) already gives `TimeEntry`, rather than an explicit
caller-chosen id list: an approved, worked day is billable by definition the moment
nothing already covers it -- unlike an hour, invoicing which is `bind_to_invoice`'s
own commercial choice, a day was already the commercial decision when it moved to
`'worked'`.

Binding a day also moves it to `invoiced` (REB-751): the state is what a day's page
offers its moves from, and the one that keeps a day on an invoice from being revoked or
written off underneath it. A draft that is deleted or an invoice that is annulled gives
its days back (`release_days_of_invoice`), through the two edges the state graph has.

`assemble_unbilled_work_units_into_new_invoice` is the convenience path for the common
case: a contract with nothing else being assembled onto the same invoice. A caller that
also needs to append a rivalsa line or a rebillable expense to the *same* invoice
should use `unbilled_work_unit_lines` and `bind_work_units` directly around its own
single `assemble_draft` call instead -- `InvoiceLineIn` deliberately carries no
`vat_nature`/`legal_reference` (`invoices/schemas.py`), so an existing rivalsa line
read back and round-tripped through a second, independent line replacement would
silently lose the very fields that make it a rivalsa line.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.repository import RateCardRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.assembly import assemble_draft
from pigrocrm.core.invoices.schemas import CURRENCY, InvoiceCreate, InvoiceLineIn, InvoiceRead
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.money import round_money
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.timetracking.locks import period_label
from pigrocrm.core.work_units.billing_schemas import BillableDays, BillableLine
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.repository import WorkUnitRepository
from pigrocrm.core.work_units.service import set_transition_context

ENTITY = "work_unit"

BILL_ACTION = "bill_contract_days"

# What the day's own history says about why it moved: read by a person on the day's page,
# so in the words the page uses.
BOUND_REASON = "inserita in una bozza di fattura"

# The groups a contract's ready days fall into: `(rate_card, year, month)`, in the order
# the lines come out.
_GroupKey = tuple[UUID, int, int]


def _require_contract(session: Session, contract_id: UUID) -> Contract:
    contract = session.get(Contract, contract_id)
    if contract is None or contract.deleted_at is not None:
        raise NotFound("contract", contract_id)
    return contract


def _currency_block(contract: Contract) -> str | None:
    """Why this contract's days cannot become a draft, or `None`. Every invoice is issued in
    euros (`invoices.schemas.CURRENCY`) and nothing here converts: a draft of a USD contract
    would carry 500 EUR where the page promised 500 USD."""
    if contract.currency == CURRENCY:
        return None
    return (
        f"il contratto è in {contract.currency}: le fatture si emettono in {CURRENCY} e qui non "
        "si converte, quindi le giornate non si possono fatturare da qui"
    )


def _gather(
    session: Session, contract_id: UUID, da: date | None, a: date | None
) -> tuple[dict[_GroupKey, list[WorkUnit]], list[WorkUnit], dict[UUID, RateCard]]:
    """`(groups, uncovered, cards)`: the ready days grouped by the rate card in force on
    their own date and by month, the days no card covers, and the contract's cards by id."""
    work_units = WorkUnitRepository(session).unbilled_for_contract(contract_id, da=da, a=a)
    cards = RateCardRepository(session).list_for_contract(contract_id)

    groups: dict[_GroupKey, list[WorkUnit]] = defaultdict(list)
    uncovered: list[WorkUnit] = []
    for work_unit in work_units:
        card = next(
            (
                c
                for c in cards
                if c.valid_from <= work_unit.date
                and (c.valid_to is None or work_unit.date <= c.valid_to)
            ),
            None,
        )
        if card is None:
            uncovered.append(work_unit)
            continue
        groups[(card.id, work_unit.date.year, work_unit.date.month)].append(work_unit)
    return groups, uncovered, {card.id: card for card in cards}


def _ordered(groups: dict[_GroupKey, list[WorkUnit]]) -> list[tuple[_GroupKey, list[WorkUnit]]]:
    return sorted(groups.items(), key=lambda item: (item[0][1], item[0][2], str(item[0][0])))


def _quantity(members: list[WorkUnit]) -> Decimal:
    return sum((m.quantity for m in members), start=Decimal("0"))


def _line_in(card: RateCard, year: int, month: int, quantity: Decimal) -> InvoiceLineIn:
    return InvoiceLineIn(
        description=f"Attività {period_label(year, month)} - {quantity} {card.unit}",
        quantity=quantity,
        unit_of_measure=card.unit,
        unit_price=card.amount,
        # `vat_rate` deliberately omitted: `None` means "the regime's
        # answer" -- `vat_nature`/`legal_reference` are not on this schema
        # at all (see the module docstring).
    )


def unbilled_work_unit_lines(
    session: Session, contract_id: UUID, da: date | None = None, a: date | None = None
) -> tuple[list[InvoiceLineIn], list[list[UUID]]]:
    """`(righe, gruppi)`: one `InvoiceLineIn` per `(rate_card, month)` group of
    unbilled, worked days, and the ids each line prices, in the same order --
    positionally zippable with the line ids `replace_lines` produces
    (`bind_work_units`), the same mechanism `bind_to_invoice` uses for
    `TimeEntry` since ``invoices.lines.compute_lines`` numbers `numero_linea` from
    the order `lines` was built in. `da`/`a` (inclusive) take only the days of a period.

    Raises `ValidationFailed` if any gathered day has no rate card covering its own
    `data` -- inventing a price here would be a commercial decision this step does
    not own, the same refusal `bind_to_invoice` gives an unpriced `TimeEntry`.
    """
    groups, uncovered, cards = _gather(session, contract_id, da, a)
    if uncovered:
        raise ValidationFailed(
            ENTITY,
            "data",
            f"{len(uncovered)} giornate non hanno una rate_card in vigore alla propria data",
            expected="una rate_card del contratto valida su ogni data selezionata: "
            + ", ".join(str(w.id) for w in uncovered),
        )

    lines: list[InvoiceLineIn] = []
    gruppi: list[list[UUID]] = []
    for (card_id, year, month), members in _ordered(groups):
        lines.append(_line_in(cards[card_id], year, month, _quantity(members)))
        gruppi.append([m.id for m in members])
    return lines, gruppi


def billable_days(
    session: Session, contract_id: UUID, da: date | None = None, a: date | None = None
) -> BillableDays:
    """What `assemble_unbilled_work_units_into_new_invoice` would put on a draft, as
    figures a page can show before anybody presses the button. It builds the lines the
    way `unbilled_work_unit_lines` does, but names the days it cannot price instead of
    refusing, so the page can say which to fix."""
    contract = _require_contract(session, contract_id)
    groups, uncovered, cards = _gather(session, contract_id, da, a)

    lines: list[BillableLine] = []
    for (card_id, year, month), members in _ordered(groups):
        card = cards[card_id]
        quantity = _quantity(members)
        lines.append(
            BillableLine(
                month=f"{year:04d}-{month:02d}",
                description=_line_in(card, year, month, quantity).description,
                work_units=len(members),
                quantity=quantity,
                unit_of_measure=card.unit,
                unit_price=card.amount,
                amount=round_money(quantity * card.amount),
            )
        )
    without_rate = sorted({w.date for w in uncovered})
    return BillableDays(
        contract_id=contract.id,
        currency=contract.currency,
        lines=lines,
        months=sorted({riga.month for riga in lines}),
        work_units=sum(riga.work_units for riga in lines),
        quantity=sum((riga.quantity for riga in lines), start=Decimal("0")),
        taxable_amount=sum((riga.amount for riga in lines), start=Decimal("0")),
        without_rate=without_rate,
        without_approval=WorkUnitRepository(session).count_without_approval(contract_id),
        billable=bool(lines) and not without_rate and _currency_block(contract) is None,
        block_reason=_currency_block(contract),
    )


def bind_work_units(
    session: Session,
    groups: list[list[UUID]],
    line_ids: list[UUID],
    actor: Actor,
    reason: str = BOUND_REASON,
) -> None:
    """Sets `invoice_line_id` on every day in each group to its assembled line's id,
    positionally, and moves each to `invoiced` -- called only after
    `InvoiceService.replace_lines` has returned, so the ids being bound are real,
    persisted lines. The move goes through the state graph's own trigger, which logs it
    under `actor` and `reason`; it flushes, never commits.

    Each day is read again under a row lock (`populate_existing`, so a day already loaded
    by the gather is overwritten with the locked row, not returned stale) and refused
    unless it is still `worked` and on no line: the gather above ran in another
    transaction, and two requests (a double click, two tabs) can both have seen the same
    days as ready. The trigger would let the second one repoint the line of a day that
    keeps its state, so this is what makes the loser fail instead of leaving the first
    draft with lines whose days moved away."""
    set_transition_context(session, actor, reason)
    for member_ids, line_id in zip(groups, line_ids, strict=True):
        for work_unit_id in member_ids:
            work_unit = session.get(
                WorkUnit, work_unit_id, with_for_update=True, populate_existing=True
            )
            if work_unit is None:
                raise NotFound(ENTITY, work_unit_id)
            if work_unit.status != "worked" or work_unit.invoice_line_id is not None:
                raise Conflict(
                    ENTITY,
                    "una giornata è già stata fatturata nel frattempo: riprova",
                    work_unit_id=str(work_unit_id),
                )
            work_unit.invoice_line_id = line_id
            work_unit.status = "invoiced"
    session.flush()


def assemble_unbilled_work_units_into_new_invoice(
    session: Session,
    storage: DocumentStorage,
    contract_id: UUID,
    actor: Actor,
    da: date | None = None,
    a: date | None = None,
) -> InvoiceRead:
    """The end-to-end common case: every unbilled, worked day on `contract_id` (or those
    in `[da, a]`) becomes one line each (grouped by rate card and month) on a fresh draft
    invoice for the contract's own customer, under the contract's own azienda, and each
    day is bound to the line it produced. The draft, its lines and the binding are one
    transaction (`assemble_draft`): a day that was billed in the meantime refuses the
    whole thing and no draft is left behind.

    `admin` and never an agent (`actor.py`'s ban list, `test_mcp_invoice_ban.py`), the
    rung `bind_to_invoice` sits on: choosing what goes on a draft decides what an
    issue will freeze. It issues nothing.
    """
    actor.require_admin(BILL_ACTION)
    contract = _require_contract(session, contract_id)
    blocked = _currency_block(contract)
    if blocked is not None:
        raise ValidationFailed(ENTITY, "currency", blocked, expected=f"un contratto in {CURRENCY}")

    lines, groups = unbilled_work_unit_lines(session, contract_id, da, a)
    if not lines:
        raise ValidationFailed(
            ENTITY,
            "contract_id",
            "nessuna giornata lavorata e non ancora fatturata su questo contratto",
            expected=(
                f"almeno un work_unit 'lavorato' senza invoice_line_id sul contratto {contract_id}"
            ),
        )

    return assemble_draft(
        InvoiceService(session, storage),
        InvoiceCreate(customer_id=contract.customer_id, lines=lines),
        actor,
        legal_entity_id=contract.legal_entity_id,
        bind=lambda line_ids: bind_work_units(session, groups, line_ids, actor),
    )


__all__ = [
    "BILL_ACTION",
    "assemble_unbilled_work_units_into_new_invoice",
    "billable_days",
    "bind_work_units",
    "unbilled_work_unit_lines",
]
