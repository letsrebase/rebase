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

Binding a day also moves it to `fatturato` (REB-751): the state is what a day's page
offers its moves from, and the one that keeps a day on an invoice from being revoked or
written off underneath it. A draft that is deleted or an invoice that is annulled gives
its days back (`release_days_of_invoice`), through the two edges the state graph has.

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
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.repository import RateCardRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.invoices.schemas import DIVISA, InvoiceCreate, InvoiceLineIn, InvoiceRead
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
    euros (`invoices.schemas.DIVISA`) and nothing here converts: a draft of a USD contract
    would carry 500 EUR where the page promised 500 USD."""
    if contract.divisa == DIVISA:
        return None
    return (
        f"il contratto è in {contract.divisa}: le fatture si emettono in {DIVISA} e qui non "
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
                if c.valido_da <= work_unit.data
                and (c.valido_a is None or work_unit.data <= c.valido_a)
            ),
            None,
        )
        if card is None:
            uncovered.append(work_unit)
            continue
        groups[(card.id, work_unit.data.year, work_unit.data.month)].append(work_unit)
    return groups, uncovered, {card.id: card for card in cards}


def _ordered(groups: dict[_GroupKey, list[WorkUnit]]) -> list[tuple[_GroupKey, list[WorkUnit]]]:
    return sorted(groups.items(), key=lambda item: (item[0][1], item[0][2], str(item[0][0])))


def _quantity(members: list[WorkUnit]) -> Decimal:
    return sum((m.quantita for m in members), start=Decimal("0"))


def _line_in(card: RateCard, anno: int, mese: int, quantita: Decimal) -> InvoiceLineIn:
    return InvoiceLineIn(
        descrizione=f"Attività {period_label(anno, mese)} - {quantita} {card.unita}",
        quantita=quantita,
        unita_misura=card.unita,
        prezzo_unitario=card.importo,
        # `aliquota_iva` deliberately omitted: `None` means "the regime's
        # answer" -- `natura`/`riferimento_normativo` are not on this schema
        # at all (see the module docstring).
    )


def unbilled_work_unit_lines(
    session: Session, contract_id: UUID, da: date | None = None, a: date | None = None
) -> tuple[list[InvoiceLineIn], list[list[UUID]]]:
    """`(righe, gruppi)`: one `InvoiceLineIn` per `(rate_card, month)` group of
    unbilled, worked days, and the ids each line prices, in the same order --
    positionally zippable with the line ids `replace_lines` produces
    (`bind_work_units`), the same mechanism `bind_time_to_invoice` uses for
    `TimeEntry` since `InvoiceService._computed_lines` numbers `numero_linea` from
    the order `righe` was built in. `da`/`a` (inclusive) take only the days of a period.

    Raises `ValidationFailed` if any gathered day has no rate card covering its own
    `data` -- inventing a price here would be a commercial decision this step does
    not own, the same refusal `bind_time_to_invoice` gives an unpriced `TimeEntry`.
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

    righe: list[InvoiceLineIn] = []
    gruppi: list[list[UUID]] = []
    for (card_id, anno, mese), members in _ordered(groups):
        righe.append(_line_in(cards[card_id], anno, mese, _quantity(members)))
        gruppi.append([m.id for m in members])
    return righe, gruppi


def billable_days(
    session: Session, contract_id: UUID, da: date | None = None, a: date | None = None
) -> BillableDays:
    """What `assemble_unbilled_work_units_into_new_invoice` would put on a draft, as
    figures a page can show before anybody presses the button. It builds the lines the
    way `unbilled_work_unit_lines` does, but names the days it cannot price instead of
    refusing, so the page can say which to fix."""
    contract = _require_contract(session, contract_id)
    groups, uncovered, cards = _gather(session, contract_id, da, a)

    righe: list[BillableLine] = []
    for (card_id, anno, mese), members in _ordered(groups):
        card = cards[card_id]
        quantita = _quantity(members)
        righe.append(
            BillableLine(
                mese=f"{anno:04d}-{mese:02d}",
                descrizione=_line_in(card, anno, mese, quantita).descrizione,
                giornate=len(members),
                quantita=quantita,
                unita_misura=card.unita,
                prezzo_unitario=card.importo,
                importo=round_money(quantita * card.importo),
            )
        )
    senza_tariffa = sorted({w.data for w in uncovered})
    return BillableDays(
        contract_id=contract.id,
        divisa=contract.divisa,
        righe=righe,
        mesi=sorted({riga.mese for riga in righe}),
        giornate=sum(riga.giornate for riga in righe),
        quantita=sum((riga.quantita for riga in righe), start=Decimal("0")),
        imponibile=sum((riga.importo for riga in righe), start=Decimal("0")),
        senza_tariffa=senza_tariffa,
        senza_approvazione=WorkUnitRepository(session).count_without_approval(contract_id),
        fatturabile=bool(righe) and not senza_tariffa and _currency_block(contract) is None,
        motivo_blocco=_currency_block(contract),
    )


