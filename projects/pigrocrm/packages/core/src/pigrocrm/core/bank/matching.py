"""Matching a bank movement to the due items it pays (spec 2026-11-09, due items and the cash
schedule, § 3; REB-1042).

A movement is **proposed** to the items it can pay, by the importer when a statement is read or
by an agent, and a **person confirms** it: confirming settles each item with the movement as the
payment's evidence, or, when the item was already marked paid by hand for the same amount,
attaches the movement to that payment so it does not make a second one. A confirmed match is
**undone** by a person, which reverses the payments it wrote (the rows stay, stamped) and lets
go of the ones it adopted. Nothing settles on a proposal and nothing settles automatically: an
agent never confirms (`Actor.require_person`), and the importer only writes proposals.

What is proposed is what the server ranks (`match_rank.py`): the amount, the number on the
document in the bank's text and who paid. A known counterparty IBAN only ranks. The importer
writes a proposal on its own only for a fit nothing else competes with.

Authority lives where the record does: the movement is an administrator's (the whole cash
module is), and the settlement is `DueItemService`'s, which asks for the item's own permission
(an outgoing or fiscal item is an administrator's act, and a `full_access` token's). Settling
here is a cash fact and the month lock does not apply (§ 3).
"""

import datetime as dt
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from decimal import Decimal
from itertools import groupby
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.bank import match_rank as rank
from pigrocrm.core.bank.constants import ADOPT_WINDOW_DAYS
from pigrocrm.core.bank.match_reads import (
    SALES_INVOICE,
    ItemFacts,
    facts_for_ids,
    facts_for_items,
    item_read,
    match_history,
)
from pigrocrm.core.bank.match_schemas import (
    MatchCandidate,
    MatchConfirm,
    MatchItemRead,
    MatchPreview,
    MatchProposalCreate,
    MatchSelection,
    MatchUndo,
)
from pigrocrm.core.bank.models import (
    BankMovement,
    MovementMatch,
    MovementMatchItem,
    OrganizationIban,
)
from pigrocrm.core.bank.movement_reads import movement_reads
from pigrocrm.core.bank.schemas import MovementDetail
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import remaining_amount
from pigrocrm.core.due_items.schedule_queries import kinds_of_modules_off
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.staff.models import StaffMember

ENTITY = "bank_movement"
MATCH_ENTITY = "movement_match"
ZERO = Decimal("0.00")
# The panel offers up to three ranked candidates (brief, «The matching panel»).
CANDIDATE_LIMIT = 3
# The open items a movement is looked up against: a space's open items for one currency are a few
# hundred, and a cap keeps one request from reading a table.
POOL_MAX = 1000
OPEN_ITEMS_LIMIT_DEFAULT = 20
OPEN_ITEMS_LIMIT_MAX = 50


@dataclass(frozen=True)
class Allocation:
    """What one item takes of the movement. `amount` is a magnitude; `adopts` says it goes to a
    payment already marked by hand."""

    fact: ItemFacts
    amount: Decimal
    adopts: bool


@dataclass(frozen=True)
class Plan:
    """A selection of items worked out against a movement: who takes what, what is left of the
    movement, whether an item is paid in part and the reasons the selection holds."""

    allocations: tuple[Allocation, ...]
    difference: Decimal
    partial: bool
    reasons: list[str]

    @property
    def allocated(self) -> Decimal:
        return sum((a.amount for a in self.allocations), ZERO)


@dataclass(frozen=True)
class _Context:
    """What ranking compares an item against: the bank's text and the organisations whose IBAN
    a person accepted for the movement's counterparty."""

    movement: BankMovement
    text: str
    ibans: frozenset[UUID]

    @property
    def magnitude(self) -> Decimal:
        return abs(self.movement.amount)


def _cash_sign(item: DueItem) -> int:
    """Which way the money moves when the item is paid: an amount owed to us comes in, one we owe
    goes out, a credit the other way round."""
    sign = 1 if item.amount > 0 else -1
    return sign if item.direction == "in" else -sign


def _fits_direction(movement: BankMovement, item: DueItem) -> bool:
    return (_cash_sign(item) > 0) == (movement.amount > 0)


