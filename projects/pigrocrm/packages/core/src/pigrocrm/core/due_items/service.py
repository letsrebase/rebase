"""The only writer of `due_items` and `due_item_settlements` (spec 2026-11-09, due items,
§ 1 and § 5).

Two kinds of method, told apart by the signature:

* **The writer's half** takes no actor: `create`, `drop`, `reschedule` and
  `attach_dependency`. The service that owns a source (an invoice, a purchase, a tax) calls
  them inside its own transaction, after it has done its own authorisation, and commits
  itself: they flush and never commit.
* **The surface's half** takes an actor: `settle`, `reverse_settlement`, `get` and `list`.
  They authorise, commit, and answer what a route or a tool returns.

Who may settle depends on what is settled. An incoming item (a client paying) is a
collaborator's and an agent's, as `set_payment_state` is today. An outgoing item (a supplier
payment marked paid) and every item of a fiscal source kind (`RESTRICTED_SOURCE_KINDS`) is an
administrator's and, for an agent, `full_access`'s. Reading follows the same line: any member
reads the ordinary kinds, only an administrator the fiscal ones.
"""

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.models import BankMovement
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import (
    CARRY_TO_CONFIRM_SUFFIX,
    CHAMBER_FEE_SOURCE_KIND,
    EXPENSE_NOTE_SOURCE_KIND,
    OPEN_STATES,
    RESTRICTED_SOURCE_KINDS,
    SOURCE_KINDS,
    TAX_ADVANCE_SOURCE_KIND,
    TAX_BALANCE_SOURCE_KIND,
    TAX_INSTALMENT_SOURCE_KIND,
    DueItem,
    DueItemSettlement,
)
from pigrocrm.core.due_items.repository import DueItemRepository
from pigrocrm.core.due_items.rules import Anchor, resolve
from pigrocrm.core.due_items.schemas import (
    DueItemCreate,
    DueItemDetail,
    DueItemListQuery,
    DueItemPage,
    DueItemRead,
    PayrollTotalLine,
    PayrollTotals,
    PayrollTotalsQuery,
    SettlementCreate,
    SettlementRead,
)
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    ImmutableField,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.expense_notes.access import own_staff_member_id, require_may_pay_item
from pigrocrm.core.expense_notes.projection import project_reimbursement
from pigrocrm.core.fiscal.f24_repository import F24GroupRepository
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity

ENTITY = "due_item"
SETTLEMENT_ENTITY = "due_item_settlement"
ZERO = Decimal("0.00")
SALES_INVOICE = "sales_invoice"
# The tax kinds whose forecast may be paid as it stands: no document replaces it. An instalment
# of a plan over an estimate is one of them (REB-1075).
TAX_SOURCE_KINDS_PAYABLE = (
    TAX_BALANCE_SOURCE_KIND,
    TAX_ADVANCE_SOURCE_KIND,
    CHAMBER_FEE_SOURCE_KIND,
    TAX_INSTALMENT_SOURCE_KIND,
)


def _is_fiscal(item: DueItem) -> bool:
    return item.source_kind in RESTRICTED_SOURCE_KINDS


def _require_settle_permission(actor: Actor, item: DueItem, *, undo: bool) -> None:
    """Collaborator and agent-allowed for a client paying; administrator, and `full_access`
    for an agent, for money going out and for anything fiscal."""
    outgoing = item.direction == "out" or _is_fiscal(item)
    # Literal keys on purpose: `test_agent_actions.py` and the web's permission table read
    # the action strings off the source.
    if undo and outgoing:
        actor.require_admin("reverse_outgoing_settlement")
    elif undo:
        actor.require_write("reverse_settlement")
    elif outgoing:
        actor.require_admin("settle_outgoing_due_item")
    else:
        actor.require_write("settle_due_item")


def _may_read_fiscal(actor: Actor) -> bool:
    try:
        actor.require_admin("read_fiscal_due_items")
    except (PermissionDenied, AgentForbidden):
        return False
    return True