def bind_work_units(
    session: Session,
    gruppi: list[list[UUID]],
    line_ids: list[UUID],
    actor: Actor,
    motivo: str = BOUND_REASON,
) -> None:
    """Sets `invoice_line_id` on every day in each group to its assembled line's id,
    positionally, and moves each to `fatturato` -- called only after
    `InvoiceService.replace_lines` has returned, so the ids being bound are real,
    persisted lines. The move goes through the state graph's own trigger, which logs it
    under `actor` and `motivo`; it flushes, never commits.

    Each day is read again under a row lock and refused unless it is still `lavorato` and
    on no line: the gather above ran in another transaction, and two requests (a double
    click, two tabs) can both have seen the same days as ready. The trigger would let the
    second one repoint the line of a day that keeps its state, so this is what makes the
    loser fail instead of leaving the first draft with lines whose days moved away."""
    set_transition_context(session, actor, motivo)
    for member_ids, line_id in zip(gruppi, line_ids, strict=True):
        for work_unit_id in member_ids:
            work_unit = session.get(WorkUnit, work_unit_id, with_for_update=True)
            assert work_unit is not None, f"work_unit {work_unit_id} vanished mid-assembly"
            if work_unit.stato != "lavorato" or work_unit.invoice_line_id is not None:
                raise Conflict(
                    ENTITY,
                    "una giornata è già stata fatturata nel frattempo: riprova",
                    work_unit_id=str(work_unit_id),
                )
            work_unit.invoice_line_id = line_id
            work_unit.stato = "fatturato"
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
    invoice for the contract's own customer, through `InvoiceService.create` then
    `replace_lines` -- mirroring `bind_time_to_invoice`'s own two-call shape -- and
    each day is bound to the line it produced.

    `admin` and never an agent (`actor.py`'s ban list, `test_mcp_invoice_ban.py`), the
    rung `bind_time_to_invoice` sits on: choosing what goes on a draft decides what an
    issue will freeze. It issues nothing.
    """
    actor.require_admin(BILL_ACTION)
    contract = _require_contract(session, contract_id)
    blocked = _currency_block(contract)
    if blocked is not None:
        raise ValidationFailed(ENTITY, "divisa", blocked, expected=f"un contratto in {DIVISA}")

    righe, gruppi = unbilled_work_unit_lines(session, contract_id, da, a)
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
    invoice = service.create(
        InvoiceCreate(customer_id=contract.customer_id, righe=[]),
        actor,
        azienda_id=contract.azienda_id,
    )
    invoice = service.replace_lines(invoice.id, righe, actor)

    line_ids = list(
        session.execute(
            select(InvoiceLine.id)
            .where(InvoiceLine.invoice_id == invoice.id)
            .order_by(InvoiceLine.numero_linea)
        ).scalars()
    )
    try:
        bind_work_units(session, gruppi, line_ids, actor)
        session.commit()
    except Exception:
        # The draft and its lines are already committed (`create` and `replace_lines` commit
        # on their own), so a failure here would leave a draft billing days that are still
        # ready, and a retry would bill them a second time. Take the draft back out.
        session.rollback()
        service.soft_delete(invoice.id, actor)
        raise
    return invoice


__all__ = [
    "BILL_ACTION",
    "assemble_unbilled_work_units_into_new_invoice",
    "billable_days",
    "bind_work_units",
    "unbilled_work_unit_lines",
]