def _direction_clause(movement: BankMovement) -> ColumnElement[bool]:
    """In SQL, the items a movement of this sign can pay (`_cash_sign`)."""
    if movement.amount > 0:
        return or_(
            and_(DueItem.direction == "in", DueItem.amount > 0),
            and_(DueItem.direction == "out", DueItem.amount < 0),
        )
    return or_(
        and_(DueItem.direction == "in", DueItem.amount < 0),
        and_(DueItem.direction == "out", DueItem.amount > 0),
    )


def _search_clause(needle: str) -> ColumnElement[bool]:
    """In SQL, the items whose description, counterparty, tax or document number contains
    `needle`, before the pool is capped: a search must reach an item past the first thousand."""
    return or_(
        DueItem.description.icontains(needle, autoescape=True),
        DueItem.tax.icontains(needle, autoescape=True),
        DueItem.organization_id.in_(
            select(Organization.id).where(
                Organization.legal_name.icontains(needle, autoescape=True)
            )
        ),
        DueItem.staff_member_id.in_(
            select(StaffMember.id).where(StaffMember.name.icontains(needle, autoescape=True))
        ),
        and_(
            DueItem.source_kind == SALES_INVOICE,
            DueItem.source_id.in_(
                select(Invoice.id).where(
                    func.concat(Invoice.year, "/", Invoice.number).icontains(
                        needle, autoescape=True
                    )
                )
            ),
        ),
        and_(
            DueItem.source_kind == "purchase",
            DueItem.source_id.in_(
                select(Purchase.id).where(Purchase.number.icontains(needle, autoescape=True))
            ),
        ),
    )


def _earliest(fact: ItemFacts) -> tuple[bool, dt.date, str]:
    return (fact.item.due_date is None, fact.item.due_date or dt.date.max, str(fact.item.id))


