"""The only writer of F24 groups (REB-1076, taxes design § 2 and § 5).

Due items that fall on the same date, credits included, are grouped into one F24 that a person
pays with one transfer. The group is an administrator's act (an administrator whose scope covers
the legal entity, an agent only with `full_access`: `agent_actions.py`), and so is reading one:
its lines are the fiscal position.

**What may be grouped.** Items owed to the tax office (the tax items, IVA, the withholdings paid for
others, payroll's INPS and INAIL, and the credits), in euro, out, all falling on one date, open
with nothing paid on them and in no other live group. The net, the debits less the credits, must be
positive: a group that nets to zero or to a credit has no movement to match.

**Why a tax forecast is made `expected` when it is grouped.** A tax has no document that replaces
its forecast: the F24 is the payment (`DueItemService._record_payment` already makes a paid tax
forecast the item that was paid). Grouping is the act that fixes the amount on the form, so the
group moves a `tax_balance` or `tax_advance` forecast to `expected`, the state the bank matching
reads as payable, and the group's one movement can settle every line without a special case for the
state. The alternative, letting the group match regardless of state, would leave an item that reads
«forecast» in the schedule after it is on a form. Ungrouping does not undo it: the item stays at the
amount it had, and the next recompute of the tax items (`TaxDueItemService`, called on ungrouping)
drops it and writes the forecast again when the figure is still an estimate. While grouped, no
recompute touches a line (`DueItemService` refuses to drop, restate or reschedule it).

**Codes.** Each line keeps the code the pack gave it when it was grouped (`f24_codes.py`), so a
paid F24 still reads what was paid. A line the pack has no code for has none and reads «codice da
verificare»: nothing is guessed.

**Paying.** One bank movement equal to the net settles the group and every line
(`MovementMatchService`, which offers the group as a candidate and settles it as a whole); undoing
the match reopens it. Ungrouping is allowed only before: a settled group is reopened first.

**Receipts.** An F24's receipt is an inbox item of kind `tax_document`; the group points at it and
the file stays in the inbox's document storage, behind the inbox's own visibility (`PAY_KINDS`).
"""

import datetime as dt
from collections.abc import Collection, Sequence
from collections.abc import Set as AbstractSet
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schedule_queries import kinds_of_modules_off
from pigrocrm.core.due_items.service import TAX_SOURCE_KINDS_PAYABLE, DueItemService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.f24_codes import code_for
from pigrocrm.core.fiscal.f24_models import F24Group, F24GroupItem, F24GroupReceipt, TaxCredit
from pigrocrm.core.fiscal.f24_repository import F24GroupRepository
from pigrocrm.core.fiscal.f24_schemas import (
    CODE_TO_VERIFY,
    F24GroupCreate,
    F24GroupListQuery,
    F24GroupPage,
    F24GroupRead,
    F24LineRead,
    F24ReceiptRead,
    TaxCreditOriginRead,
)
from pigrocrm.core.fiscal.pack import FiscalPack, resolve_pack
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.inbox.access import visible_item
from pigrocrm.core.inbox.models import InboxItem

ENTITY = "f24_group"
ZERO = Decimal("0.00")
EURO = "EUR"
RECEIPT_KIND = "tax_document"


