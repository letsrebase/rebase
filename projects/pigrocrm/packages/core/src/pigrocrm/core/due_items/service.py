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
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import (
    OPEN_STATES,
    RESTRICTED_SOURCE_KINDS,
    SOURCE_KINDS,
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
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity

ENTITY = "due_item"
SETTLEMENT_ENTITY = "due_item_settlement"
ZERO = Decimal("0.00")
SALES_INVOICE = "sales_invoice"


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
        self.activities = ActivityService(session)

    # ---- the writer's half: no actor, no commit -------------------------------------------

    def create(self, data: DueItemCreate) -> DueItemRead:
        """Records an amount that falls due. The caller commits, with the document it is for."""
        if data.source_kind not in SOURCE_KINDS:
            raise ValidationFailed(
                ENTITY,
                "source_kind",
                "tipo di origine sconosciuto",
                expected=", ".join(SOURCE_KINDS),
            )
        if self.session.get(LegalEntity, data.legal_entity_id) is None:
            raise NotFound("legal_entity", data.legal_entity_id)
        if data.organization_id is not None:
            self._require_organization_of(data.organization_id, data.legal_entity_id)

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
                tax=data.tax,
                amount=data.amount,
                currency=data.currency,
                description=data.description,
                source_kind=data.source_kind,
                source_id=data.source_id,
                source_rule=data.source_rule,
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
        item.state = "dropped"
        item.dropped_reason = reason[:255] if reason else None
        self.session.flush()
        self._follow(item)
        return self._read(item)

    def reschedule(self, item_id: UUID, due_date: dt.date | None) -> DueItemRead:
        """Moves an item's own date. A sales item's date is the invoice's and never moves
        (`MUTABLE_AFTER_ISSUE`): only an outgoing item and a forecast can be rescheduled."""
        item = self._locked(item_id)
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
        amount is the invoice's receivable, the date is its frozen `due_date`, and an invoice
        that arrives already collected (an import, a row of the previous release) is settled
        in full on `paid_on`. Answers `None` for an invoice with nothing to collect. Flushes
        and never commits: the caller's transaction is the invoice's."""
        existing = self.repo.by_source(SALES_INVOICE, invoice.id)
        if existing is not None:
            return existing
        if invoice.total == 0:
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
                amount=invoice.total,
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
            for settlement in self.repo.live_settlements(item.id):
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
        if item.source_kind != SALES_INVOICE or item.source_id is None or item.state == "dropped":
            return
        invoice = self.session.get(Invoice, item.source_id)
        if invoice is None:
            return
        paid = item.state == "settled"
        invoice.payment_status = "paid" if paid else "unpaid"
        invoice.payment_date = self.repo.totals(item.id).last_settled_on if paid else None
        self.session.flush()

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

    def get(self, item_id: UUID, actor: Actor) -> DueItemDetail:
        actor.require_agent_allowed("get_due_item")
        item = self.repo.get(item_id)
        if item is None:
            raise NotFound(ENTITY, item_id)
        if _is_fiscal(item):
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
        rows = self.repo.list_rows(query, restricted=_may_read_fiscal(actor))
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
        self.activities.record("legal_entity", item.legal_entity_id, kind, actor, payload)

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

    def _require_organization_of(self, organization_id: UUID, legal_entity_id: UUID) -> None:
        owner = self.repo.organization_legal_entity(organization_id)
        if owner is None:
            raise NotFound("organization", organization_id)
        if owner != legal_entity_id:
            raise ValidationFailed(
                ENTITY,
                "organization_id",
                "l'organizzazione e' di un'altra azienda",
                expected="un'organizzazione della stessa azienda della scadenza",
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
