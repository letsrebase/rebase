"""The only writer of `expense_notes` (spec 2026-11-09, staff, payroll and expenses, § 3 and § 4).

**Who may do what** (the design's table, with no new role):

- *submit*: the staff member (a user linked to one, with the collaborator or administrator role:
  `readonly` cannot write) for their own notes, or an administrator for any staff member in
  their scope, a person who is not a user included. An agent credential submits only for the
  staff member its owner is, from an inbox item the owner uploaded (`submit_expense_note`);
- *read*: a staff member reads their own notes, an administrator (and a `full_access` token)
  the notes of the legal entities in scope; the entity scope itself is Postgres's;
- *approve, reject, reimburse*: an administrator, never on their own note, and an agent only
  with `full_access`. A note is **own** when the staff member is the deciding user or the user
  typed it in. In a space with one active administrator that administrator is the only one who
  can decide, so approving their own note is allowed and the note is flagged `self_approved`,
  which the accountant export says (C8); an agent never decides an own note, whatever the space.

**What approving does.** It judges the expense: the note becomes a purchase of kind
`expense_note` (cost, VAT and export have one owner of the figure) and writes the one outgoing
due item to the person, from that day. **Reimbursing** is its own act (brief question 5): it
chooses the way and when money leaves. By `transfer` the item is dated and, when the transfer
already left, settled; otherwise it is settled like any other item (a person's mark, a bank
movement) and `projection.py` turns the note `reimbursed`. In a `payslip` the item waits for the
accepted payslip of the chosen month, which settles it (`payslips.py`): no second due item.
Reimbursing happens once: a note already `reimbursed` is a 409, and a way chosen can be chosen
again only while nothing was paid.

Method names are unique across the services on purpose, as `StaffService`'s are: the MCP ban
test finds the privileged tools by name.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import EXPENSE_NOTE_SOURCE_KIND
from pigrocrm.core.due_items.repository import DueItemRepository
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.expense_notes.access import (
    is_own_note,
    may_decide_on_own,
    reads_all_notes,
    require_may_decide,
)
from pigrocrm.core.expense_notes.models import ExpenseNote
from pigrocrm.core.expense_notes.repository import ExpenseNoteRepository
from pigrocrm.core.expense_notes.schemas import (
    ExpenseNoteCreate,
    ExpenseNoteListQuery,
    ExpenseNotePage,
    ExpenseNoteRead,
    ExpenseNoteReimburse,
    ExpenseNoteReject,
    ExpenseNoteSummary,
)
from pigrocrm.core.inbox.access import visible_item
from pigrocrm.core.inbox.filing import file_as_purchase, resolve_original
from pigrocrm.core.money import round_money
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.staff.estimate import PayDay, add_months, first_of
from pigrocrm.core.staff.models import StaffMember
from pigrocrm.core.staff.repository import StaffRepository
from pigrocrm.core.timetracking.categories import CostCategoryService

ENTITY = "expense_note"
NOT_LINKED = "Non risulti tra le persone dell'azienda: chiedi a un amministratore di collegarti."
# How many months ahead the next payroll month without a payslip is looked for.
PAYROLL_MONTHS_AHEAD = 24


class ExpenseNoteService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ExpenseNoteRepository(session)
        self.staff = StaffRepository(session)
        self.activities = ActivityService(session)
        self.categories = CostCategoryService(session)
        self.dues = DueItemService(session)
        self.due_items = DueItemRepository(session)

    # ---- who ---------------------------------------------------------------------------

    def _own_member(self, actor: Actor) -> StaffMember | None:
        return None if actor.id is None else self.repo.staff_member_of_user(actor.id)

    def _require_may_decide(
        self, note: ExpenseNote, member: StaffMember, actor: Actor, action: str
    ) -> bool:
        return require_may_decide(self.session, note, member, actor, action)

    def _require_member_in_service(self, member: StaffMember, day: dt.date) -> None:
        if day < member.start_date or (member.end_date is not None and day > member.end_date):
            raise ValidationFailed(
                ENTITY,
                "date",
                "la persona non era in servizio il giorno della spesa",
                expected="un giorno tra l'inizio e la fine del rapporto",
            )

    # ---- writes ------------------------------------------------------------------------

    def submit_expense_note(self, data: ExpenseNoteCreate, actor: Actor) -> ExpenseNoteRead:
        actor.require_write("submit_expense_note")
        member = self._submitting_member(data, actor)
        if data.date > today_local():
            raise ValidationFailed(
                ENTITY, "date", "la spesa e' di un giorno che non e' ancora arrivato"
            )
        self._require_member_in_service(member, data.date)
        self.categories.require_active(data.category_id)
        amount = self._amount(data)
        inbox_item_id = self._receipt(data.inbox_item_id, member, actor)
        note = self.repo.add(
            ExpenseNote(
                staff_member_id=member.id,
                legal_entity_id=member.legal_entity_id,
                kind=data.kind,
                status="submitted",
                date=data.date,
                description=data.description,
                category_id=data.category_id,
                amount=amount,
                kilometres=data.kilometres,
                rate_per_km=data.rate_per_km,
                route_from=data.route_from,
                route_to=data.route_to,
                inbox_item_id=inbox_item_id,
                submitted_by_id=actor.id,
                submitted_by_type=actor.type,
            )
        )
        self.activities.record(
            ENTITY, note.id, "expense_note_submitted", actor, {"kind": note.kind}
        )
        self.session.commit()
        return self._reads([note], actor)[0]

    def approve_expense_note(self, note_id: UUID, actor: Actor) -> ExpenseNoteRead:
        actor.require_admin("approve_expense_note")
        note = self._locked(note_id)
        member = self._member(note)
        if note.status != "submitted":
            raise Conflict(
                ENTITY, "la nota e' gia' stata decisa", id=str(note.id), status=note.status
            )
        self_approved = self._require_may_decide(note, member, actor, "approve_expense_note")
        purchase = PurchaseService(self.session).register_expense_note(note)
        self.activities.record(
            "purchase",
            purchase.id,
            "created",
            actor,
            {"kind": purchase.kind, "amount": str(purchase.amount), "deal_id": None},
        )
        if purchase.inbox_item_id is not None:
            file_as_purchase(
                self.session, purchase.inbox_item_id, purchase.id, actor, purchase.legal_entity_id
            )
        item = self.dues.create(
            DueItemCreate(
                legal_entity_id=note.legal_entity_id,
                direction="out",
                counterparty_type="staff_member",
                staff_member_id=note.staff_member_id,
                amount=note.amount,
                description=f"Rimborso nota spese: {note.description}"[:255],
                source_kind=EXPENSE_NOTE_SOURCE_KIND,
                source_id=note.id,
                own_date=today_local(),
            )
        )
        note.status = "approved"
        note.purchase_id = purchase.id
        note.due_item_id = item.id
        self._decided(note, actor)
        note.self_approved = self_approved
        self.activities.record(
            ENTITY, note.id, "expense_note_approved", actor, {"self_approved": self_approved}
        )
        self.session.commit()
        return self._reads([note], actor)[0]

    def reject_expense_note(
        self, note_id: UUID, data: ExpenseNoteReject, actor: Actor
    ) -> ExpenseNoteRead:
        actor.require_admin("reject_expense_note")
        note = self._locked(note_id)
        member = self._member(note)
        if note.status != "submitted":
            raise Conflict(
                ENTITY, "la nota e' gia' stata decisa", id=str(note.id), status=note.status
            )
        self._require_may_decide(note, member, actor, "reject_expense_note")
        note.status = "rejected"
        note.rejection_reason = data.reason
        self._decided(note, actor)
        self.activities.record(ENTITY, note.id, "expense_note_rejected", actor)
        self.session.commit()
        return self._reads([note], actor)[0]

    def reimburse_expense_note(
        self, note_id: UUID, data: ExpenseNoteReimburse, actor: Actor
    ) -> ExpenseNoteRead:
        actor.require_admin("reimburse_expense_note")
        note = self._locked(note_id)
        member = self._member(note)
        if note.status == "reimbursed":
            raise Conflict(ENTITY, "la nota e' gia' rimborsata", id=str(note.id))
        if note.status != "approved":
            raise Conflict(
                ENTITY,
                "si rimborsa una nota approvata",
                id=str(note.id),
                status=note.status,
            )
        self._require_may_decide(note, member, actor, "reimburse_expense_note")
        assert note.due_item_id is not None
        if self.due_items.totals(note.due_item_id).settled != 0:
            raise Conflict(
                ENTITY, "la scadenza ha un pagamento: il modo non si cambia", id=str(note.id)
            )
        if data.method == "transfer":
            assert data.date is not None
            if data.paid and data.date > today_local():
                raise ValidationFailed(
                    ENTITY, "date", "un bonifico non e' ancora partito in una data futura"
                )
            date, month = data.date, None
        else:
            # Read before any field changes: these queries flush, and a half-chosen way breaks
            # the table's own check.
            month = self._payroll_month(member, data.payroll_month)
            date = self._pay_date(member, month)
        note.reimbursement_method = data.method
        note.reimbursement_date = date
        note.payroll_month = month
        self.session.flush()
        self.dues.reschedule(note.due_item_id, note.reimbursement_date)
        if data.method == "transfer" and data.paid:
            assert data.date is not None
            self.dues.settle_remaining(note.due_item_id, data.date, "Bonifico", actor)
        self.activities.record(
            ENTITY,
            note.id,
            "expense_note_reimbursed" if note.status == "reimbursed" else "expense_note_way_chosen",
            actor,
            {"method": data.method},
        )
        self.session.commit()
        return self._reads([note], actor)[0]

    # ---- reads -------------------------------------------------------------------------

    def get_expense_note(self, note_id: UUID, actor: Actor) -> ExpenseNoteRead:
        note = self.repo.get(note_id)
        if note is None:
            raise NotFound(ENTITY, note_id)
        if not reads_all_notes(actor):
            own = self._own_member(actor)
            if own is None or note.staff_member_id != own.id:
                raise NotFound(ENTITY, note_id)
        return self._reads([note], actor)[0]

    def summarize_expense_notes(self, actor: Actor) -> ExpenseNoteSummary:
        """What the page needs before it lists: who the caller is among the staff, the count of
        each tab and the money still to be paid back."""
        own = self._own_member(actor)
        reads_all = reads_all_notes(actor)
        scope = None if reads_all else (own.id if own is not None else None)
        if not reads_all and own is None:
            counts = {"to_approve": 0, "to_reimburse": 0, "closed": 0}
            awaiting = Decimal("0.00")
        else:
            counts = self.repo.bucket_counts(scope)
            awaiting = self.repo.awaiting_total(scope)
        return ExpenseNoteSummary(
            staff_member_id=own.id if own is not None else None,
            staff_member_name=own.name if own is not None else None,
            staff_member_legal_entity_id=own.legal_entity_id if own is not None else None,
            can_submit=actor.can_write and (own is not None or reads_all),
            to_approve=counts["to_approve"],
            to_reimburse=counts["to_reimburse"],
            closed=counts["closed"],
            awaiting_reimbursement=awaiting,
            only_administrator=reads_all and may_decide_on_own(self.session, actor),
        )

    # `list_expense_notes` stays the last method in this class -- the unconditional project
    # rule (a `def list(...)` would rebind `list` in the class namespace).
    def list_expense_notes(self, query: ExpenseNoteListQuery, actor: Actor) -> ExpenseNotePage:
        if reads_all_notes(actor):
            scope: UUID | None = None
        else:
            own = self._own_member(actor)
            if own is None:
                return ExpenseNotePage(
                    items=[], next_cursor=None, total_count=0, total_amount=Decimal("0.00")
                )
            scope = own.id
        rows = self.repo.list_rows(query, scope)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        total_count, total_amount = self.repo.count_and_total(query, scope)
        return ExpenseNotePage(
            items=self._reads(items, actor),
            next_cursor=items[-1].id if has_more and items else None,
            total_count=total_count,
            total_amount=total_amount,
        )

    # ---- the pieces --------------------------------------------------------------------

    def _locked(self, note_id: UUID) -> ExpenseNote:
        note = self.repo.get_locked(note_id)
        if note is None:
            raise NotFound(ENTITY, note_id)
        return note

    def _member(self, note: ExpenseNote) -> StaffMember:
        member = self.repo.staff_member(note.staff_member_id)
        if member is None:
            raise NotFound("staff_member", note.staff_member_id)
        return member

    def _submitting_member(self, data: ExpenseNoteCreate, actor: Actor) -> StaffMember:
        """The staff member the note is for. An administrator person names anyone in scope (or
        is their own); everyone else, an agent included, is their own staff member."""
        own = self._own_member(actor)
        for_others = actor.type == "user" and actor.can_administer
        if data.staff_member_id is None or (own is not None and data.staff_member_id == own.id):
            if own is None:
                raise Conflict(ENTITY, NOT_LINKED)
            return own
        if not for_others:
            raise PermissionDenied("submit_expense_note", ["admin"], actor.role)
        member = self.repo.staff_member(data.staff_member_id)
        if member is None:
            raise NotFound("staff_member", data.staff_member_id)
        return member

    @staticmethod
    def _amount(data: ExpenseNoteCreate) -> Decimal:
        """An expense note's amount is what the receipt says; a mileage note's is the
        kilometres times the rate the person typed, rounded to the cent."""
        if data.kind == "expense":
            assert data.amount is not None
            return data.amount
        assert data.kilometres is not None and data.rate_per_km is not None
        amount = round_money(data.kilometres * data.rate_per_km)
        if amount <= 0:
            raise ValidationFailed(
                ENTITY,
                "kilometres",
                "i chilometri per la tariffa non arrivano a un centesimo",
                expected="un importo di almeno un centesimo",
            )
        return amount

    def _receipt(
        self, inbox_item_id: UUID | None, member: StaffMember, actor: Actor
    ) -> UUID | None:
        """The inbox item that holds the receipt: one the submitter sees, that is not
        discarded and backs no other live note. An unclassified one takes the staff member's
        legal entity, so the administrators of that entity can open it."""
        if inbox_item_id is None:
            return None
        item = visible_item(self.session, inbox_item_id, actor)
        if item.state == "discarded":
            raise ValidationFailed(
                ENTITY,
                "inbox_item_id",
                "il documento e' stato scartato",
                expected="un documento in arrivo",
            )
        original = resolve_original(self.session, item.id, actor)
        if self.repo.noted_item(original) is not None:
            raise Conflict(
                ENTITY, "lo scontrino e' gia' in un'altra nota", inbox_item_id=str(original)
            )
        if item.legal_entity_id is None:
            item.legal_entity_id = member.legal_entity_id
        return original

    @staticmethod
    def _decided(note: ExpenseNote, actor: Actor) -> None:
        note.decided_by_id = actor.id
        note.decided_by_type = actor.type
        note.decided_at = dt.datetime.now(dt.UTC)

    def _payroll_month(self, member: StaffMember, wanted: dt.date | None) -> dt.date:
        """The month whose payslip pays the note: the one asked for, or the person's first
        month from this one with no payslip recorded yet."""
        start, end = (
            first_of(member.start_date),
            (first_of(member.end_date) if member.end_date is not None else None),
        )
        if wanted is not None:
            month = first_of(wanted)
            if month < start or (end is not None and month > end):
                raise ValidationFailed(
                    ENTITY,
                    "payroll_month",
                    "la persona non era in servizio in quel mese",
                    expected="un mese tra l'inizio e la fine del rapporto",
                )
            if self.staff.payslip(member.id, month) is not None:
                raise Conflict(
                    ENTITY,
                    "il cedolino di quel mese e' gia' registrato: scegli un mese dopo",
                    payroll_month=month.isoformat(),
                )
            return month
        month = max(first_of(today_local()), start)
        for _ in range(PAYROLL_MONTHS_AHEAD):
            if (end is not None and month > end) or self.staff.payslip(member.id, month) is None:
                break
            month = add_months(month, 1)
        if end is not None and month > end:
            raise ValidationFailed(
                ENTITY,
                "payroll_month",
                "non c'e' un mese di paga ancora aperto: il rapporto e' finito",
                expected="un rimborso con bonifico",
            )
        return month

    def _pay_date(self, member: StaffMember, month: dt.date) -> dt.date:
        """The day the payslip of `month` pays: the person's pay day, else the legal entity's;
        with neither typed, the last day of the month the payslip is for."""
        day: PayDay | None = None
        if member.pay_day is not None and member.pay_month_offset is not None:
            day = PayDay(member.pay_day, member.pay_month_offset)
        else:
            found = self.staff.pay_day(member.legal_entity_id)
            if found is not None:
                day = PayDay(found.pay_day, found.pay_month_offset)
        if day is None:
            return add_months(month, 1) - dt.timedelta(days=1)
        return add_months(month, day.month_offset).replace(day=day.day)

    def _reads(self, notes: list[ExpenseNote], actor: Actor) -> list[ExpenseNoteRead]:
        """The notes as the caller reads them: names beside the ids, and what the caller may
        do with each (`own`, `can_decide`)."""
        if not notes:
            return []
        members = {
            member.id: member
            for member in (
                self.repo.staff_member(id_) for id_ in {n.staff_member_id for n in notes}
            )
            if member is not None
        }
        member_names, category_names, receipt_names, user_names = self.repo.names(
            members.keys(),
            {note.category_id for note in notes},
            {note.inbox_item_id for note in notes if note.inbox_item_id is not None},
            {note.decided_by_id for note in notes if note.decided_by_id is not None},
        )
        deciding = reads_all_notes(actor)
        sole = deciding and may_decide_on_own(self.session, actor)
        out: list[ExpenseNoteRead] = []
        for note in notes:
            member = members.get(note.staff_member_id)
            own = member is not None and is_own_note(note, member, actor)
            can_decide = (
                deciding
                and note.status == "submitted"
                and (not own or (sole and actor.type == "user"))
            )
            out.append(
                ExpenseNoteRead.model_validate(
                    {
                        **{
                            name: getattr(note, name)
                            for name in ExpenseNoteRead.model_fields
                            if hasattr(note, name)
                        },
                        "staff_member_name": member_names.get(note.staff_member_id, ""),
                        "category_name": category_names.get(note.category_id, ""),
                        "receipt_name": receipt_names.get(note.inbox_item_id)
                        if note.inbox_item_id is not None
                        else None,
                        "decided_by_name": user_names.get(note.decided_by_id)
                        if note.decided_by_id is not None and note.decided_by_type != "system"
                        else None,
                        "own": own,
                        "can_decide": can_decide,
                    }
                )
            )
        return out