class MovementMatchService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.activities = ActivityService(session)
        self.due_items = DueItemService(session)

    # ---- reads ----------------------------------------------------------------------------

    def get_movement(
        self,
        movement_id: UUID,
        actor: Actor,
        *,
        modules: AbstractSet[str] | None = None,
    ) -> MovementDetail:
        """One movement with its live proposal or confirmed match, the candidates the server
        ranked while it waits, and what was dismissed or undone before."""
        actor.require_admin("get_bank_movement")
        return self._detail(self._movement(movement_id), modules)

    def list_open_items(
        self,
        movement_id: UUID,
        actor: Actor,
        *,
        search: str | None = None,
        limit: int = OPEN_ITEMS_LIMIT_DEFAULT,
        modules: AbstractSet[str] | None = None,
    ) -> list[MatchItemRead]:
        """The open items this movement could pay, the ones of its legal entity, currency and
        direction, by due date; `search` keeps those whose counterparty, description or document
        number contains it. What «Cerca un'altra voce» lists."""
        actor.require_admin("get_bank_movement")
        if not 1 <= limit <= OPEN_ITEMS_LIMIT_MAX:
            raise ValidationFailed(
                MATCH_ENTITY,
                "limit",
                "fuori intervallo",
                expected=f"1-{OPEN_ITEMS_LIMIT_MAX}",
            )
        movement = self._movement(movement_id)
        needle = (search or "").strip().casefold()
        facts = self._open_pool(movement, modules, search=needle or None)
        if needle:
            facts = [
                fact
                for fact in facts
                if needle
                in " ".join(
                    filter(
                        None,
                        (fact.counterparty_name, fact.item.description, fact.document_number),
                    )
                ).casefold()
            ]
        return [item_read(fact, ZERO, False) for fact in facts[:limit]]

    def preview(
        self,
        movement_id: UUID,
        selection: MatchSelection,
        actor: Actor,
        *,
        modules: AbstractSet[str] | None = None,
    ) -> MatchPreview:
        """What confirming `selection` would do: the part of the movement each item takes, what
        is left over and whether an item would be paid in part. Writes nothing."""
        actor.require_admin("get_bank_movement")
        movement = self._movement(movement_id)
        plan = self._plan(movement, selection.due_item_ids, modules)
        return MatchPreview(
            items=self._plan_items(plan),
            reasons=plan.reasons,  # type: ignore[arg-type]
            allocated=plan.allocated,
            difference=plan.difference,
            partial=plan.partial,
            can_confirm=plan.difference == 0 and bool(plan.allocations),
        )

    # ---- writes ---------------------------------------------------------------------------

    def propose_match(
        self,
        movement_id: UUID,
        data: MatchProposalCreate,
        actor: Actor,
        *,
        modules: AbstractSet[str] | None = None,
    ) -> MovementDetail:
        """Records that the movement pays these items, for a person to confirm. It settles
        nothing. A proposal that covers the movement exactly, in full or with a last item paid
        in part; one that is waiting is replaced."""
        actor.require_admin("propose_movement_match")
        movement = self._movement(movement_id, lock=True)
        self._require_unmatched(movement)
        plan = self._plan(movement, data.due_item_ids, modules)
        self._require_covered(plan)
        self._dismiss_proposal(movement, actor, "sostituita da una nuova proposta")
        match = self._add_match(movement, plan, "proposed", actor, note=data.note)
        self._record_match("match_proposed", movement, match, plan, actor)
        self.session.commit()
        return self._detail(movement, modules)

    def confirm(
        self,
        movement_id: UUID,
        data: MatchConfirm,
        actor: Actor,
        *,
        modules: AbstractSet[str] | None = None,
    ) -> MovementDetail:
        """A person's act: settles each item with the movement as evidence, dated on its booking
        date, and the movement reads `matched`. The selection must cover the movement exactly; an
        item paid only in part needs `accept_partial`."""
        actor.require_person("confirm_movement_match")
        actor.require_admin("confirm_movement_match")
        movement = self._movement(movement_id, lock=True)
        self._require_unmatched(movement)
        plan = self._plan(movement, data.due_item_ids, modules)
        self._require_covered(plan)
        if plan.partial and not data.accept_partial:
            raise ValidationFailed(
                MATCH_ENTITY,
                "accept_partial",
                "il movimento salda solo una parte di una scadenza: va confermato",
                expected="accept_partial true",
            )
        live = self._live(movement.id)
        match = (
            live if live is not None and self._item_ids(live) == set(data.due_item_ids) else None
        )
        if match is None:
            self._dismiss_proposal(movement, actor, "sostituita dalla scelta di una persona")
            match = self._add_match(movement, plan, "confirmed", actor)
        else:
            self._replace_items(match, plan)
            match.reasons = plan.reasons
            match.state = "confirmed"
            match.confirmed_at = _now()
            match.confirmed_by_id = actor.id
            match.confirmed_by_type = actor.type
        self.session.flush()
        rows = {row.due_item_id: row for row in self._items(match)}
        for allocation in sorted(plan.allocations, key=lambda a: str(a.fact.item.id)):
            item = allocation.fact.item
            if allocation.adopts:
                settlement = self.due_items.adopt_for_movement(item.id, movement, actor)
            else:
                signed = allocation.amount if item.amount > 0 else -allocation.amount
                settlement = self.due_items.settle_from_movement(item.id, signed, movement, actor)
            rows[item.id].settlement_id = settlement.id
            rows[item.id].adopted = allocation.adopts
        movement.state = "matched"
        self.session.flush()
        self._record_match("matched", movement, match, plan, actor)
        self.session.commit()
        return self._detail(movement, modules)

    def undo(
        self,
        movement_id: UUID,
        data: MatchUndo,
        actor: Actor,
        *,
        modules: AbstractSet[str] | None = None,
    ) -> MovementDetail:
        """A person takes a confirmed match back: the payments it wrote are reversed (the rows
        stay, stamped, with the movement as their evidence), the ones it adopted lose the
        movement and stay as they were marked, the items reopen and the movement waits again.
        The match stays as `undone`."""
        actor.require_person("undo_movement_match")
        actor.require_admin("undo_movement_match")
        movement = self._movement(movement_id, lock=True)
        match = self._live(movement.id)
        if match is None or match.state != "confirmed":
            raise Conflict(ENTITY, "il movimento non e' abbinato", id=str(movement.id))
        for row in sorted(self._items(match), key=lambda r: str(r.due_item_id)):
            if row.settlement_id is None:
                continue
            self.due_items.release_for_movement(
                row.settlement_id, adopted=row.adopted, reason=data.reason, actor=actor
            )
        match.state = "undone"
        match.closed_at = _now()
        match.closed_by_id = actor.id
        match.closed_by_type = actor.type
        match.close_reason = data.reason
        movement.state = "unmatched"
        self.session.flush()
        self._record_match("unmatched", movement, match, None, actor)
        self.session.commit()
        return self._detail(movement, modules)

    # ---- the importer's half: no actor check, no commit -----------------------------------

    def propose_for_import(
        self, movement_ids: Sequence[UUID], *, modules: AbstractSet[str] | None = None
    ) -> int:
        """Writes a proposal, as `system`, for each new movement that has exactly one fit worth
        proposing, and answers how many. A fit worth proposing covers the movement exactly and
        has evidence besides the amount (the document's number in the bank's text, a known IBAN or
        the counterparty's name), and nothing ranks beside it: a document number found on only
        one candidate, else a single candidate. Anything ambiguous is left for a person, who sees
        the ranked candidates in the panel. Flushes, does not commit."""
        movements = list(
            self.session.scalars(
                select(BankMovement)
                .where(BankMovement.id.in_(list(movement_ids)), BankMovement.state == "unmatched")
                .order_by(BankMovement.booking_date, BankMovement.id)
            )
        )
        actor = Actor.system()
        written = 0
        pools: dict[tuple[UUID, str], list[ItemFacts]] = {}
        taken: set[UUID] = set()
        for movement in movements:
            key = (movement.legal_entity_id, movement.currency)
            if key not in pools:
                pools[key] = self._open_pool(movement, modules, directional=False)
            pool = [fact for fact in pools[key] if _fits_direction(movement, fact.item)]
            candidates = self._rank(movement, pool, self._adoptable(movement, modules))
            # An item already proposed to an earlier movement of this import is not offered again:
            # the importer's guarantee is that nothing competes for what it proposes.
            candidates = [
                plan
                for plan in candidates
                if not {a.fact.item.id for a in plan.allocations} & taken
            ]
            chosen = self._certain(candidates)
            if chosen is None:
                continue
            taken.update(fact.item.id for fact in chosen)
            plan = self._plan_of(movement, chosen)
            match = self._add_match(movement, plan, "proposed", actor)
            self._record_match("match_proposed", movement, match, plan, actor)
            written += 1
        return written

    # ---- the ranking ----------------------------------------------------------------------

    def _candidates(
        self, movement: BankMovement, modules: AbstractSet[str] | None
    ) -> list[MatchCandidate]:
        pool = self._open_pool(movement, modules)
        ranked = self._rank(movement, pool, self._adoptable(movement, modules))
        return [self._candidate(plan) for plan in ranked[:CANDIDATE_LIMIT]]

    def _rank(
        self, movement: BankMovement, pool: list[ItemFacts], adoptable: list[ItemFacts]
    ) -> list[Plan]:
        """The plans the movement could be paid by, best first: a single item, a split across
        items of one counterparty that adds up to it, or a payment already marked by hand that
        the movement would adopt. Each has a reason besides, or instead of, the bare amount."""
        context = self._context(movement)
        magnitude = context.magnitude
        scored: list[tuple[int, tuple[bool, dt.date, str], Plan]] = []

        def add(facts: Sequence[ItemFacts], adopts: bool) -> None:
            if adopts:
                plan = Plan(
                    allocations=(Allocation(facts[0], magnitude, True),),
                    difference=ZERO,
                    partial=False,
                    reasons=rank.selection_reasons(
                        [self._fit(facts[0], context, True)], magnitude, magnitude
                    ),
                )
                eligible = bool(set(plan.reasons) & rank.EVIDENCE)
            else:
                plan = self._plan_of(movement, facts, context)
                evidence = bool(set(plan.reasons) & rank.EVIDENCE)
                # A single item is offered on its amount alone; a split, being a sum of
                # amounts, needs a reason that says who paid.
                eligible = evidence or (len(facts) == 1 and rank.EXACT_AMOUNT in plan.reasons)
            if eligible and plan.difference == 0:
                scored.append(
                    (
                        -rank.score(plan.reasons),
                        min(_earliest(a.fact) for a in plan.allocations),
                        plan,
                    )
                )

        for fact in pool:
            if fact.remaining_magnitude >= magnitude:
                add([fact], False)
        for fact in adoptable:
            if fact.remaining_magnitude < magnitude:
                add([fact], True)
        short = [fact for fact in pool if 0 < fact.remaining_magnitude < magnitude]
        short.sort(key=lambda fact: (str(fact.item.organization_id), *_earliest(fact)))
        for _, group in groupby(short, key=lambda fact: fact.item.organization_id):
            members = list(group)
            if len(members) < 2 or members[0].item.organization_id is None:
                continue
            by_id = {fact.item.id: fact for fact in members}
            fits = [self._fit(fact, context, False) for fact in members]
            for chosen in rank.split_combinations(fits, magnitude):
                add([by_id[fit.item_id] for fit in chosen], False)
        scored.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in scored]

    @staticmethod
    def _certain(plans: Sequence[Plan]) -> Sequence[ItemFacts] | None:
        """The one plan the importer may propose on its own, as its items, or `None`."""
        fits = [plan for plan in plans if not plan.partial and set(plan.reasons) & rank.EVIDENCE]
        strong = [plan for plan in fits if rank.DOCUMENT_NUMBER in plan.reasons]
        pick = strong if strong else fits
        if len(pick) != 1:
            return None
        return [allocation.fact for allocation in pick[0].allocations]

    def _fit(self, fact: ItemFacts, context: _Context, adoptable: bool) -> rank.Fit:
        item = fact.item
        return rank.Fit(
            item_id=item.id,
            remaining=fact.remaining_magnitude,
            due_date=item.due_date,
            organization_id=item.organization_id,
            has_number=self._mentions(context.text, fact),
            has_iban=item.organization_id is not None and item.organization_id in context.ibans,
            has_name=rank.names_match(context.movement.counterparty_name, fact.counterparty_name)
            or rank.name_in_text(context.movement.description, fact.counterparty_name),
            adoptable=adoptable,
        )

    @staticmethod
    def _mentions(text: str, fact: ItemFacts) -> bool:
        if fact.document_number is None:
            return False
        if fact.item.source_kind == SALES_INVOICE:
            year, _, number = fact.document_number.partition("/")
            return rank.invoice_mentioned(text, int(year), int(number))
        return rank.text_mentions(text, fact.document_number)

    def _context(self, movement: BankMovement) -> _Context:
        ibans: frozenset[UUID] = frozenset()
        if movement.counterparty_iban is not None:
            ibans = frozenset(
                self.session.scalars(
                    select(OrganizationIban.organization_id).where(
                        OrganizationIban.iban == movement.counterparty_iban,
                        OrganizationIban.state == "accepted",
                    )
                )
            )
        text = " ".join(
            filter(None, (movement.description, movement.bank_reference, movement.end_to_end_id))
        )
        return _Context(movement, text, ibans)

    # ---- plans ----------------------------------------------------------------------------

    def _plan(
        self,
        movement: BankMovement,
        due_item_ids: Sequence[UUID],
        modules: AbstractSet[str] | None,
    ) -> Plan:
        """The selection a person or an agent named, worked out against the movement. Refuses an
        item that cannot pay it: another legal entity, currency, direction or a module that is
        off, and one that is not open."""
        facts = facts_for_ids(self.session, due_item_ids)
        missing = [item_id for item_id in due_item_ids if item_id not in facts]
        if missing:
            raise NotFound("due_item", missing[0])
        off = kinds_of_modules_off(modules)
        chosen = [facts[item_id] for item_id in due_item_ids]
        for fact in chosen:
            item = fact.item
            if item.legal_entity_id != movement.legal_entity_id:
                raise ValidationFailed(
                    MATCH_ENTITY, "due_item_ids", "la scadenza e' di un'altra azienda"
                )
            if item.currency != movement.currency:
                raise ValidationFailed(
                    MATCH_ENTITY,
                    "due_item_ids",
                    "la scadenza e' in una valuta diversa da quella del movimento",
                )
            if not _fits_direction(movement, item):
                raise ValidationFailed(
                    MATCH_ENTITY,
                    "due_item_ids",
                    "la scadenza va nel verso opposto a quello del movimento",
                )
            if item.source_kind in off:
                raise ValidationFailed(
                    MATCH_ENTITY, "due_item_ids", "la scadenza e' di un modulo spento"
                )
            if item.state == "forecast":
                raise Conflict(MATCH_ENTITY, "una previsione non e' ancora dovuta", id=str(item.id))
            if item.state == "dropped":
                raise Conflict(MATCH_ENTITY, "una scadenza scartata non si salda", id=str(item.id))
        return self._plan_of(movement, chosen)

    def _plan_of(
        self,
        movement: BankMovement,
        facts: Sequence[ItemFacts],
        context: _Context | None = None,
    ) -> Plan:
        """Who takes what of the movement. One item that is already paid, or whose remainder is
        less than the movement, adopts the payment marked by hand for the movement's amount when
        it has one. Otherwise the items, earliest due first, take what they are still owed until
        the movement runs out: the last may be paid in part, and one with nothing left to take
        is refused."""
        context = context or self._context(movement)
        magnitude = context.magnitude
        if len(facts) == 1:
            fact = facts[0]
            if (
                fact.item.state == "settled" or fact.remaining_magnitude < magnitude
            ) and magnitude in fact.unevidenced:
                reasons = rank.selection_reasons(
                    [self._fit(fact, context, True)], magnitude, magnitude
                )
                return Plan((Allocation(fact, magnitude, True),), ZERO, False, reasons)
        allocations: list[Allocation] = []
        left = magnitude
        for fact in sorted(facts, key=_earliest):
            if fact.item.state == "settled" or fact.remaining_magnitude == 0:
                raise Conflict(MATCH_ENTITY, "la scadenza e' gia' saldata", id=str(fact.item.id))
            take = min(fact.remaining_magnitude, left)
            if take == 0:
                raise ValidationFailed(
                    MATCH_ENTITY,
                    "due_item_ids",
                    "il movimento e' gia' coperto dalle altre scadenze indicate",
                )
            allocations.append(Allocation(fact, take, False))
            left -= take
        plan_facts = [a.fact for a in allocations]
        fits = [self._fit(fact, context, False) for fact in plan_facts]
        partial = any(a.amount < a.fact.remaining_magnitude for a in allocations)
        return Plan(
            tuple(allocations),
            left,
            partial,
            rank.selection_reasons(fits, magnitude, magnitude - left),
        )

    def _candidate(self, plan: Plan) -> MatchCandidate:
        return MatchCandidate(
            items=self._plan_items(plan),
            reasons=plan.reasons,  # type: ignore[arg-type]
            allocated=plan.allocated,
            difference=plan.difference,
            partial=plan.partial,
        )

    @staticmethod
    def _plan_items(plan: Plan) -> list[MatchItemRead]:
        return [item_read(a.fact, a.amount, a.adopts) for a in plan.allocations]

    @staticmethod
    def _require_covered(plan: Plan) -> None:
        if plan.difference != 0:
            raise ValidationFailed(
                MATCH_ENTITY,
                "due_item_ids",
                f"le scadenze indicate non coprono il movimento: mancano {plan.difference}",
                expected="scadenze che coprono il movimento per intero",
            )

    # ---- loading --------------------------------------------------------------------------

    def _movement(self, movement_id: UUID, *, lock: bool = False) -> BankMovement:
        movement = self.session.get(BankMovement, movement_id, with_for_update=lock)
        if movement is None:
            raise NotFound(ENTITY, movement_id)
        return movement

    def _open_pool(
        self,
        movement: BankMovement,
        modules: AbstractSet[str] | None,
        *,
        directional: bool = True,
        search: str | None = None,
    ) -> list[ItemFacts]:
        """The open items of the movement's legal entity and currency (and direction), earliest
        due first. Their facts are read in the four queries `facts_for_items` runs."""
        stmt = select(DueItem, remaining_amount()).where(
            DueItem.legal_entity_id == movement.legal_entity_id,
            DueItem.state == "expected",
            DueItem.currency == movement.currency,
            remaining_amount() != 0,
        )
        if directional:
            stmt = stmt.where(_direction_clause(movement))
        if search:
            stmt = stmt.where(_search_clause(search))
        off = kinds_of_modules_off(modules)
        if off:
            stmt = stmt.where(DueItem.source_kind.not_in(off))
        rows = [
            (item, Decimal(remaining))
            for item, remaining in self.session.execute(
                stmt.order_by(DueItem.due_date.asc().nulls_last(), DueItem.id).limit(POOL_MAX)
            )
        ]
        facts = facts_for_items(self.session, rows)
        return sorted(facts.values(), key=_earliest)

    def _adoptable(
        self, movement: BankMovement, modules: AbstractSet[str] | None
    ) -> list[ItemFacts]:
        """The items holding a live payment marked by hand for exactly the movement's amount,
        dated within `ADOPT_WINDOW_DAYS` of its booking: what a movement may attach to rather
        than settle a second time."""
        window = dt.timedelta(days=ADOPT_WINDOW_DAYS)
        stmt = (
            select(DueItem, remaining_amount())
            .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
            .where(
                DueItem.legal_entity_id == movement.legal_entity_id,
                DueItem.state.in_(("expected", "settled")),
                DueItem.currency == movement.currency,
                _direction_clause(movement),
                DueItemSettlement.reversed_at.is_(None),
                DueItemSettlement.bank_movement_id.is_(None),
                func.abs(DueItemSettlement.amount) == abs(movement.amount),
                DueItemSettlement.settled_on.between(
                    movement.booking_date - window, movement.booking_date + window
                ),
            )
        )
        off = kinds_of_modules_off(modules)
        if off:
            stmt = stmt.where(DueItem.source_kind.not_in(off))
        rows = {
            item.id: (item, Decimal(remaining))
            for item, remaining in self.session.execute(stmt.order_by(DueItem.id).limit(POOL_MAX))
        }
        return sorted(facts_for_items(self.session, list(rows.values())).values(), key=_earliest)

    # ---- the match rows -------------------------------------------------------------------

    def _live(self, movement_id: UUID) -> MovementMatch | None:
        return self.session.scalars(
            select(MovementMatch).where(
                MovementMatch.bank_movement_id == movement_id,
                MovementMatch.state.in_(("proposed", "confirmed")),
            )
        ).first()

    def _items(self, match: MovementMatch) -> list[MovementMatchItem]:
        return list(
            self.session.scalars(
                select(MovementMatchItem)
                .where(MovementMatchItem.match_id == match.id)
                .order_by(MovementMatchItem.due_item_id)
            )
        )

    def _item_ids(self, match: MovementMatch) -> set[UUID]:
        return {row.due_item_id for row in self._items(match)}

    def _add_match(
        self,
        movement: BankMovement,
        plan: Plan,
        state: str,
        actor: Actor,
        *,
        note: str | None = None,
    ) -> MovementMatch:
        match = MovementMatch(
            bank_movement_id=movement.id,
            legal_entity_id=movement.legal_entity_id,
            state=state,
            reasons=plan.reasons,
            note=note,
            proposed_by_id=actor.id,
            proposed_by_type=actor.type,
        )
        if state == "confirmed":
            match.confirmed_at = _now()
            match.confirmed_by_id = actor.id
            match.confirmed_by_type = actor.type
        self.session.add(match)
        self.session.flush()
        self._replace_items(match, plan)
        return match

    def _replace_items(self, match: MovementMatch, plan: Plan) -> None:
        for row in self._items(match):
            self.session.delete(row)
        self.session.flush()
        for allocation in plan.allocations:
            self.session.add(
                MovementMatchItem(
                    match_id=match.id,
                    due_item_id=allocation.fact.item.id,
                    legal_entity_id=match.legal_entity_id,
                    amount=allocation.amount,
                )
            )
        self.session.flush()

    def _dismiss_proposal(self, movement: BankMovement, actor: Actor, reason: str) -> None:
        live = self._live(movement.id)
        if live is None or live.state != "proposed":
            return
        live.state = "dismissed"
        live.closed_at = _now()
        live.closed_by_id = actor.id
        live.closed_by_type = actor.type
        live.close_reason = reason
        self.session.flush()

    @staticmethod
    def _require_unmatched(movement: BankMovement) -> None:
        if movement.state != "unmatched":
            raise Conflict(
                ENTITY,
                "il movimento e' gia' stato trattato: annulla prima l'abbinamento",
                id=str(movement.id),
                state=movement.state,
            )

    def _detail(self, movement: BankMovement, modules: AbstractSet[str] | None) -> MovementDetail:
        return MovementDetail(
            movement=movement_reads(self.session, [movement])[0],
            candidates=self._candidates(movement, modules) if movement.state == "unmatched" else [],
            history=match_history(self.session, movement.id),
        )

    def _record_match(
        self,
        kind: str,
        movement: BankMovement,
        match: MovementMatch,
        plan: Plan | None,
        actor: Actor,
    ) -> None:
        """The timeline entry of the movement. It is the last thing before the commit."""
        payload: dict[str, object] = {
            "match_id": str(match.id),
            "bank_account_id": str(movement.bank_account_id),
            "amount": str(movement.amount),
        }
        if plan is not None:
            payload["due_item_ids"] = [str(a.fact.item.id) for a in plan.allocations]
            payload["reasons"] = plan.reasons
        self.activities.record(ENTITY, movement.id, kind, actor, payload)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
