"""The only writer of F24 groups (REB-1076, taxes design § 2 and § 5).

Due items that fall on the same date, credits included, are grouped into one F24 that a person
pays with one transfer. The group is an administrator's act (an administrator whose scope covers
the legal entity, an agent only with `full_access`: `agent_actions.py`), and so is reading one:
its lines are the fiscal position.

**What may be grouped.** Items owed to the tax office (the tax items, IVA, the withholdings paid for
others, payroll's INPS and INAIL, and the credits), in euro, out, all falling on one date, open
with nothing paid on them (a credit an earlier F24 used in part goes in with what is left of it) and
in no other live group. The net, the debits less the credits, may be positive, zero or a credit
(REB-1093).

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

**Paying.** One bank movement equal to the net settles a group with an amount to pay and every
line (`MovementMatchService`, which offers the group as a candidate and settles it as a whole);
undoing the match reopens it. Ungrouping is allowed only before: a settled group is reopened first.

**Filing a group with nothing to pay (REB-1093).** An F24 that nets to zero, or to a credit, moves
no money, so no bank movement can settle it. It must still be presented, and only through the
telematic services of the Agenzia delle Entrate: art. 11 comma 2 of D.L. 66/2014, as the Agenzia's
circolare 27/E of 19 September 2014 reads it, says the payments "sono eseguiti: a) esclusivamente
mediante i servizi telematici messi a disposizione dall'Agenzia delle entrate, nel caso in cui, per
effetto delle compensazioni effettuate, il saldo finale sia di importo pari a zero", and the
Agenzia's page «F24: come effettuare i versamenti» adds that since 1 July 2024 an F24 with credits
used in compensation goes through I24 "a prescindere se vi sia un saldo da versare oppure un saldo
pari a zero" (A5 register, REB-1093). So the act that settles such a group is a person saying it
was filed (`file_f24`: the day and, optionally, the receipt), with the permission of confirming a
match and no agent credential. It writes what a movement's match writes, the lines' payments
(`DueItemService._record_payment`) and the group's `settled` state (`F24GroupRepository.settle`),
only without a movement: every debit is paid in full, and the credits are spent oldest tax year
first until they cover the debits, so a group that nets to a credit spends only part of the last
one it touches and releases the credits it did not need, the rest of each staying open to go in
another F24 (`used_amount` on each line says how much was used). A group with an amount to pay is
refused: only the money leaving the account proves it was paid. Reopening a filed group reverses
the payments it wrote.

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
from pigrocrm.core.db import today_local
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
    F24GroupFile,
    F24GroupListQuery,
    F24GroupPage,
    F24GroupRead,
    F24LineRead,
    F24ReceiptRead,
    F24SettledBy,
    TaxCreditOriginRead,
)
from pigrocrm.core.fiscal.pack import FiscalPack, resolve_pack
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.inbox.access import visible_item
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.legal_entities.models import LegalEntity

ENTITY = "f24_group"
ZERO = Decimal("0.00")
EURO = "EUR"
RECEIPT_KIND = "tax_document"
FILING_NOTE = "F24 presentato senza importo da pagare"


def _settled_by(group: F24Group) -> F24SettledBy | None:
    if group.state != "settled":
        return None
    return "movement" if group.bank_movement_id is not None else "filing"


def _credit_left(group: F24Group, lines: Sequence[F24GroupItem]) -> Decimal | None:
    """What of its credits a settled group did not use, still open (zero for one a movement
    paid, which takes every line whole); null while the group is not settled."""
    if group.state != "settled":
        return None
    return sum(
        (
            -line.amount + (line.amount if line.used_amount is None else line.used_amount)
            for line in lines
            if line.amount < 0
        ),
        ZERO,
    )


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
        that cannot be paid with an F24 and says which. A net that is zero or a credit is
        allowed: that F24 is filed, not matched (`file_f24`)."""
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
        remaining = {
            item.id: item.amount - self.dues.repo.totals(item.id).settled for item in items
        }
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
            code = code_for(item, pack, payment_date, self.session)
            self.session.add(
                F24GroupItem(
                    group_id=group.id,
                    due_item_id=item.id,
                    legal_entity_id=legal_entity_id,
                    amount=remaining[item.id],
                    line=code.line,
                    code=code.code,
                    code_kind=code.kind,
                    code_label=code.label,
                    reference_year=code.reference_year,
                    reference_period=code.reference_period,
                )
            )
        self.session.flush()
        # Who grouped it and how many lines; never an amount, which is the fiscal position.
        self.activities.record(ENTITY, group.id, "f24_grouped", actor, {"lines": len(items)})
        self.session.commit()
        return self._read([group])[0]

    def ungroup_f24(self, group_id: UUID, actor: Actor) -> F24GroupRead:
        """Releases the lines of a group that is not paid: the items are free again and the
        group stays as `dissolved`. A group a movement paid is reopened first (undo the match),
        and so is one marked filed (`reopen_f24`)."""
        actor.require_admin("ungroup_f24")
        group = self._group(group_id, lock=True)
        if group.state == "dissolved":
            return self._read([group])[0]
        if group.state == "settled":
            raise Conflict(
                ENTITY,
                "l'F24 e' stato pagato: prima si annulla l'abbinamento al movimento"
                if group.bank_movement_id is not None
                else "l'F24 e' stato presentato: prima lo si riapre",
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

    # ---- filing a group with nothing to pay (REB-1093) --------------------------------------

    def file_f24(self, group_id: UUID, data: F24GroupFile, actor: Actor) -> F24GroupRead:
        """A person says an F24 whose net is zero or a credit was filed, on `filed_on`: the group
        settles as a movement's match settles one (every line paid, the credits spent) with no
        movement, and the receipt, when given, is linked. A group with an amount to pay is
        refused: only the money leaving the account says it was paid."""
        actor.require_person("file_f24")
        actor.require_admin("file_f24")
        group = self._group(group_id, lock=True)
        if group.state != "open":
            raise Conflict(
                ENTITY,
                "un F24 sciolto non si presenta"
                if group.state == "dissolved"
                else "l'F24 e' gia' segnato come pagato",
                id=str(group.id),
            )
        lines = self.repo.live_lines(group.id)
        if sum((line.amount for line in lines), ZERO) > 0:
            raise ValidationFailed(
                ENTITY,
                "group_id",
                "l'F24 ha un importo da pagare: lo salda il movimento in banca",
                expected="un F24 il cui totale netto e' zero o a credito",
            )
        if data.filed_on > today_local():
            raise ValidationFailed(
                ENTITY,
                "filed_on",
                "un F24 non e' presentato in una data futura",
                expected="il giorno in cui l'F24 e' stato presentato",
            )
        items = {item.id: item for item in self._lock([line.due_item_id for line in lines])}
        for line in lines:
            item = items[line.due_item_id]
            if (
                item.state != "expected"
                or item.amount - self.dues.repo.totals(item.id).settled != line.amount
            ):
                raise Conflict(
                    ENTITY, "una scadenza dell'F24 e' cambiata: riprova", id=str(item.id)
                )
        if data.receipt_inbox_item_id is not None:
            self._link_receipt(group, data.receipt_inbox_item_id, actor)
        used = self._credit_use(lines, items)
        self.repo.settle(group, data.filed_on)
        now = dt.datetime.now(dt.UTC)
        for line in sorted(lines, key=lambda row: str(row.due_item_id)):
            paid = line.amount if line.amount > 0 else used[line.id]
            settlement = (
                self.dues.settle_by_filing(
                    line.due_item_id, paid, data.filed_on, FILING_NOTE, actor
                )
                if paid != 0
                else None
            )
            # A credit the F24 did not use up is released: what is left of it is free to go in
            # another F24.
            self.repo.use_line(
                line,
                paid,
                None if settlement is None else settlement.id,
                release=line.amount < 0 and paid != line.amount,
                at=now,
            )
        self.activities.record(ENTITY, group.id, "f24_filed", actor, {"lines": len(lines)})
        self.session.commit()
        return self._read([group])[0]

    def reopen_f24(self, group_id: UUID, actor: Actor) -> F24GroupRead:
        """A person takes back the filing of a group: the payments it wrote are reversed and the
        group waits again. A group a movement paid is reopened by undoing the match. Refused when
        a credit it released went into another F24 or was used since."""
        actor.require_person("reopen_f24")
        actor.require_admin("reopen_f24")
        group = self._group(group_id, lock=True)
        if group.state != "settled" or group.bank_movement_id is not None:
            raise Conflict(
                ENTITY, "solo un F24 segnato come presentato si riapre da qui", id=str(group.id)
            )
        lines = self.repo.lines_of([group.id])[group.id]
        self._lock([line.due_item_id for line in lines])
        for line in lines:
            if line.released_at is None:
                continue
            live = {row.id for row in self.dues.repo.live_settlements(line.due_item_id)}
            if self.repo.group_of(line.due_item_id) is not None or live - {line.settlement_id}:
                raise Conflict(
                    ENTITY,
                    "il credito rimasto e' gia' in un altro F24 o e' stato usato",
                    id=str(line.due_item_id),
                )
        for line in sorted(lines, key=lambda row: str(row.due_item_id)):
            if line.settlement_id is not None:
                self.dues.release_for_movement(
                    line.settlement_id, adopted=False, reason="F24 riaperto", actor=actor
                )
        self.repo.reopen(group)
        self.activities.record(ENTITY, group.id, "f24_reopened", actor)
        self.session.commit()
        return self._read([group])[0]

    def _credit_use(
        self, lines: Sequence[F24GroupItem], items: dict[UUID, DueItem]
    ) -> dict[UUID, Decimal]:
        """How much of each credit line (by line id, negative like the line) the F24 spends: the
        credits are taken oldest tax year first until they cover the debits, so a net credit
        leaves the rest of the last one it touches and the ones after it whole."""
        source_ids = [items[line.due_item_id].source_id for line in lines if line.amount < 0]
        credits = self.repo.credits_by_id(
            source_id for source_id in source_ids if source_id is not None
        )

        def order(line: F24GroupItem) -> tuple[int, str]:
            source_id = items[line.due_item_id].source_id
            credit = credits.get(source_id) if source_id is not None else None
            return (credit.tax_year if credit is not None else 0, str(line.due_item_id))

        to_cover = sum((line.amount for line in lines if line.amount > 0), ZERO)
        used: dict[UUID, Decimal] = {}
        for line in sorted((row for row in lines if row.amount < 0), key=order):
            take = min(-line.amount, to_cover)
            to_cover -= take
            used[line.id] = -take if take else ZERO
        return used

    # ---- receipts ---------------------------------------------------------------------------

    def attach_f24_receipt(self, group_id: UUID, inbox_item_id: UUID, actor: Actor) -> F24GroupRead:
        """Points the group at an inbox item of kind `tax_document`, the receipt of the F24. The
        file stays in the inbox; an item of another company or of another kind is refused."""
        actor.require_admin("attach_f24_receipt")
        group = self._group(group_id, lock=True)
        if group.state == "dissolved":
            raise Conflict(ENTITY, "un F24 sciolto non ha ricevute", id=str(group.id))
        self._link_receipt(group, inbox_item_id, actor)
        self.session.commit()
        return self._read([group])[0]

    def _link_receipt(self, group: F24Group, inbox_item_id: UUID, actor: Actor) -> None:
        """The checks and the write of a receipt link, shared by `attach_f24_receipt` and by
        filing a group. Flushes, does not commit."""
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
        settled = self.dues.repo.totals(item.id).settled
        # A credit an earlier F24 used in part is open with what is left (REB-1093); anything
        # else paid, even in part, is not.
        part_used_credit = item.amount < 0 and item.state == "expected"
        if item.state == "settled" or (settled != ZERO and not part_used_credit):
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
        broken_year = self._broken_year_entities({group.legal_entity_id for group in groups})
        return [
            self._group_read(
                group,
                lines[group.id],
                receipts[group.id],
                items,
                credits,
                names,
                group.legal_entity_id in broken_year,
            )
            for group in groups
        ]

    def _broken_year_entities(self, legal_entity_ids: set[UUID]) -> set[UUID]:
        """The legal entities whose fiscal year does not end on 31 December, whose F24 has the
        header box «non coincidente con anno solare» ticked."""
        if not legal_entity_ids:
            return set()
        return set(
            self.session.scalars(
                select(LegalEntity.id).where(
                    LegalEntity.id.in_(legal_entity_ids),
                    or_(
                        LegalEntity.fiscal_year_end_month != 12,
                        LegalEntity.fiscal_year_end_day != 31,
                    ),
                )
            )
        )

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
        fiscal_year_not_calendar_year: bool,
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
                    reference_period=line.reference_period,
                    released=line.released_at is not None,
                    used_amount=line.used_amount,
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
            fiscal_year_not_calendar_year=fiscal_year_not_calendar_year,
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
            settled_by=_settled_by(group),
            credit_left=_credit_left(group, lines),
            created_by_type=group.created_by_type,
            created_at=group.created_at,
            updated_at=group.updated_at,
        )


__all__ = ["F24GroupService"]
