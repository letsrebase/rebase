"""Spec §9's own call site: the unbilled-`work_unit`-to-`InvoiceLine` assembly step.

This is the foundations half REB-359 owns -- fee lines only, for a day with no
`applies_social_charge` awareness at all (that election, and the social security surcharge line
itself,
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
also needs to append a social security surcharge line or a rebillable expense to the *same* invoice
should use `unbilled_work_unit_lines` and `bind_work_units` directly around its own
single `assemble_draft` call instead -- `InvoiceLineIn` deliberately carries no
`vat_nature`/`legal_reference` (`invoices/schemas.py`), so an existing social security surcharge
line
read back and round-tripped through a second, independent line replacement would
silently lose the very fields that make it a social security surcharge line.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.due_items.forecast import ForecastService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.assembly import assemble_draft
from pigrocrm.core.invoices.schemas import CURRENCY, InvoiceCreate, InvoiceLineIn, InvoiceRead
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.labels import RATE_CARD_UNIT, label
from pigrocrm.core.money import round_money
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.work_units.billing_schemas import BillableDays, BillableLine
from pigrocrm.core.work_units.gathering import gather, line_in, ordered, quantity_of
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.repository import WorkUnitRepository
from pigrocrm.core.work_units.service import set_transition_context

ENTITY = "work_unit"

BILL_ACTION = "bill_contract_days"

# What the day's own history says about why it moved: read by a person on the day's page,
# so in the words the page uses.
BOUND_REASON = "inserita in una bozza di fattura"


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


def unbilled_work_unit_lines(
    session: Session, contract_id: UUID, date_from: date | None = None, date_to: date | None = None
) -> tuple[list[InvoiceLineIn], list[list[UUID]]]:
    """`(lines, groups)`: one `InvoiceLineIn` per `(rate_card, month)` group of
    unbilled, worked days, and the ids each line prices, in the same order --
    positionally zippable with the line ids `replace_lines` produces
    (`bind_work_units`), the same mechanism `bind_to_invoice` uses for
    `TimeEntry` since ``invoices.lines.compute_lines`` numbers `numero_linea` from
    the order `lines` was built in. `da`/`a` (inclusive) take only the days of a period.

    Raises `ValidationFailed` if any gathered day has no rate card covering its own
    `date` -- inventing a price here would be a commercial decision this step does
    not own, the same refusal `bind_to_invoice` gives an unpriced `TimeEntry`.
    """
    grouped, uncovered, cards = gather(session, contract_id, date_from, date_to)
    if uncovered:
        raise ValidationFailed(
            ENTITY,
            "date",
            f"{len(uncovered)} giornate non hanno una rate_card in vigore alla propria data",
            expected="una rate_card del contratto valida su ogni data selezionata: "
            + ", ".join(str(w.id) for w in uncovered),
        )

    lines: list[InvoiceLineIn] = []
    groups: list[list[UUID]] = []
    for (card_id, year, month), members in ordered(grouped):
        lines.append(line_in(cards[card_id], year, month, quantity_of(members)))
        groups.append([m.id for m in members])
    return lines, groups


def billable_days(
    session: Session, contract_id: UUID, date_from: date | None = None, date_to: date | None = None
) -> BillableDays:
    """What `assemble_unbilled_work_units_into_new_invoice` would put on a draft, as
    figures a page can show before anybody presses the button. It builds the lines the
    way `unbilled_work_unit_lines` does, but names the days it cannot price instead of
    refusing, so the page can say which to fix."""
    contract = _require_contract(session, contract_id)
    groups, uncovered, cards = gather(session, contract_id, date_from, date_to)

    lines: list[BillableLine] = []
    for (card_id, year, month), members in ordered(groups):
        card = cards[card_id]
        quantity = quantity_of(members)
        lines.append(
            BillableLine(
                month=f"{year:04d}-{month:02d}",
                description=line_in(card, year, month, quantity).description,
                work_units=len(members),
                quantity=quantity,
                unit_of_measure=label(RATE_CARD_UNIT, card.unit),
                unit_price=card.amount,
                amount=round_money(quantity * card.amount),
            )
        )
    without_rate = sorted({w.date for w in uncovered})
    return BillableDays(
        contract_id=contract.id,
        currency=contract.currency,
        lines=lines,
        months=sorted({line.month for line in lines}),
        work_units=sum(line.work_units for line in lines),
        quantity=sum((line.quantity for line in lines), start=Decimal("0")),
        taxable_amount=sum((line.amount for line in lines), start=Decimal("0")),
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
    forecasts = ForecastService(session)
    # The contracts before their days, as every writer of a worked day takes them (REB-997).
    forecasts.lock_contracts(
        set(
            session.scalars(
                select(WorkUnit.contract_id).where(
                    WorkUnit.id.in_([work_unit_id for group in groups for work_unit_id in group])
                )
            )
        )
    )
    contract_ids: set[UUID] = set()
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
            contract_ids.add(work_unit.contract_id)
    session.flush()
    # The days are on a line now, so the forecast of money in that counted them as unbilled is
    # replaced by the draft's own (REB-997).
    for contract_id in sorted(contract_ids, key=str):
        forecasts.refresh_contract(contract_id)


def assemble_unbilled_work_units_into_new_invoice(
    session: Session,
    storage: DocumentStorage,
    contract_id: UUID,
    actor: Actor,
    date_from: date | None = None,
    date_to: date | None = None,
) -> InvoiceRead:
    """The end-to-end common case: every unbilled, worked day on `contract_id` (or those
    in `[da, a]`) becomes one line each (grouped by rate card and month) on a fresh draft
    invoice for the contract's own customer, under the contract's own legal entity, and each
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

    lines, groups = unbilled_work_unit_lines(session, contract_id, date_from, date_to)
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