class F24GroupService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = F24GroupRepository(session)
        self.dues = DueItemService(session)
        self.activities = ActivityService(session)

    # ---- group and ungroup ------------------------------------------------------------------

    def group_f24(
        self,
        data: F24GroupCreate,
        actor: Actor,
        *,
        modules: AbstractSet[str] | None = None,
    ) -> F24GroupRead:
        """Groups the named due items into one F24, dated on their common date. Refuses an item
        that cannot be paid with an F24 and says which, and a group whose net is not a payment."""
        actor.require_admin("group_f24")
        items = self._lock(data.due_item_ids)
        entity_ids = {item.legal_entity_id for item in items}
        if len(entity_ids) != 1:
            raise ValidationFailed(
                ENTITY,
                "due_item_ids",
                "un F24 e' di una sola azienda",
                expected="scadenze della stessa azienda",
            )
        off = kinds_of_modules_off(modules)
        for item in items:
            self._require_groupable(item, off)
        dates = {item.due_date for item in items}
        if len(dates) != 1 or None in dates:
            raise ValidationFailed(
                ENTITY,
                "due_item_ids",
                "le scadenze cadono in giorni diversi: un F24 si paga in un giorno",
                expected="scadenze dello stesso giorno",
            )
        (payment_date,) = dates
        assert payment_date is not None  # noqa: S101 - `None in dates` was refused above
        net = sum((item.amount for item in items), ZERO)
        if net <= 0:
            raise ValidationFailed(
                ENTITY,
                "due_item_ids",
                "i crediti coprono tutto il dovuto: l'F24 non ha un importo da pagare",
                expected="scadenze il cui totale netto e' positivo",
            )
        legal_entity_id = next(iter(entity_ids))
        pack = self._pack(legal_entity_id)
        group = F24Group(
            legal_entity_id=legal_entity_id,
            payment_date=payment_date,
            state="open",
            note=data.note,
            created_by_id=actor.id,
            created_by_type=actor.type,
        )
        self.session.add(group)
        self.session.flush()
        for item in items:
            if item.state == "forecast":
                # A tax forecast is the item that is paid (module docstring).
                item.state = "expected"
                item.forecast_key = None
            code = code_for(item, pack, payment_date)
            self.session.add(
                F24GroupItem(
                    group_id=group.id,
                    due_item_id=item.id,
                    legal_entity_id=legal_entity_id,
                    amount=item.amount,
                    line=code.line,
                    code=code.code,
                    code_kind=code.kind,
                    code_label=code.label,
                    reference_year=code.reference_year,
                )
            )
        self.session.flush()
        # Who grouped it and how many lines; never an amount, which is the fiscal position.
        self.activities.record(ENTITY, group.id, "f24_grouped", actor, {"lines": len(items)})
        self.session.commit()
        return self._read([group])[0]

    def ungroup_f24(self, group_id: UUID, actor: Actor) -> F24GroupRead:
        """Releases the lines of a group that is not paid: the items are free again and the
        group stays as `dissolved`. A group a movement paid is reopened first (undo the match)."""
        actor.require_admin("ungroup_f24")
        group = self._group(group_id, lock=True)
        if group.state == "dissolved":
            return self._read([group])[0]
        if group.state == "settled":
            raise Conflict(
                ENTITY,
                "l'F24 e' stato pagato: prima si annulla l'abbinamento al movimento",
                id=str(group.id),
            )
        now = dt.datetime.now(dt.UTC)
        for line in self.repo.live_lines(group.id):
            line.released_at = now
        group.state = "dissolved"
        self.session.flush()
        # What a grouped forecast was frozen at is recomputed now, not at the next daily pass.
        TaxDueItemService(self.session).refresh_tax_due_items(group.legal_entity_id)
        self.activities.record(ENTITY, group.id, "f24_ungrouped", actor)
        self.session.commit()
        return self._read([group])[0]

    # ---- receipts ---------------------------------------------------------------------------

    def attach_f24_receipt(self, group_id: UUID, inbox_item_id: UUID, actor: Actor) -> F24GroupRead:
        """Points the group at an inbox item of kind `tax_document`, the receipt of the F24. The
        file stays in the inbox; an item of another company or of another kind is refused."""
        actor.require_admin("attach_f24_receipt")
        group = self._group(group_id, lock=True)
        if group.state == "dissolved":
            raise Conflict(ENTITY, "un F24 sciolto non ha ricevute", id=str(group.id))
        item = visible_item(self.session, inbox_item_id, actor)
        if item.kind != RECEIPT_KIND:
            raise ValidationFailed(
                ENTITY,
                "inbox_item_id",
                "il documento non e' di tipo documento fiscale: imposta prima il tipo",
                expected="un elemento in arrivo di tipo documento fiscale",
            )
        if item.legal_entity_id is not None and item.legal_entity_id != group.legal_entity_id:
            raise ValidationFailed(ENTITY, "inbox_item_id", "il documento e' di un'altra azienda")
        already = self.session.scalar(
            select(F24GroupReceipt.id).where(
                F24GroupReceipt.group_id == group.id, F24GroupReceipt.inbox_item_id == item.id
            )
        )
        if already is not None:
            raise Conflict(ENTITY, "la ricevuta e' gia' collegata all'F24", id=str(group.id))
        self.session.add(
            F24GroupReceipt(
                group_id=group.id,
                legal_entity_id=group.legal_entity_id,
                inbox_item_id=item.id,
                attached_by_id=actor.id,
                attached_by_type=actor.type,
            )
        )
        self.session.flush()
        self.activities.record(ENTITY, group.id, "f24_receipt_attached", actor)
        self.session.commit()
        return self._read([group])[0]

    def detach_f24_receipt(self, group_id: UUID, inbox_item_id: UUID, actor: Actor) -> F24GroupRead:
        """Takes a receipt off the group; the inbox item is untouched."""
        actor.require_admin("detach_f24_receipt")
        group = self._group(group_id, lock=True)
        receipt = self.session.scalars(
            select(F24GroupReceipt).where(
                F24GroupReceipt.group_id == group.id,
                F24GroupReceipt.inbox_item_id == inbox_item_id,
            )
        ).first()
        if receipt is None:
            raise NotFound("f24_group_receipt", inbox_item_id)
        self.session.delete(receipt)
        self.session.flush()
        self.activities.record(ENTITY, group.id, "f24_receipt_detached", actor)
        self.session.commit()
        return self._read([group])[0]

    # ---- reads ------------------------------------------------------------------------------

    def get_f24_group(self, group_id: UUID, actor: Actor) -> F24GroupRead:
        actor.require_admin("list_f24_groups")
        return self._read([self._group(group_id)])[0]

    # `list_f24_groups` stays the last method of the class: a `def list` would rebind `list`
    # in the class namespace, and this one is named apart for the MCP ban test's by-name scan.
    def list_f24_groups(self, query: F24GroupListQuery, actor: Actor) -> F24GroupPage:
        """Groups by payment date, the open ones unless `state` says another, for one legal
        entity or all the actor sees."""
        actor.require_admin("list_f24_groups")
        stmt = select(F24Group).where(F24Group.state == (query.state or "open"))
        if query.legal_entity_id is not None:
            stmt = stmt.where(F24Group.legal_entity_id == query.legal_entity_id)
        if query.due_from is not None:
            stmt = stmt.where(F24Group.payment_date >= query.due_from)
        if query.due_to is not None:
            stmt = stmt.where(F24Group.payment_date <= query.due_to)
        if query.cursor is not None:
            anchor = self.repo.get(query.cursor)
            if anchor is not None:
                stmt = stmt.where(
                    or_(
                        F24Group.payment_date > anchor.payment_date,
                        (F24Group.payment_date == anchor.payment_date) & (F24Group.id > anchor.id),
                    )
                )
        rows = list(
            self.session.scalars(
                stmt.order_by(F24Group.payment_date, F24Group.id).limit(query.limit + 1)
            )
        )
        page = rows[: query.limit]
        return F24GroupPage(
            items=self._read(page),
            next_cursor=page[-1].id if len(rows) > query.limit and page else None,
        )

    # ---- internals --------------------------------------------------------------------------

    def _group(self, group_id: UUID, *, lock: bool = False) -> F24Group:
        group = self.repo.get(group_id, lock=lock)
        if group is None:
            raise NotFound(ENTITY, group_id)
        return group

    def _lock(self, item_ids: Sequence[UUID]) -> list[DueItem]:
        """The named items under their lock, in id order so two groupings queue without a
        deadlock; one that is not there, or is another scope's, is not found."""
        rows = {
            item.id: item
            for item in self.session.scalars(
                select(DueItem)
                .where(DueItem.id.in_(list(item_ids)))
                .order_by(DueItem.id)
                .with_for_update()
            )
        }
        for item_id in item_ids:
            if item_id not in rows:
                raise NotFound("due_item", item_id)
        return [rows[item_id] for item_id in item_ids]

    def _require_groupable(self, item: DueItem, off: Collection[str]) -> None:
        item_id = str(item.id)
        if item.counterparty_type != "tax_office" or item.direction != "out":
            raise ValidationFailed(
                ENTITY,
                "due_item_ids",
                "una scadenza che non e' verso l'erario non va in un F24",
                expected="scadenze verso l'erario",
            )
        if item.currency != EURO:
            raise ValidationFailed(
                ENTITY, "due_item_ids", "un F24 si paga in euro", expected="scadenze in euro"
            )
        if item.source_kind in off:
            raise ValidationFailed(ENTITY, "due_item_ids", "la scadenza e' di un modulo spento")
        if item.state == "dropped":
            raise Conflict(ENTITY, "una scadenza scartata non va in un F24", id=item_id)
        if item.state == "settled" or self.dues.repo.totals(item.id).settled != ZERO:
            raise Conflict(
                ENTITY, "una scadenza gia' pagata, anche in parte, non va in un F24", id=item_id
            )
        if item.state == "forecast" and item.source_kind not in TAX_SOURCE_KINDS_PAYABLE:
            raise Conflict(ENTITY, "una previsione non e' ancora dovuta", id=item_id)
        if self.repo.group_of(item.id) is not None:
            raise Conflict(ENTITY, "la scadenza e' gia' in un F24", id=item_id)

    def _pack(self, legal_entity_id: UUID) -> FiscalPack | None:
        profile = FiscalProfileRepository(self.session).get(legal_entity_id)
        if profile is None:
            return None
        try:
            return resolve_pack(profile.pack_id, profile.pack_version)
        except KeyError:
            return None

    def _read(self, groups: Sequence[F24Group]) -> list[F24GroupRead]:
        ids = [group.id for group in groups]
        lines = self.repo.lines_of(ids)
        receipts = self.repo.receipts_of(ids)
        item_ids = {line.due_item_id for group_lines in lines.values() for line in group_lines}
        items: dict[UUID, DueItem] = {}
        if item_ids:
            items = {
                item.id: item
                for item in self.session.scalars(select(DueItem).where(DueItem.id.in_(item_ids)))
            }
        credits = self.repo.credits_by_id(
            item.source_id
            for item in items.values()
            if item.source_kind == "tax_credit" and item.source_id is not None
        )
        names = self._inbox_names(
            {
                receipt.inbox_item_id
                for group_receipts in receipts.values()
                for receipt in group_receipts
            }
        )
        return [
            self._group_read(group, lines[group.id], receipts[group.id], items, credits, names)
            for group in groups
        ]

    def _inbox_names(self, inbox_ids: set[UUID]) -> dict[UUID, str]:
        if not inbox_ids:
            return {}
        rows = self.session.execute(
            select(InboxItem.id, InboxItem.original_name).where(InboxItem.id.in_(inbox_ids))
        )
        return {item_id: name for item_id, name in rows}

    @staticmethod
    def _group_read(
        group: F24Group,
        lines: Sequence[F24GroupItem],
        receipts: Sequence[F24GroupReceipt],
        items: dict[UUID, DueItem],
        credits: dict[UUID, TaxCredit],
        names: dict[UUID, str],
    ) -> F24GroupRead:
        line_reads: list[F24LineRead] = []
        for line in lines:
            item = items[line.due_item_id]
            credit = credits.get(item.source_id) if item.source_id is not None else None
            line_reads.append(
                F24LineRead(
                    due_item_id=item.id,
                    source_kind=item.source_kind,
                    tax=item.tax,
                    description=item.description,
                    amount=line.amount,
                    is_credit=line.amount < 0,
                    credit_origin=None
                    if credit is None
                    else TaxCreditOriginRead(
                        origin=credit.origin,  # type: ignore[arg-type]
                        tax=credit.tax,
                        tax_year=credit.tax_year,
                        iva_settlement_id=credit.iva_settlement_id,
                    ),
                    line=line.line,
                    code=line.code,
                    code_kind=line.code_kind,  # type: ignore[arg-type]
                    code_label=line.code_label or CODE_TO_VERIFY,
                    code_to_verify=line.code is None,
                    reference_year=line.reference_year,
                    released=line.released_at is not None,
                )
            )
        line_reads.sort(key=lambda read: (read.is_credit, read.tax or "", str(read.due_item_id)))
        debit = sum((read.amount for read in line_reads if not read.is_credit), ZERO)
        credit_total = sum((-read.amount for read in line_reads if read.is_credit), ZERO)
        return F24GroupRead(
            id=group.id,
            legal_entity_id=group.legal_entity_id,
            payment_date=group.payment_date,
            state=group.state,  # type: ignore[arg-type]
            note=group.note,
            total=debit - credit_total,
            debit_total=debit,
            credit_total=credit_total,
            lines=line_reads,
            receipts=[
                F24ReceiptRead(
                    id=receipt.id,
                    inbox_item_id=receipt.inbox_item_id,
                    original_name=names.get(receipt.inbox_item_id),
                    attached_at=receipt.created_at,
                )
                for receipt in receipts
            ],
            bank_movement_id=group.bank_movement_id,
            settled_on=group.settled_on,
            created_by_type=group.created_by_type,
            created_at=group.created_at,
            updated_at=group.updated_at,
        )


__all__ = ["F24GroupService"]