class DueItemService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = DueItemRepository(session)
        self.groups = F24GroupRepository(session)
        self.activities = ActivityService(session)

    # ---- the writer's half: no actor, no commit -------------------------------------------

    def create(self, data: DueItemCreate, *, check_organization: bool = True) -> DueItemRead:
        """Records an amount that falls due. The caller commits, with the document it is for.
        `check_organization=False` is for a document that is billed under another legal entity
        than its customer's, as a contract's days are (see `open_for_invoice`)."""
        if data.source_kind not in SOURCE_KINDS:
            raise ValidationFailed(
                ENTITY,
                "source_kind",
                "tipo di origine sconosciuto",
                expected=", ".join(SOURCE_KINDS),
            )
        if self.session.get(LegalEntity, data.legal_entity_id) is None:
            raise NotFound("legal_entity", data.legal_entity_id)
        if data.organization_id is not None and check_organization:
            self._require_organization_of(data.organization_id, data.legal_entity_id)
        if data.staff_member_id is not None:
            self._require_staff_member_of(data.staff_member_id, data.legal_entity_id)

        anchor: Anchor | None = None
        if data.rule.depends_on is not None:
            # Locked: a settlement of the dependency re-dates its dependents under the same
            # lock, so the date read here cannot be one that is about to change.
            dependency = self.repo.get_locked(data.rule.depends_on)
            if dependency is None:
                raise NotFound(ENTITY, data.rule.depends_on)
            if dependency.legal_entity_id != data.legal_entity_id:
                raise ValidationFailed(
                    ENTITY,
                    "rule.depends_on",
                    "la scadenza da cui dipende e' di un'altra azienda",
                    expected="una scadenza della stessa azienda",
                )
            anchor = self._anchor(dependency)

        resolved = resolve(
            data.rule.kind,
            own_date=data.own_date,
            offset_days=data.rule.offset_days,
            day_of_month=data.rule.day_of_month,
            not_after=data.rule.not_after,
            anchor=anchor,
        )
        item = self.repo.add(
            DueItem(
                legal_entity_id=data.legal_entity_id,
                direction=data.direction,
                state=data.state,
                counterparty_type=data.counterparty_type,
                organization_id=data.organization_id,
                staff_member_id=data.staff_member_id,
                tax=data.tax,
                amount=data.amount,
                currency=data.currency,
                description=data.description,
                source_kind=data.source_kind,
                source_id=data.source_id,
                source_rule=data.source_rule,
                forecast_key=data.forecast_key,
                own_date=data.own_date,
                due_date=resolved.date,
                due_date_estimated=resolved.estimated,
                rule_kind=data.rule.kind,
                depends_on_id=data.rule.depends_on,
                rule_offset_days=data.rule.offset_days,
                rule_day_of_month=data.rule.day_of_month,
                rule_not_after=data.rule.not_after,
            )
        )
        return self._read(item)

    def revise_forecast(
        self,
        item_id: UUID,
        *,
        amount: Decimal,
        own_date: dt.date | None,
        description: str | None,
        organization_id: UUID,
    ) -> DueItemRead:
        """Brings a live forecast to what its rule produces now: the amount, the date, the
        description and the organisation. Only a forecast is revised, since a document fixes
        the other states; a forecast has no settlements, so nothing else depends on it. Flushes
        and never commits; answers without writing when nothing changed."""
        item = self._locked(item_id)
        if item.state != "forecast":
            raise Conflict(ENTITY, "solo una previsione si rivede", id=str(item.id))
        if amount == ZERO:
            raise ValidationFailed(
                ENTITY, "amount", "l'importo di una scadenza non e' zero", expected="un importo"
            )
        description = description[:255] if description else None
        if (item.amount, item.own_date, item.description, item.organization_id) != (
            amount,
            own_date,
            description,
            organization_id,
        ):
            item.amount = amount
            item.own_date = own_date
            item.description = description
            item.organization_id = organization_id
            self.session.flush()
            self._redate(item)
            self._follow(item)
        return self._read(item)

    def drop(self, item_id: UUID, reason: str | None = None) -> DueItemRead:
        """The item is no longer owed (a credit note, a contract that ended). Idempotent. An
        item with a payment recorded is not dropped: the payment is reversed first, or the
        item is reduced by a credit note, which is another operation."""
        item = self._locked(item_id)
        if item.state == "dropped":
            return self._read(item)
        if item.state == "settled" or self.repo.totals(item.id).settled != ZERO:
            raise Conflict(
                ENTITY,
                "una scadenza con pagamenti registrati non si scarta: prima si annullano",
                id=str(item.id),
            )
        self._require_not_grouped(item)
        item.state = "dropped"
        item.dropped_reason = reason[:255] if reason else None
        self.session.flush()
        self._follow(item)
        return self._read(item)

    def restate(self, item_id: UUID, *, amount: Decimal, own_date: dt.date) -> DueItemRead:
        """Corrects what a computed source owes: the IVA settlement of a period recomputed
        after its registers moved. The amount and the date follow while nothing was paid on
        the item, and a dropped item owed again is reopened; an item with a payment recorded
        is left alone when the figures agree and refused when they do not, because a payment
        reversed is a person's decision and not a recomputation's."""
        item = self._locked(item_id)
        unchanged = (item.amount, item.own_date) == (amount, own_date)
        if unchanged and item.state != "dropped":
            return self._read(item)
        if item.state == "settled" or self.repo.totals(item.id).settled != ZERO:
            raise Conflict(
                ENTITY,
                "una scadenza con pagamenti registrati non si ricalcola: prima si annullano",
                id=str(item.id),
            )
        self._require_not_grouped(item)
        item.amount = amount
        item.own_date = own_date
        item.state = "expected"
        item.dropped_reason = None
        self.session.flush()
        self._redate(item)
        self._follow(item)
        return self._read(item)

    def reschedule(self, item_id: UUID, due_date: dt.date | None) -> DueItemRead:
        """Moves an item's own date. A sales item's date is the invoice's and never moves
        (`MUTABLE_AFTER_ISSUE`): only an outgoing item and a forecast can be rescheduled."""
        item = self._locked(item_id)
        self._require_not_grouped(item)
        if item.state not in OPEN_STATES:
            raise Conflict(ENTITY, "una scadenza saldata o scartata non si sposta", id=str(item.id))
        if item.direction == "in" and item.state == "expected":
            raise ImmutableField(
                ENTITY, "due_date", "la scadenza di un incasso e' quella del documento"
            )
        if item.rule_kind not in ("fixed", "after_settlement"):
            raise ImmutableField(
                ENTITY, "due_date", "questa regola data la scadenza dal pagamento da cui dipende"
            )
        item.own_date = due_date
        self.session.flush()
        self._redate(item)
        self._follow(item)
        return self._read(item)

    def attach_dependency(self, item_id: UUID, depends_on_id: UUID) -> DueItemRead:
        """Gives a waiting item (an `after_settlement` rule with a backstop and no dependency
        yet) the item it waits for, once that exists, and dates it from it."""
        item = self._locked(item_id)
        if (
            item.rule_kind != "after_settlement"
            or item.depends_on_id is not None
            or item.state not in OPEN_STATES
        ):
            raise Conflict(
                ENTITY,
                "solo una scadenza aperta che aspetta un pagamento non ancora noto ne ha uno",
                id=str(item.id),
            )
        return self._point_at(item, depends_on_id)

    def repoint_dependency(self, item_id: UUID, depends_on_id: UUID) -> DueItemRead:
        """Moves an open `after_settlement` item that already follows one item to follow
        another: the engagement it serves was linked to another client, or a later invoice now
        covers the same period. Without a change it is a no-op."""
        item = self._locked(item_id)
        if (
            item.rule_kind != "after_settlement"
            or item.depends_on_id is None
            or item.state not in OPEN_STATES
        ):
            raise Conflict(
                ENTITY,
                "solo una scadenza aperta che segue un pagamento puo' passare a seguirne un altro",
                id=str(item.id),
            )
        if item.depends_on_id == depends_on_id:
            return self._read(item)
        return self._point_at(item, depends_on_id)

    def _point_at(self, item: DueItem, depends_on_id: UUID) -> DueItemRead:
        dependency = self.repo.get_locked(depends_on_id)
        if dependency is None:
            raise NotFound(ENTITY, depends_on_id)
        if dependency.legal_entity_id != item.legal_entity_id:
            raise ValidationFailed(
                ENTITY,
                "rule.depends_on",
                "la scadenza da cui dipende e' di un'altra azienda",
                expected="una scadenza della stessa azienda",
            )
        # A rule names an item that already exists, so a chain cannot loop; the walk is the
        # proof for the one case that could: the dependency already follows this item.
        cursor: DueItem | None = dependency
        while cursor is not None:
            if cursor.id == item.id:
                raise ValidationFailed(
                    ENTITY, "rule.depends_on", "la dipendenza formerebbe un giro chiuso"
                )
            cursor = (
                self.repo.get(cursor.depends_on_id) if cursor.depends_on_id is not None else None
            )
        item.depends_on_id = dependency.id
        self.session.flush()
        self._redate(item)
        self._follow(item)
        return self._read(item)

    # ---- the issued invoices (§ 1.1): the writer's half, for the invoice's own service ----

    def open_for_invoice(
        self, invoice: Invoice, *, paid_on: dt.date | None, actor: Actor
    ) -> DueItem | None:
        """The due item of an issued invoice, created once and left alone after that: the
        amount is the invoice's receivable (its net of withholding and split payment, REB-994),
        the date is its frozen `due_date`, and an invoice that arrives already collected (an
        import, a row of the previous release) is settled in full on `paid_on`. Answers `None`
        for an invoice with nothing to collect. Flushes
        and never commits: the caller's transaction is the invoice's."""
        existing = self.repo.by_source(SALES_INVOICE, invoice.id)
        if existing is not None:
            return existing
        if invoice.net_receivable == 0:
            return None
        item = self.repo.add(
            DueItem(
                legal_entity_id=invoice.legal_entity_id,
                direction="in",
                state="expected",
                counterparty_type="organization",
                # Not `_require_organization_of`: a contract's days are billed under the
                # contract's own legal entity, which a customer moved since no longer shares,
                # and the invoice must still get its item.
                organization_id=invoice.customer_id,
                amount=invoice.net_receivable,
                currency=invoice.currency,
                source_kind=SALES_INVOICE,
                source_id=invoice.id,
                own_date=invoice.due_date,
                due_date=invoice.due_date,
            )
        )
        if paid_on is not None:
            self._add_settlement(item, item.amount, paid_on, actor)
            item.state = "settled"
            self.session.flush()
        self._project(item)
        return item

    def reconcile_invoice(self, invoice: Invoice) -> DueItem | None:
        """`open_for_invoice` for an invoice the previous release wrote: its own columns say
        whether it was collected, and an invoice marked paid with no date is settled on its
        issue date, the fallback every reader had for it."""
        paid_on = None
        if invoice.payment_status == "paid":
            paid_on = invoice.payment_date or invoice.issue_date
        return self.open_for_invoice(invoice, paid_on=paid_on, actor=Actor.system())

    def record_invoice_payment(
        self, invoice: Invoice, *, paid: bool, paid_on: dt.date | None, actor: Actor
    ) -> None:
        """`set_invoice_payment_state`'s write: «paid on `paid_on`» settles what remains on
        that date (a payment already partly made keeps its history), «unpaid» reverses every
        live settlement. Then the invoice's two columns are projected from the item. The
        caller has locked the invoice and commits."""
        item = self.open_for_invoice(invoice, paid_on=None, actor=actor)
        if item is None:
            # Nothing to collect: the columns are all there is, and this is still their
            # only writer.
            invoice.payment_status = "paid" if paid else "unpaid"
            invoice.payment_date = paid_on if paid else None
            return
        item = self._locked_item(item.id)
        if item.state == "dropped":
            raise Conflict(
                ENTITY, "la scadenza di questa fattura e' stata scartata", id=str(item.id)
            )
        if paid:
            assert paid_on is not None
            self._settle_invoice_item(item, paid_on, actor)
        elif item.state == "settled":
            # Only a call that really flips a collected invoice back reverses what was paid.
            # An invoice that is already unpaid keeps its partial payments: «unpaid» is what
            # the projection says about it.
            live = self.repo.live_settlements(item.id)
            self._refuse_evidenced(live)
            for settlement in live:
                self._reverse(settlement, "incasso annullato", actor)
            item.state = "expected"
        self.session.flush()
        self._follow(item)
        self._project(item)

    def repair_invoice(self, invoice: Invoice) -> bool:
        """Brings the item of an invoice the previous release changed after the item existed
        back in step with the invoice: the old code wrote `payment_status` and `payment_date`
        without a settlement and annulled without dropping, and the old API keeps serving
        while the new release migrates. Answers whether anything changed; idempotent, since
        after it the item says what the invoice says.

        An invoice marked paid settles what remains on its date, one marked unpaid reverses
        the settlements of an item that reads settled, a collected invoice whose date moved
        moves its settlement, and an annulled invoice drops its item."""
        item = self.repo.by_source(SALES_INVOICE, invoice.id)
        if item is None:
            return False
        if invoice.status == "cancelled":
            if item.state == "dropped":
                return False
            self.drop_for_invoice(invoice.id)
            return True
        if item.state == "dropped":
            return False
        paid = invoice.payment_status == "paid"
        paid_on = (invoice.payment_date or invoice.issue_date) if paid else None
        totals = self.repo.totals(item.id)
        if (item.state == "settled") == paid and (not paid or totals.last_settled_on == paid_on):
            return False
        self.record_invoice_payment(invoice, paid=paid, paid_on=paid_on, actor=Actor.system())
        return True

    def drop_for_invoice(self, invoice_id: UUID) -> None:
        """An annulled invoice owes nothing. The payments recorded against it stay, as cash
        facts; the item reads `dropped`. Idempotent, and a no-op for an invoice with no item."""
        item = self.repo.by_source(SALES_INVOICE, invoice_id)
        if item is None or item.state == "dropped":
            return
        item = self._locked_item(item.id)
        item.state = "dropped"
        item.dropped_reason = "fattura annullata"
        self.session.flush()
        self._follow(item)

    def _settle_invoice_item(self, item: DueItem, paid_on: dt.date, actor: Actor) -> None:
        remaining = item.amount - self.repo.totals(item.id).settled
        if remaining != ZERO:
            self._add_settlement(item, remaining, paid_on, actor)
        else:
            # Already collected in full: only the date can differ, and the settlements of
            # the last day move to the new one.
            last = self.repo.totals(item.id).last_settled_on
            if last == paid_on:
                return
            moved = [row for row in self.repo.live_settlements(item.id) if row.settled_on == last]
            self._refuse_evidenced(moved)
            for settlement in moved:
                self._reverse(settlement, "data dell'incasso corretta", actor)
            self._add_settlement(item, sum((row.amount for row in moved), ZERO), paid_on, actor)
        item.state = "settled"

    def _add_settlement(
        self, item: DueItem, amount: Decimal, settled_on: dt.date, actor: Actor
    ) -> DueItemSettlement:
        settlement = self.repo.add_settlement(
            DueItemSettlement(
                due_item_id=item.id,
                legal_entity_id=item.legal_entity_id,
                amount=amount,
                settled_on=settled_on,
                recorded_by_id=actor.id,
                recorded_by_type=actor.type,
            )
        )
        if actor.type != "system":
            self._record("due_item_settled", item, settlement, actor)
        return settlement

    def _reverse(self, settlement: DueItemSettlement, reason: str, actor: Actor) -> None:
        settlement.reversed_at = _now()
        settlement.reversed_by_id = actor.id
        settlement.reversed_by_type = actor.type
        settlement.reversal_reason = reason
        item = self.repo.get(settlement.due_item_id)
        if item is not None and actor.type != "system":
            self._record("due_item_settlement_reversed", item, settlement, actor)

    def _project(self, item: DueItem) -> None:
        """The invoice's `payment_status` and `payment_date`, projected from its item: `paid`
        when nothing remains, dated by the latest live settlement. This is the only place that
        writes the two columns of an issued invoice (§ 1.1). A dropped item (an annulled
        invoice) leaves them as they were."""
        if item.source_kind == EXPENSE_NOTE_SOURCE_KIND:
            # An expense note's item (`expense_notes/projection.py`): the note reads reimbursed
            # when its item is settled in full.
            project_reimbursement(self.session, item, self.repo.totals(item.id).last_settled_on)
            return
        if item.source_kind != SALES_INVOICE or item.source_id is None or item.state == "dropped":
            return
        invoice = self.session.get(Invoice, item.source_id)
        if invoice is None:
            return
        paid = item.state == "settled"
        invoice.payment_status = "paid" if paid else "unpaid"
        invoice.payment_date = self.repo.totals(item.id).last_settled_on if paid else None
        self.session.flush()

    def _record_payment(
        self,
        item: DueItem,
        data: SettlementCreate,
        actor: Actor,
        *,
        bank_movement_id: UUID | None = None,
    ) -> DueItemSettlement:
        """The checks and the write of one payment against a locked item, shared by `settle` and
        by the matching of a bank movement. Flushes and projects, never commits."""
        if item.state == "forecast" and item.source_kind in TAX_SOURCE_KINDS_PAYABLE:
            # A tax has no document that replaces its forecast: the F24 is the payment, so a
            # tax forecast that is paid becomes the item that was paid (REB-1074).
            item.state = "expected"
            item.forecast_key = None
        if item.state == "forecast":
            raise Conflict(
                ENTITY,
                "una previsione non e' ancora dovuta: si salda il documento che la sostituira'",
                id=str(item.id),
            )
        if item.state == "dropped":
            raise Conflict(ENTITY, "una scadenza scartata non si salda", id=str(item.id))
        if item.state == "settled":
            raise Conflict(ENTITY, "la scadenza e' gia' saldata", id=str(item.id))
        if (data.amount > 0) != (item.amount > 0):
            raise ValidationFailed(
                SETTLEMENT_ENTITY,
                "amount",
                "il pagamento ha il segno della scadenza",
                expected="positivo per un importo dovuto, negativo per un credito",
            )
        if data.settled_on > today_local():
            raise ValidationFailed(
                SETTLEMENT_ENTITY,
                "settled_on",
                "un pagamento non e' ancora avvenuto in una data futura",
                expected="la data in cui il pagamento e' avvenuto",
            )
        remaining = item.amount - self.repo.totals(item.id).settled
        if abs(data.amount) > abs(remaining):
            raise Conflict(
                ENTITY,
                "il pagamento supera quanto resta da saldare",
                id=str(item.id),
                remaining=str(remaining),
                amount=str(data.amount),
            )
        settlement = self.repo.add_settlement(
            DueItemSettlement(
                due_item_id=item.id,
                legal_entity_id=item.legal_entity_id,
                amount=data.amount,
                account_amount=data.account_amount,
                account_currency=data.account_currency,
                settled_on=data.settled_on,
                value_date=data.value_date,
                bank_movement_id=bank_movement_id,
                note=data.note,
                recorded_by_id=actor.id,
                recorded_by_type=actor.type,
            )
        )
        totals = self.repo.totals(item.id)
        if totals.settled == item.amount:
            item.state = "settled"
        self.session.flush()
        self._follow(item)
        self._project(item)
        self._record("due_item_settled", item, settlement, actor)
        return settlement

    # ---- the bank matching's half: an actor, no commit ------------------------------------
    #
    # `MovementMatchService` has asked for a person before it calls these and commits after
    # them; what each asks again is the item's own permission, which depends on the record
    # (an outgoing or fiscal item is an administrator's).

    def settle_from_movement(
        self, item_id: UUID, amount: Decimal, movement: BankMovement, actor: Actor
    ) -> DueItemSettlement:
        """Settles `amount` (in the item's currency and sign) of an item, dated on the movement's
        booking date, with the movement as its evidence. Flushes, does not commit."""
        item = self._locked(item_id)
        _require_settle_permission(actor, item, undo=False)
        self._require_may_pay_note(item, actor)
        data = SettlementCreate(
            amount=amount, settled_on=movement.booking_date, value_date=movement.value_date
        )
        return self._record_payment(item, data, actor, bank_movement_id=movement.id)

    def settle_remaining(
        self, item_id: UUID, settled_on: dt.date, note: str, actor: Actor
    ) -> DueItemSettlement:
        """Settles what remains of an item, dated `settled_on` or today when that day has not
        come (a payment is never dated in the future). The writer's half for a source that pays
        its own item (an expense note reimbursed by a transfer already made, or by the payslip
        that paid it): the caller has asked for an administrator first. Flushes, does not
        commit."""
        item = self._locked(item_id)
        remaining = item.amount - self.repo.totals(item.id).settled
        data = SettlementCreate(
            amount=remaining, settled_on=min(settled_on, today_local()), note=note
        )
        return self._record_payment(item, data, actor)

    def settle_by_filing(
        self, item_id: UUID, amount: Decimal, filed_on: dt.date, note: str, actor: Actor
    ) -> DueItemSettlement:
        """Settles `amount` (in the item's sign) of a line of an F24 that was filed with no
        bank movement, dated the day it was filed: the same payment a movement's match writes,
        without the movement. The caller (`F24GroupService.file_f24`) has asked for a person
        first. Flushes, does not commit."""
        item = self._locked(item_id)
        _require_settle_permission(actor, item, undo=False)
        data = SettlementCreate(amount=amount, settled_on=filed_on, note=note)
        return self._record_payment(item, data, actor)

    def adopt_for_movement(
        self, item_id: UUID, movement: BankMovement, actor: Actor
    ) -> DueItemSettlement:
        """Attaches the movement to the payment already marked by hand for the same amount, so
        the movement does not make a second one (spec § 3). Refuses when the item has no
        live payment of that amount without evidence. Flushes, does not commit."""
        item = self._locked(item_id)
        _require_settle_permission(actor, item, undo=False)
        if item.state not in ("expected", "settled"):
            raise Conflict(ENTITY, "la scadenza non e' piu' aperta ne' saldata", id=str(item.id))
        magnitude = abs(movement.amount)
        # Of several payments of that amount the one dated nearest the booking is the one the
        # movement proves.
        nearest = sorted(
            (
                settlement
                for settlement in self.repo.live_settlements(item.id)
                if settlement.bank_movement_id is None and abs(settlement.amount) == magnitude
            ),
            key=lambda row: abs((row.settled_on - movement.booking_date).days),
        )
        if nearest:
            settlement = nearest[0]
            settlement.bank_movement_id = movement.id
            self.session.flush()
            self._record("due_item_settlement_evidenced", item, settlement, actor)
            return settlement
        raise Conflict(
            ENTITY,
            "nessun pagamento segnato a mano per questo importo da collegare al movimento",
            id=str(item.id),
        )

    def release_for_movement(
        self, settlement_id: UUID, *, adopted: bool, reason: str | None, actor: Actor
    ) -> None:
        """Takes back what a match did to one payment: a payment the match wrote is reversed
        (the row stays, stamped, with its evidence), one it adopted only loses the evidence and
        stays as the person marked it. Also how reopening a filed F24 reverses the payments the
        filing wrote (`adopted` false). Flushes, does not commit."""
        settlement = self.repo.get_settlement(settlement_id)
        if settlement is None:
            raise NotFound(SETTLEMENT_ENTITY, settlement_id)
        item = self._locked(settlement.due_item_id)
        _require_settle_permission(actor, item, undo=True)
        self.session.refresh(settlement)
        if adopted:
            settlement.bank_movement_id = None
            self.session.flush()
            self._record("due_item_settlement_evidence_removed", item, settlement, actor)
            return
        if settlement.reversed_at is not None:
            return
        self._reverse(settlement, reason or "abbinamento annullato", actor)
        if item.state == "settled":
            item.state = "expected"
        self.session.flush()
        self._follow(item)
        self._project(item)

    # ---- the surface's half: an actor, a commit -------------------------------------------

    def settle(self, item_id: UUID, data: SettlementCreate, actor: Actor) -> DueItemDetail:
        """Records a payment, partial or full. Partly paid, an item stays `expected` with what
        remains; paid in full it reads `settled`. A payment beyond what remains is refused."""
        # The gate that needs no item first: a reader, or a credential no action reaches,
        # is refused before the lookup says whether the item exists. What the item's
        # direction and source add comes after it.
        actor.require_write("settle_due_item")
        item = self._locked(item_id)
        _require_settle_permission(actor, item, undo=False)
        self._require_may_pay_note(item, actor)
        self._require_not_grouped(item)
        self._record_payment(item, data, actor)
        self.session.commit()
        return self._detail(item)

    def confirm_tax_carry(self, item_id: UUID, actor: Actor) -> DueItemDetail:
        """An administrator confirms the difference an advance already paid left on this tax item
        (REB-1074 writes «da confermare» in its description; the legal treatment is «to verify»,
        taxes design § 2). A decision for a person: no agent credential takes it, `full_access` or
        not. Only the confirmation is recorded: the amount stays. Confirming twice is a no-op; an
        item rewritten with another difference is a new one to confirm."""
        actor.require_person("confirm_tax_carry")
        actor.require_admin("confirm_tax_carry")
        item = self._locked(item_id)
        if (
            item.source_kind not in TAX_SOURCE_KINDS_PAYABLE
            or item.state == "dropped"
            or not (item.description or "").endswith(CARRY_TO_CONFIRM_SUFFIX)
        ):
            raise Conflict(
                ENTITY, "questa voce non ha una differenza da confermare", id=str(item.id)
            )
        if item.carry_confirmed_at is None:
            item.carry_confirmed_at = _now()
            self.activities.record(
                "legal_entity",
                item.legal_entity_id,
                "tax_carry_confirmed",
                actor,
                {"due_item_id": str(item.id)},
            )
            self.session.commit()
        return self._detail(item)

    def reverse_settlement(
        self, settlement_id: UUID, reason: str | None, actor: Actor
    ) -> DueItemDetail:
        """Undoes a payment recorded by mistake, or one the bank never confirmed. The row stays,
        stamped; a settled item goes back to `expected` with what the reversal frees."""
        actor.require_write("reverse_settlement")
        settlement = self.repo.get_settlement(settlement_id)
        if settlement is None:
            raise NotFound(SETTLEMENT_ENTITY, settlement_id)
        item = self._locked(settlement.due_item_id)
        _require_settle_permission(actor, item, undo=True)
        self.session.refresh(settlement)
        if settlement.reversed_at is not None:
            raise Conflict(
                SETTLEMENT_ENTITY, "il pagamento e' gia' stato annullato", id=str(settlement.id)
            )
        if settlement.bank_movement_id is not None:
            raise Conflict(
                SETTLEMENT_ENTITY,
                "il pagamento risulta da un movimento in banca: annulla l'abbinamento",
                id=str(settlement.id),
            )
        settlement.reversed_at = _now()
        settlement.reversed_by_id = actor.id
        settlement.reversed_by_type = actor.type
        settlement.reversal_reason = reason[:255] if reason else None
        if item.state == "settled":
            item.state = "expected"
        self.session.flush()
        self._follow(item)
        self._project(item)
        self._record("due_item_settlement_reversed", item, settlement, actor)
        self.session.commit()
        return self._detail(item)

    def list_payroll_totals(self, query: PayrollTotalsQuery, actor: Actor) -> PayrollTotals:
        """The one «Stipendi» line per date a reader who may not see payroll gets in its
        place (staff design § 4): the open payroll items of the date added up, with nothing
        that says whose pay it is, what it is for or how many people it covers. Anyone who
        may list due items reads it, an administrator included."""
        actor.require_agent_allowed("list_due_items")
        rows = self.repo.payroll_totals(
            legal_entity_id=query.legal_entity_id, due_from=query.due_from, due_to=query.due_to
        )
        return PayrollTotals(
            items=[
                PayrollTotalLine(due_date=day, amount=total, forecast=forecast)
                for day, total, forecast in rows
                if total != 0
            ]
        )

    def get(self, item_id: UUID, actor: Actor) -> DueItemDetail:
        actor.require_agent_allowed("get_due_item")
        item = self.repo.get(item_id)
        if item is None:
            raise NotFound(ENTITY, item_id)
        if _is_fiscal(item) and not self._is_own_note_item(item, actor):
            actor.require_admin("read_fiscal_due_items")
        return self._detail(item)

    # `list` stays the last method in this class -- the unconditional project rule (a
    # `def list(...)` would rebind `list` in the class namespace).
    def list(self, query: DueItemListQuery, actor: Actor) -> DueItemPage:
        """Open items in due-date order, undated ones last. The fiscal kinds are left out for
        a caller who may not read them; naming one in `source_kind` is refused outright."""
        actor.require_agent_allowed("list_due_items")
        if query.source_kind in RESTRICTED_SOURCE_KINDS:
            actor.require_admin("read_fiscal_due_items")
        rows = self.repo.list_rows(
            query,
            restricted=_may_read_fiscal(actor),
            own_staff_member_id=own_staff_member_id(self.session, actor),
        )
        page = rows[: query.limit]
        return DueItemPage(
            items=[self._read(item, name, settled) for item, name, settled in page],
            next_cursor=page[-1][0].id if len(rows) > query.limit and page else None,
        )

    # ---- internals ------------------------------------------------------------------------

    def _record(
        self, kind: str, item: DueItem, settlement: DueItemSettlement, actor: Actor
    ) -> None:
        """The timeline entry of the item's legal entity: who, when and through which door
        (REB-781). Due items have no timeline of their own yet, so it hangs on the legal
        entity, whose visibility the row policy already knows. The amount is left out of an
        outgoing or fiscal item's entry: whoever reads the legal entity's timeline is not
        necessarily who may read that payment."""
        payload: dict[str, Any] = {
            "due_item_id": str(item.id),
            "settlement_id": str(settlement.id),
        }
        if item.direction == "in" and not _is_fiscal(item):
            payload["amount"] = str(settlement.amount)
        if settlement.bank_movement_id is not None:
            payload["bank_movement_id"] = str(settlement.bank_movement_id)
        self.activities.record("legal_entity", item.legal_entity_id, kind, actor, payload)

    def _is_own_note_item(self, item: DueItem, actor: Actor) -> bool:
        """An expense note's item is its staff member's to read, as well as an administrator's."""
        return (
            item.source_kind == EXPENSE_NOTE_SOURCE_KIND
            and item.staff_member_id is not None
            and item.staff_member_id == own_staff_member_id(self.session, actor)
        )

    def _require_may_pay_note(self, item: DueItem, actor: Actor) -> None:
        """Paying an expense note's item is reimbursing the note: not the author's to do
        (`expense_notes/access.py`)."""
        if item.source_kind == EXPENSE_NOTE_SOURCE_KIND:
            require_may_pay_item(self.session, item.id, actor)

    @staticmethod
    def _refuse_evidenced(settlements: Sequence[DueItemSettlement]) -> None:
        """A payment a bank movement proves is taken back through its match, which keeps the
        match and the movement's state in step; every other door refuses it."""
        for settlement in settlements:
            if settlement.bank_movement_id is not None:
                raise Conflict(
                    SETTLEMENT_ENTITY,
                    "il pagamento risulta da un movimento in banca: annulla l'abbinamento",
                    id=str(settlement.id),
                )

    def _locked(self, item_id: UUID) -> DueItem:
        """The item under its lock, the invoice's own row first when it is a sales item: the
        invoice's door locks the invoice and then the item, and this door must queue the
        same way."""
        peek = self.repo.get(item_id)
        if peek is not None and peek.source_kind == SALES_INVOICE and peek.source_id is not None:
            self.repo.lock_invoice(peek.source_id)
        return self._locked_item(item_id)

    def _locked_item(self, item_id: UUID) -> DueItem:
        item = self.repo.get_locked(item_id)
        if item is None:
            raise NotFound(ENTITY, item_id)
        return item

    def _require_not_grouped(self, item: DueItem) -> None:
        """A line of a live F24 group is paid with its group by the one movement that pays the
        net, and is not recomputed: ungroup first."""
        if self.groups.group_of(item.id) is not None:
            raise Conflict(
                ENTITY, "la scadenza e' in un F24: prima si scioglie il gruppo", id=str(item.id)
            )

    def _require_organization_of(self, organization_id: UUID, legal_entity_id: UUID) -> None:
        row = self.repo.organization_legal_entity(organization_id)
        if row is None:
            raise NotFound("organization", organization_id)
        owner = row[0]
        # An organization shared across the legal entities (no legal entity of its own) is
        # every legal entity's (REB-970).
        if owner is not None and owner != legal_entity_id:
            raise ValidationFailed(
                ENTITY,
                "organization_id",
                "l'organizzazione e' di un'altra azienda",
                expected="un'organizzazione della stessa azienda della scadenza",
            )

    def _require_staff_member_of(self, staff_member_id: UUID, legal_entity_id: UUID) -> None:
        owner = self.repo.staff_member_legal_entity(staff_member_id)
        if owner is None:
            raise NotFound("staff_member", staff_member_id)
        if owner != legal_entity_id:
            raise ValidationFailed(
                ENTITY,
                "staff_member_id",
                "la persona e' di un'altra azienda",
                expected="una persona della stessa azienda della scadenza",
            )

    def _anchor(self, dependency: DueItem) -> Anchor:
        settled_on = (
            self.repo.totals(dependency.id).last_settled_on
            if dependency.state == "settled"
            else None
        )
        return Anchor(dependency.state, dependency.due_date, settled_on)

    def _redate(self, item: DueItem) -> bool:
        """Recomputes `due_date` and `due_date_estimated` from the rule and the dependency's
        present state. Returns whether either moved."""
        anchor = None
        if item.depends_on_id is not None:
            dependency = self.repo.get(item.depends_on_id)
            if dependency is not None:
                anchor = self._anchor(dependency)
        resolved = resolve(
            item.rule_kind,
            own_date=item.own_date,
            offset_days=item.rule_offset_days,
            day_of_month=item.rule_day_of_month,
            not_after=item.rule_not_after,
            anchor=anchor,
        )
        moved = (item.due_date, item.due_date_estimated) != (resolved.date, resolved.estimated)
        item.due_date = resolved.date
        item.due_date_estimated = resolved.estimated
        self.session.flush()
        return moved

    def _follow(self, item: DueItem) -> None:
        """Re-dates every open item that follows `item`, and theirs in turn while a date
        keeps moving. A rule names an item that already exists, so a chain cannot loop."""
        pending = [item]
        seen: set[UUID] = set()
        while pending:
            current = pending.pop()
            if current.id in seen:
                continue
            seen.add(current.id)
            for dependent in self.repo.dependents(current.id):
                if self._redate(dependent):
                    pending.append(dependent)

    def _read(
        self, item: DueItem, name: str | None = None, settled: Decimal | None = None
    ) -> DueItemRead:
        if settled is None:
            settled = self.repo.totals(item.id).settled
            name = self.repo.organization_name(item.organization_id)
        remaining = ZERO if item.state == "dropped" else item.amount - settled
        return DueItemRead.model_validate(
            {
                "id": item.id,
                "legal_entity_id": item.legal_entity_id,
                "direction": item.direction,
                "state": item.state,
                "counterparty_type": item.counterparty_type,
                "organization_id": item.organization_id,
                "organization_name": name,
                "staff_member_id": item.staff_member_id,
                "tax": item.tax,
                "amount": item.amount,
                "currency": item.currency,
                "settled": settled,
                "remaining": remaining,
                "description": item.description,
                "source_kind": item.source_kind,
                "source_id": item.source_id,
                "source_rule": item.source_rule,
                "own_date": item.own_date,
                "due_date": item.due_date,
                "due_date_estimated": item.due_date_estimated,
                "rule": {
                    "kind": item.rule_kind,
                    "depends_on_id": item.depends_on_id,
                    "offset_days": item.rule_offset_days,
                    "day_of_month": item.rule_day_of_month,
                    "not_after": item.rule_not_after,
                },
                "dropped_reason": item.dropped_reason,
                "carry_to_confirm": item.carry_confirmed_at is None
                and item.state != "dropped"
                and (item.description or "").endswith(CARRY_TO_CONFIRM_SUFFIX),
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
        )

    def _detail(self, item: DueItem) -> DueItemDetail:
        self.session.refresh(item)
        return DueItemDetail(
            **self._read(item).model_dump(),
            settlements=[
                SettlementRead.model_validate(settlement)
                for settlement in self.repo.settlements(item.id)
            ],
        )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


__all__ = ["ENTITY", "SETTLEMENT_ENTITY", "DueItemService"]
