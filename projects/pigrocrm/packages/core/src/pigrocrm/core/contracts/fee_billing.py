"""Billing one occurrence of a contract's recurring fee (REB-1032).

A `fixed_recurring` rate card of an active contract writes a forecast of money in for each of its
occurrences (`due_items/forecast.py`, REB-997), and an invoice replaces that forecast only when its
line names the rate card and the period it bills (`InvoiceLineIn.rate_card_id` and `period_start`).
This is the action that writes such an invoice: a draft with the card's fee, its amount and its
period on the one line, so nobody types two fields right by hand. It issues nothing: the draft is
read, edited and issued like any other, and issuing it is what replaces the forecast with the
invoice's own due item.

**One occurrence, one document.** An occurrence that a draft invoice or a proforma already bills is
answered with that document (`created` false) instead of a second one; one an issued invoice bills
is refused, naming the invoice; a deleted draft or an annulled invoice frees it again. The contract
row is locked first, as every writer that decides on a contract's forecasts does, so two calls for
the same occurrence queue: the second one finds the draft the first one made.

`occurrences` is the read behind the contract's page: each occurrence with the document that bills
it, so a row can offer the button or say where the invoice is.
"""

import datetime as dt
from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.fee_schemas import (
    FeeBilled,
    FeeBillRequest,
    FeeOccurrence,
    FeeOccurrenceInvoice,
    FeeOccurrences,
    FeeState,
)
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.projection import RECURRING_FEE_TYPE
from pigrocrm.core.contracts.repository import RateCardRepository
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.due_items.forecast import (
    BILLING_STATUSES,
    HORIZON_DAYS,
    ForecastService,
    occurrence_spans,
    period_covered,
)
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import CURRENCY, InvoiceCreate, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.base import DocumentStorage

ENTITY = "contract"

BILL_FEE_ACTION = "bill_contract_fee"

# What the contract's page lists by default: the occurrences of the last three months, which are
# the ones somebody may have forgotten, and the ones the forecasts already draw ahead.
LOOKBACK_DAYS = 90
# The ceiling of a window, the engagements report's own (`REPORT_MAX_DAYS`): a card with years of
# monthly occurrences is not listed whole by accident.
MAX_WINDOW_DAYS = 800
# How many valid dates a refused one names, so a caller can correct itself.
SUGGESTED_DATES = 6

DESCRIPTION_MAX_LENGTH = 200

# What a contract that is not in force for billing says, in the words a person reads.
NOT_STARTED = "il contratto è ancora una bozza: i canoni si fatturano da un contratto attivo"


def _block(contract: Contract) -> str | None:
    """Why no occurrence of this contract can be billed from here, or `None`."""
    if contract.status == "draft":
        return NOT_STARTED
    if contract.currency != CURRENCY:
        return (
            f"il contratto è in {contract.currency}: le fatture si emettono in {CURRENCY} e qui "
            "non si converte, quindi i canoni non si possono fatturare da qui"
        )
    return None


def _period_end(contract: Contract, card: RateCard, following: dt.date | None) -> dt.date | None:
    """The last day a fee covers: the day before the next occurrence, cut where the card or the
    contract ends first. A one-off covers no period."""
    if following is None:
        return None
    end = following - dt.timedelta(days=1)
    for limit in (card.valid_to, contract.end):
        if limit is not None and limit < end:
            end = limit
    return end


def _description(contract: Contract, day: dt.date, end: dt.date | None) -> str:
    """The line's words: the contract and the days the fee covers (`del` the day for a one-off)."""
    when = (
        f"dal {day.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}"
        if end is not None
        else f"del {day.strftime('%d/%m/%Y')}"
    )
    prefix, suffix = "Canone ", f" {when}"
    room = DESCRIPTION_MAX_LENGTH - len(prefix) - len(suffix)
    return f"{prefix}{contract.title[:room]}{suffix}"


class FeeBillingService:
    def __init__(self, session: Session, storage: DocumentStorage | None = None) -> None:
        self.session = session
        self.storage = storage
        self.activities = ActivityService(session)

    # ---- reading ----------------------------------------------------------------------------

    def occurrences(
        self,
        contract_id: UUID,
        *,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> FeeOccurrences:
        """The occurrences of the contract's recurring fee cards in `[date_from, date_to]`, each
        with the document that bills it. The window defaults to the last `LOOKBACK_DAYS` days
        and the forecasts' own horizon ahead."""
        contract = self._contract(contract_id)
        today = today_local()
        first = date_from if date_from is not None else today - dt.timedelta(days=LOOKBACK_DAYS)
        last = date_to if date_to is not None else today + dt.timedelta(days=HORIZON_DAYS)
        if last < first:
            raise ValidationFailed(
                ENTITY, "to", "la fine precede l'inizio", expected="una data dopo quella di inizio"
            )
        if (last - first).days > MAX_WINDOW_DAYS:
            raise ValidationFailed(
                ENTITY,
                "to",
                f"il periodo supera {MAX_WINDOW_DAYS} giorni",
                expected=f"al massimo {MAX_WINDOW_DAYS} giorni tra l'inizio e la fine",
            )
        cards = self._fee_cards(contract.id)
        documents = self._documents([card.id for card in cards])
        rows: list[FeeOccurrence] = []
        for card in cards:
            for day, following in occurrence_spans(contract, card, last):
                if day < first:
                    continue
                document = _billing_document(day, following, documents.get(card.id, []))
                rows.append(
                    FeeOccurrence(
                        rate_card_id=card.id,
                        period_start=day,
                        period_end=_period_end(contract, card, following),
                        delivery_period=card.delivery_period or "",
                        amount=card.amount,
                        state=_state(document),
                        invoice=_invoice_ref(document),
                    )
                )
        rows.sort(key=lambda row: (row.period_start, str(row.rate_card_id)))
        reason = _block(contract)
        return FeeOccurrences(
            contract_id=contract.id,
            contract_status=contract.status,
            currency=contract.currency,
            billable=reason is None,
            block_reason=reason,
            date_from=first,
            date_to=last,
            occurrences=rows,
        )

    # ---- billing ----------------------------------------------------------------------------

    def bill(self, contract_id: UUID, request: FeeBillRequest, actor: Actor) -> FeeBilled:
        """The draft invoice that bills the occurrence `request` names, or the draft or proforma
        that already does. `admin` and a `full_access` token, like the days (`bill_contract_days`):
        what is on a draft decides what issuing will freeze. It issues nothing."""
        actor.require_admin(BILL_FEE_ACTION)
        # The contract row before anything is read or decided, as every writer of its forecasts
        # does: a second call for the same occurrence waits here and then finds the first's draft.
        ForecastService(self.session).lock_contracts([contract_id])
        contract = self._contract(contract_id)
        reason = _block(contract)
        if reason is not None:
            raise ValidationFailed(
                ENTITY, "status" if contract.status == "draft" else "currency", reason
            )
        card = self._fee_card(contract, request.rate_card_id)
        following = self._span_of(contract, card, request.period_start)

        document = _billing_document(
            request.period_start, following, self._documents([card.id]).get(card.id, [])
        )
        invoices = self._invoices()
        if document is not None:
            if document.status == "issued":
                raise Conflict(
                    ENTITY,
                    "il canone di questo periodo è già in una fattura emessa",
                    invoice_id=str(document.id),
                    year=document.year,
                    number=document.number,
                )
            return FeeBilled(created=False, invoice=invoices.get(document.id, actor))

        line = InvoiceLineIn(
            description=_description(
                contract, request.period_start, _period_end(contract, card, following)
            ),
            unit_price=card.amount,
            rate_card_id=card.id,
            period_start=request.period_start,
        )
        draft = invoices.create(
            InvoiceCreate(customer_id=contract.customer_id, lines=[line]),
            actor,
            legal_entity_id=contract.legal_entity_id,
            commit=False,
        )
        # Last before the commit, as `ActivityService.record` asks: the contract's timeline says
        # which fee was billed on which draft, in the same transaction as the draft.
        self.activities.record(
            ENTITY,
            contract.id,
            "fee_billed",
            actor,
            {
                "rate_card_id": str(card.id),
                "period_start": request.period_start.isoformat(),
                "invoice_id": str(draft.id),
            },
        )
        self.session.commit()
        return FeeBilled(created=True, invoice=draft)

    # ---- the pieces -------------------------------------------------------------------------

    def _invoices(self) -> InvoiceService:
        if self.storage is None:
            raise RuntimeError("a fee is billed through an invoice service, which needs storage")
        return InvoiceService(self.session, self.storage)

    def _contract(self, contract_id: UUID) -> Contract:
        contract = self.session.get(Contract, contract_id, populate_existing=True)
        if contract is None or contract.deleted_at is not None:
            raise NotFound(ENTITY, contract_id)
        return contract

    def _fee_cards(self, contract_id: UUID) -> list[RateCard]:
        return [
            card
            for card in RateCardRepository(self.session).list_for_contract(contract_id)
            if card.type == RECURRING_FEE_TYPE and card.delivery_period is not None
        ]

    def _fee_card(self, contract: Contract, rate_card_id: UUID) -> RateCard:
        card = self.session.get(RateCard, rate_card_id)
        if card is None or card.contract_id != contract.id:
            raise NotFound("rate_card", rate_card_id)
        if card.type != RECURRING_FEE_TYPE or card.delivery_period is None:
            raise ValidationFailed(
                ENTITY,
                "rate_card_id",
                "la scheda tariffa non è un canone ricorrente",
                expected="una scheda tariffa di tipo canone ricorrente con la sua periodicità",
            )
        return card

    def _span_of(self, contract: Contract, card: RateCard, day: dt.date) -> dt.date | None:
        """The day the occurrence after `day` falls on, once `day` is one of the card's own: the
        period a line names has to be a date the schedule actually produces."""
        for occurrence, following in occurrence_spans(contract, card, day):
            if occurrence == day:
                return following
        today = today_local()
        nearby = sorted(
            (
                occurrence
                for occurrence, _following in occurrence_spans(
                    contract, card, max(day, today) + dt.timedelta(days=HORIZON_DAYS)
                )
            ),
            key=lambda occurrence: abs((occurrence - day).days),
        )[:SUGGESTED_DATES]
        raise ValidationFailed(
            ENTITY,
            "period_start",
            "la data non è una scadenza del canone di questa scheda tariffa",
            expected=(
                "una scadenza del canone: "
                + ", ".join(candidate.isoformat() for candidate in sorted(nearby))
                if nearby
                else "una scheda tariffa le cui scadenze cadono nel periodo del contratto"
            ),
        )

    def _documents(self, card_ids: Collection[UUID]) -> dict[UUID, list[tuple[dt.date, Invoice]]]:
        """The drafts, confirmed proformas and issued invoices whose lines bill a period of each
        card, the issued ones first and the oldest first among equals: the one an occurrence is
        shown under when two documents cover it."""
        if not card_ids:
            return {}
        rows = self.session.execute(
            select(InvoiceLine.rate_card_id, InvoiceLine.period_start, Invoice)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(
                InvoiceLine.rate_card_id.in_(card_ids),
                InvoiceLine.period_start.is_not(None),
                Invoice.deleted_at.is_(None),
                Invoice.status.in_(BILLING_STATUSES),
            )
            .order_by(Invoice.status != "issued", Invoice.created_at, Invoice.id)
        )
        found: dict[UUID, list[tuple[dt.date, Invoice]]] = {}
        for card_id, period, invoice in rows:
            if card_id is not None and period is not None:
                found.setdefault(card_id, []).append((period, invoice))
        return found


def _billing_document(
    day: dt.date, following: dt.date | None, billed: list[tuple[dt.date, Invoice]]
) -> Invoice | None:
    """The document whose line bills the occurrence at `day`, by the same span the forecasts use."""
    for period, invoice in billed:
        if period_covered(day, following, [period]):
            return invoice
    return None


def _state(document: Invoice | None) -> FeeState:
    if document is None:
        return "to_bill"
    return "issued" if document.status == "issued" else "drafted"


def _invoice_ref(document: Invoice | None) -> FeeOccurrenceInvoice | None:
    if document is None:
        return None
    return FeeOccurrenceInvoice(
        id=document.id,
        type=document.type,
        status=document.status,
        year=document.year,
        number=document.number,
        reference=document.reference,
    )
