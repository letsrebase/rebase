from collections.abc import Collection
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.db import encode_cursor, today_local
from pigrocrm.core.deals.models import Deal, DealValueLine
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import (
    DEAL_SORTS,
    DealCreate,
    DealDetailRead,
    DealListQuery,
    DealPage,
    DealRead,
    DealUpdate,
    DealValueLineIn,
    DealValueLineRead,
    ValuePreviewIn,
    ValuePreviewRead,
)
from pigrocrm.core.deals.value import (
    ValueSummary,
    ValueTerm,
    line_term,
    summarize,
    term_total,
    terms_overlap,
)
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.money import HOURS_QUANT, MONEY_QUANT
from pigrocrm.core.notes.writer import append_first_note
from pigrocrm.core.organizations.repository import OrganizationRepository
from pigrocrm.core.pipeline.schemas import PipelineStageRead
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.schemas import changed_keys, reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`), matching
# OrganizationService.ENTITY/PersonService.ENTITY exactly: passing a plain `str` into
# `specs_for` fails mypy strict, which requires the narrower
# Literal["organization", "person", "deal"].
ENTITY: EntityType = "deal"


# What `Numeric(12, 2)` holds: a total at or beyond this is refused by name, never left to
# reach the database as an out-of-range error that poisons the session.
VALUE_LIMIT = Decimal(10) ** 10


def _line_label(index: int, line: DealValueLineIn) -> str:
    return f"riga {index + 1}" + (f" ({line.description})" if line.description else "")


def _line_terms(
    lines: list[DealValueLineIn], close_date: date | None
) -> list[tuple[DealValueLineIn, ValueTerm]]:
    """Each line as a term, with the deal's expected close date (or today, when the deal has
    none: the total does not depend on the dates) standing in for a missing start."""
    fallback = close_date or today_local()
    terms: list[tuple[DealValueLineIn, ValueTerm]] = []
    for index, line in enumerate(lines):
        try:
            terms.append((line, line_term(line, fallback)))
        except (ValueError, OverflowError):
            raise ValidationFailed(
                ENTITY,
                "value_lines",
                f"{_line_label(index, line)}: la data è fuori dal calendario",
            ) from None
    return terms


def _check_lines(lines: list[DealValueLineIn], close_date: date | None) -> ValueSummary:
    """Refuses the lines that cannot be priced, naming the line, and answers their figures.

    Beyond the shape each line already has, this is what no single field can say: a flat
    line is a lump sum (quantity 1), a flat one-off line has no window, a day or hour line
    needs a start (its own or the deal's close date), at most one day or hour line is in
    force on any date (a contract prices a worked day by the one rate in force), and no
    line's total nor the deal's total overflows `Numeric(12, 2)`."""
    pairs = _line_terms(lines, close_date)
    for index, (line, _term) in enumerate(pairs):
        label = _line_label(index, line)
        if line.unit == "flat" and line.quantity != 1:
            raise ValidationFailed(
                ENTITY,
                "value_lines",
                f"{label}: una riga a corpo ha sempre quantità 1",
                expected="quantity = 1",
            )
        if line.unit == "flat" and line.delivery_period == "one_off" and line.duration_months:
            raise ValidationFailed(
                ENTITY,
                "value_lines",
                f"{label}: una riga a corpo una tantum non ha durata",
                expected="duration_months nullo",
            )
        if line.unit != "flat" and line.start is None and close_date is None:
            raise ValidationFailed(
                ENTITY,
                "value_lines",
                f"{label}: una tariffa a giorni o a ore ha bisogno di una data di inizio "
                "(la sua, o la data di chiusura prevista del deal)",
                expected="start o expected_close_date",
            )
    rated = [(index, line, term) for index, (line, term) in enumerate(pairs) if line.unit != "flat"]
    for position, (index, line, term) in enumerate(rated):
        for other_index, other, other_term in rated[position + 1 :]:
            if terms_overlap(term, other_term):
                raise ValidationFailed(
                    ENTITY,
                    "value_lines",
                    f"{_line_label(other_index, other)} si sovrappone a "
                    f"{_line_label(index, line)}: un deal ha al più una tariffa a giorni o a "
                    "ore in vigore in ogni data (due persone a tariffe diverse sono due deal)",
                    expected="tariffe a giorni o a ore non sovrapposte",
                )
    for index, (line, term) in enumerate(pairs):
        try:
            line_total = term_total(term)
        except (ValueError, OverflowError):
            # An occurrence stepped past year 9999.
            raise ValidationFailed(
                ENTITY,
                "value_lines",
                f"{_line_label(index, line)}: la data è fuori dal calendario",
            ) from None
        if line_total >= VALUE_LIMIT:
            raise ValidationFailed(
                ENTITY,
                "value_lines",
                f"{_line_label(index, line)}: il totale della riga è troppo grande",
                expected=f"meno di {VALUE_LIMIT}",
            )
    summary = summarize(term for _line, term in pairs)
    if summary.total >= VALUE_LIMIT:
        raise ValidationFailed(
            ENTITY,
            "value_lines",
            "il totale del deal è troppo grande",
            expected=f"meno di {VALUE_LIMIT}",
        )
    return summary


def _line_key(line: DealValueLineIn | DealValueLine) -> tuple[object, ...]:
    return (
        line.description,
        line.unit,
        line.delivery_period,
        line.unit_price,
        line.quantity,
        line.start,
        line.duration_months,
    )


def _check_numbers(values: dict[str, Any]) -> None:
    """Shared by `create` and `update`: `probability` must be a percentage, and none
    of the money/hours fields can be negative. Values arriving here are already
    Decimal/int (or None) courtesy of the Pydantic schemas -- this only checks range,
    it does not coerce or normalize anything, unlike `_check_fiscal`/`_check_email`
    in the Organization/Person services."""
    probability = values.get("probability")
    if probability is not None and not 0 <= probability <= 100:
        raise ValidationFailed(ENTITY, "probability", "fuori intervallo", expected="0-100")
    for field in ("budgeted_value", "budgeted_hours", "hourly_rate"):
        value = values.get(field)
        if value is not None and Decimal(value) < 0:
            raise ValidationFailed(ENTITY, field, "non può essere negativo", expected=">= 0")


def _settle_probability(stage: PipelineStageRead, probability: int | None) -> int:
    """The single authority on what a deal's `probability` becomes once its stage is
    known -- shared by `create`, `move_stage`, and `update` so "won at 60%" cannot be
    reached through any one of them individually. Before this function existed, only
    `move_stage` settled the probability on a terminal transition, and the class
    docstring's own claim ("'Won at 60%' is not a state a deal can be left in") was
    false in two different ways the tests never caught: `create()` with an explicit
    `pipeline_stage_id` on a terminal stage and a contradicting `probability` kept
    that value verbatim, and an ordinary `update(probability=...)` after a
    `move_stage()` had already settled it to 100/0 could walk it back down with no
    check at all.

    A terminal stage's `type` overrides any candidate value outright, the same way
    `move_stage` already did before this fix -- silently, not by raising. Overriding
    was kept rather than switched to rejecting an inconsistent explicit value: the
    override already had a passing, documented precedent
    (`test_moving_to_a_won_stage_sets_probability_to_one_hundred`) that a reject-based
    design would have had to invalidate, and consistency across all three write paths
    mattered more than which of the two reasonable designs was picked. A non-terminal
    stage keeps the candidate if one was supplied, or falls back to the stage's own
    `default_probability` -- unchanged from `create`'s original default-from-stage
    behavior."""
    if stage.type == "won":
        return 100
    if stage.type == "lost":
        return 0
    return probability if probability is not None else stage.default_probability


def _settle_closure_date(
    deal: Deal, previous: PipelineStageRead, target: PipelineStageRead
) -> None:
    """The single authority on `deals.closed_at`, at module level for the same reason
    `_settle_probability` is: `AutomationRunner` reaches it through
    `set_stage_in_transaction` (slice 6 §9.3), and an invariant reachable through two
    paths must live in one function or it holds on one of them.

    `today_local()` and never `date.today()`: at 00:30 on 1 April in Rome it is still
    31 March in UTC, and a deal won just after midnight would land in the previous
    month's conversion rate.

    The three cases are deliberately asymmetric. `target.type` is tested first, so a
    reopening clears the stamp whatever the deal came from; `previous.type` then decides
    whether entering a terminal stage is a closure at all.
    """
    if target.type == "open":
        # Reopened. A reopened deal is not a deal closed in March, and leaving the stamp
        # would put it in the conversion rate and in the open pipeline at once.
        deal.closed_at = None
        return
    if previous.type != "open":
        # won -> lost or lost -> won: a correction, not a closure.
        return
    deal.closed_at = today_local()


class DealService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = DealRepository(session)
        self.organizations = OrganizationRepository(session)
        self.users = UserRepository(session)
        self.pipeline = PipelineService(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    def _owner_for(self, owner_id: UUID | None, actor: Actor, *, creating: bool) -> UUID | None:
        """The owner a write leaves on the deal (REB-780).

        Creating: whoever the request names, and otherwise whoever creates it. The actor
        of a job or of rebase has no user id, so such a deal has no owner, which the
        reminders give to the space's owner. Anybody who may write a deal may hand it to
        somebody else (unlike a customer's owner, which is an admin's: a deal is one
        person's work, and the member who opened it is the one who knows who runs it).
        The person must be an active user: a dangling id is worse than none, and a
        deactivated one would be told nothing.

        `None` on an update clears the owner, and is the only way to: on a create it
        means «not said», which is the default above.
        """
        if owner_id is None:
            return actor.id if creating else None
        owner = self.users.get(owner_id)
        if owner is None:
            raise NotFound("user", owner_id)
        if not owner.active:
            raise ValidationFailed(
                ENTITY,
                "owner_id",
                "la persona e' disattivata: un deal si assegna a un utente attivo",
                expected="l'id di un utente attivo",
            )
        return owner.id

    def _read(self, deal: Deal) -> DealRead:
        """One deal's read shape, customer name included.

        Every read path in this class goes through here rather than calling
        `DealRead.model_validate` directly, so a deal's customer cannot depend on which
        method the caller happened to use -- `create`, `update`, `move_stage`, `restore`,
        `get` and `list` all return the same shape. Mirrors `PersonService._read`.
        """
        return self._reads([deal])[0]

    def _reads(
        self, deals: list[Deal], lines: dict[UUID, list[DealValueLine]] | None = None
    ) -> list[DealRead]:
        """The batched form, and the reason `customer_legal_name` is resolved here
        and not inside `DealRead` itself: one lookup for the whole page (see
        `DealRepository.customer_names`), so a page of deals costs two queries rather
        than one per row. A schema-level validator or a lazy ORM relationship would both
        put the lookup on the row, which is exactly the N+1 this avoids.

        `model_copy` and not a second `model_validate`: the name is not an attribute of
        `Deal` at all, so there is nothing on the ORM object for `from_attributes` to
        read -- and the value comes from a `String` column, already the right type.
        """
        names = self.repo.customer_names({deal.customer_id for deal in deals})
        if lines is None:
            lines = self.repo.value_lines({deal.id for deal in deals})
        reads: list[DealRead] = []
        for deal in deals:
            figures = self._figures(deal, lines.get(deal.id, []))
            reads.append(
                DealRead.model_validate(deal).model_copy(
                    update={"customer_legal_name": names.get(deal.customer_id), **figures}
                )
            )
        return reads

    @staticmethod
    def _figures(deal: Deal, lines: list[DealValueLine]) -> dict[str, Decimal | None]:
        """The one-off and the monthly figure of a deal's lines, `None` for both when it has
        none (the same «senza valore» it always was)."""
        if not lines:
            return {"one_off_value": None, "monthly_recurring_value": None}
        fallback = deal.expected_close_date or today_local()
        summary = summarize(line_term(line, fallback) for line in lines)
        return {
            "one_off_value": summary.one_off,
            "monthly_recurring_value": summary.monthly_recurring,
        }

    def _detail(self, deal: Deal, lines: list[DealValueLine] | None = None) -> DealDetailRead:
        """The single-deal read: the list shape plus the lines behind its figures. `lines`
        are the ones the caller already holds; without them they are read."""
        if lines is None:
            lines = self.repo.value_lines([deal.id]).get(deal.id, [])
        read = self._reads([deal], {deal.id: lines})[0]
        contract = self.repo.linked_contract(deal.id)
        return DealDetailRead(
            **read.model_dump(),
            value_lines=[DealValueLineRead.model_validate(line) for line in lines],
            contract_id=None if contract is None else contract[0],
        )

    def preview_value(self, data: ValuePreviewIn) -> ValuePreviewRead:
        """The figures a list of lines would give, validated as a write would be, and
        written nowhere: what a form shows under the lines as they are typed."""
        if not data.value_lines:
            return ValuePreviewRead(
                expected_value=None, one_off_value=None, monthly_recurring_value=None
            )
        summary = _check_lines(data.value_lines, data.expected_close_date)
        return ValuePreviewRead(
            expected_value=summary.total,
            one_off_value=summary.one_off,
            monthly_recurring_value=summary.monthly_recurring,
        )

    def _write_lines(
        self, deal: Deal, lines: list[DealValueLineIn], summary: ValueSummary
    ) -> list[DealValueLine]:
        """Replaces the deal's lines and writes its total, in the caller's transaction;
        answers the rows written."""
        rows = [
            DealValueLine(
                deal_id=deal.id,
                position=position,
                **{
                    **line.model_dump(),
                    # At the column's scale, as the database will answer them: these rows
                    # are read back without a round trip.
                    "unit_price": line.unit_price.quantize(MONEY_QUANT),
                    "quantity": line.quantity.quantize(HOURS_QUANT),
                },
            )
            for position, line in enumerate(lines)
        ]
        self.repo.replace_value_lines(deal.id, rows)
        deal.expected_value = summary.total if lines else None
        return rows

    def create(
        self, data: DealCreate, actor: Actor, *, rebase_ref: str | None = None
    ) -> DealDetailRead:
        """A new deal. `rebase_ref` is for the engagements door alone (the leads route and
        the letters): the reference it finds this deal again by, which no schema carries,
        since a caller who could set it could claim a request that was never made."""
        actor.require_write("create_deal")
        payload = data.model_dump()
        # Not a column: the first note of the deal, written below by the creating actor.
        first_note = payload.pop("note", None)
        payload.pop("value_lines")
        lines = data.value_lines or []
        # Refused before anything is written, so a bad line leaves no deal behind.
        summary = _check_lines(lines, data.expected_close_date)
        _check_numbers(payload)

        # A deal without a customer has no economic meaning -- see Deal's own
        # docstring. Unlike Person.customer_id (optional), this is never skipped.
        customer = self.organizations.get(payload["customer_id"])
        if customer is None:
            raise NotFound("organization", payload["customer_id"])
        payload["owner_id"] = self._owner_for(payload.get("owner_id"), actor, creating=True)
        # The customer's legal entity at this moment, kept from now on (REB-623, spec §1.7);
        # refused when that legal entity is deactivated, since nothing new is born on one.
        payload["legal_entity_id"] = LegalEntityService(self.session).inherited(
            customer.legal_entity_id, ENTITY
        )

        # `PipelineService.get` raises NotFound for a stage id that does not resolve
        # to a live row; `default_stage()` raises ValidationFailed if no `open` stage
        # is configured at all. Neither is reimplemented here.
        stage = (
            self.pipeline.get(payload["pipeline_stage_id"])
            if payload.get("pipeline_stage_id")
            else self.pipeline.default_stage()
        )
        payload["pipeline_stage_id"] = stage.id
        payload["probability"] = _settle_probability(stage, payload.get("probability"))
        payload["custom_fields"] = validate_custom_fields(
            ENTITY, self.fields.specs_for(ENTITY), payload.get("custom_fields") or {}
        )
        payload["rebase_refs"] = [rebase_ref] if rebase_ref is not None else []

        deal = self.repo.add(Deal(**payload))
        rows = self._write_lines(deal, lines, summary) if lines else []
        self.activities.record(
            ENTITY, deal.id, "created", actor, {"name": deal.name, "stage": stage.name}
        )
        append_first_note(self.session, ENTITY, deal.id, first_note, actor)
        self.session.commit()
        return self._detail(deal, rows)

    def attach_rebase_ref(self, deal_id: UUID, rebase_ref: str, actor: Actor) -> DealRead:
        """Adds a reference of the engagements door to a deal the CRM already had (the leads
        route linking a request to it), once: the same one again changes nothing. The
        door's own call, like `create`'s `rebase_ref`."""
        actor.require_write("update_deal")
        deal = self.repo.get(deal_id)
        if deal is None:
            raise NotFound(ENTITY, deal_id)
        if rebase_ref not in deal.rebase_refs:
            deal.rebase_refs = [*deal.rebase_refs, rebase_ref]
            self.activities.record(ENTITY, deal.id, "updated", actor, {"changed": ["rebase_refs"]})
        self.session.commit()
        return self._read(deal)

    def update(self, deal_id: UUID, data: DealUpdate, actor: Actor) -> DealDetailRead:
        actor.require_write("update_deal")
        # Locked, so a conversion into a contract running at the same time either sees the
        # lines this call writes or is seen by it, never neither.
        deal = self.repo.get_locked(deal_id)
        if deal is None:
            raise NotFound(ENTITY, deal_id)

        # custom_fields is handled separately from the rest of the payload, reading
        # `data.custom_fields` directly rather than through `model_dump`: this method
        # must see a caller-supplied `None` *inside* the dict (e.g. {"fonte": None},
        # meaning "remove this key") exactly as given, with no risk of it being
        # confused with the field itself being absent -- see `merge_custom`.
        changes = supplied_changes(data, exclude={"custom_fields", "value_lines"})
        reject_cleared_columns(ENTITY, Deal, changes)
        _check_numbers(changes)
        # The lines are written whole or not at all; an unchanged list is not a change.
        close_date = changes.get("expected_close_date", deal.expected_close_date)
        stored = self.repo.value_lines([deal.id]).get(deal.id, [])
        new_lines = (data.value_lines or []) if "value_lines" in data.model_fields_set else None
        lines_changed = new_lines is not None and [_line_key(line) for line in new_lines] != [
            _line_key(line) for line in stored
        ]
        summary: ValueSummary | None = None
        contract = self.repo.linked_contract(deal.id)
        if lines_changed and contract is not None:
            # The cards of the contract are the price from the conversion on: two sources
            # of it would drift apart.
            raise Conflict(
                ENTITY,
                f"il deal è diventato il contratto «{contract[1]}»: le righe di valore non si "
                "modificano più, il prezzo è quello del contratto",
                contract_id=str(contract[0]),
            )
        if new_lines is not None:
            summary = _check_lines(new_lines, close_date)
        elif stored and close_date != deal.expected_close_date and contract is None:
            # A line with no start moves with the close date, so the rule that two day or
            # hour lines never overlap is checked again.
            _check_lines(
                [DealValueLineIn.model_validate(line, from_attributes=True) for line in stored],
                close_date,
            )
        # Only a change is checked: re-sending the owner a deal already has must not be
        # refused the day that person is deactivated. An explicit `null` clears it.
        if "owner_id" in changes:
            if changes["owner_id"] == deal.owner_id:
                del changes["owner_id"]
            else:
                changes["owner_id"] = self._owner_for(changes["owner_id"], actor, creating=False)
        if "probability" in changes:
            # `update` never changes `deal.pipeline_stage_id` -- that is
            # `move_stage`'s job alone -- so the deal's *current* stage is what
            # settling must be checked against here. Without this, an ordinary
            # `update(probability=60)` on a deal a previous `move_stage` had already
            # settled to 100/0 could walk it back down with no check at all -- the
            # third of the three gates "won at 60%" needed closed.
            current_stage = self.pipeline.get(deal.pipeline_stage_id)
            changes["probability"] = _settle_probability(current_stage, changes["probability"])
        if data.custom_fields is not None:
            changes["custom_fields"] = merge_custom(
                ENTITY, self.fields.specs_for(ENTITY), deal.custom_fields, data.custom_fields
            )
        changed = changed_keys(deal, changes)
        for key, value in changes.items():
            setattr(deal, key, value)
        if lines_changed and new_lines is not None and summary is not None:
            stored = self._write_lines(deal, new_lines, summary)

        changed = sorted([*changed, *(["value_lines"] if lines_changed else [])])
        self.activities.record(ENTITY, deal.id, "updated", actor, {"changed": changed})
        self.session.commit()
        return self._detail(deal, stored)

    def move_stage(self, deal_id: UUID, stage_id: UUID, actor: Actor) -> DealRead:
        """The only supported way to change a deal's stage -- see `DealUpdate`'s own
        docstring for why it is not also a plain field on `update`. `_settle_probability`
        -- also used by `create` and `update` -- is what actually keeps "won at 60%"
        unreachable through *any* of the three; this method no longer settles the
        probability by itself.

        Since slice 6 it also maintains `closed_at`, and the three cases are not
        symmetric: entering a terminal stage from an open one stamps today, returning to
        an open stage clears it, and moving between two terminal stages leaves it alone --
        that is a correction of *which* outcome, not a new closure, and restamping would
        move the deal into the month somebody fixed the mistake in.
        """
        actor.require_write("move_deal")
        deal = self.repo.get(deal_id)
        if deal is None:
            raise NotFound(ENTITY, deal_id)

        target = self.pipeline.get(stage_id)
        previous = self.pipeline.get(deal.pipeline_stage_id)

        deal.pipeline_stage_id = target.id
        deal.probability = _settle_probability(target, deal.probability)
        _settle_closure_date(deal, previous, target)

        self.activities.record(
            ENTITY, deal.id, "stage_changed", actor, {"from": previous.name, "to": target.name}
        )
        self.session.commit()
        return self._read(deal)

    def set_stage_in_transaction(self, deal: Deal, stage: PipelineStageRead) -> None:
        """Move a deal to a stage, and nothing else. Slice 6 §9.3's convention.

        Mutates. Does **not** record an activity, does **not** commit, and does **not**
        check authorisation. Callable only from `core/automations/`, and
        `packages/core/tests/test_in_transaction_callers.py` enforces that on the AST of
        every call site in the repository.

        Why it exists at all: `AutomationRunner` runs inside its trigger's transaction
        (`DocumentService.set_offer_state`), and `move_stage` commits and records. Calling
        `move_stage` from the runner would commit the offer's state change before the
        trigger had finished -- destroying the atomicity that is the whole answer to "what
        happens if an automation fails halfway" -- and would write a second timeline entry
        for a single movement.

        Why it does not authorise: the trigger already did (`set_offer_state` calls
        `actor.require_write`), so a `readonly` actor never reaches the runner. Adding a
        check here would be harmless; *elevating* here would turn accepting an offer into a
        way to write to a deal the actor could not otherwise touch. It takes no `actor`
        parameter at all, so there is nothing to elevate with.

        `_settle_probability` and `_settle_closure_date` are reused rather than
        reimplemented, and that is the only reason both are module-level functions: "won at
        60%" and "closed in the wrong month" must stay unreachable through this path too,
        and an invariant reachable through two paths has to live in one place.

        The previous stage is read **before** the assignment, and the order is
        load-bearing: `_settle_closure_date` decides on `previous.type`, so looking it up
        afterwards would hand it the target as the previous stage and every move would take
        the "correction between two terminal stages" branch, which stamps nothing at all.
        """
        previous = self.pipeline.get(deal.pipeline_stage_id)
        deal.pipeline_stage_id = stage.id
        deal.probability = _settle_probability(stage, deal.probability)
        _settle_closure_date(deal, previous, stage)

    def get(self, deal_id: UUID, actor: Actor) -> DealDetailRead:
        deal = self.repo.get(deal_id)
        if deal is None:
            raise NotFound(ENTITY, deal_id)
        return self._detail(deal)

    def get_many(self, deal_ids: Collection[UUID], actor: Actor) -> dict[UUID, DealRead]:
        """The live deals among `deal_ids` by id, two queries whatever their number. An id
        with no live deal is absent from the answer, where `get` raises."""
        del actor
        deals = self.repo.get_many(deal_ids)
        return {deal.id: read for deal, read in zip(deals, self._reads(deals), strict=True)}

    def soft_delete(self, deal_id: UUID, actor: Actor) -> None:
        """Sets deleted_at. No physical delete exists in this slice: a misread
        instruction from an agent must be reversible.

        Refuses while the deal still carries live hours, exactly as
        `OrganizationService.soft_delete` refuses while a customer still has active deals. The
        invariant is the same one level down -- **no live child hangs off an archived
        parent** -- and it is what keeps slice 6 criterion 2 true for every figure computed
        over `time_entries`.

        Without it the operational dashboard contradicted itself: `unbilled_backlog` sums
        `time_entries` and joins nothing, while `count_won_deals_to_invoice` joins `deals`
        and filters `Deal.deleted_at IS NULL`, so archiving a deal with a billable unbilled
        entry left valore_maturato standing against a signal card reading zero, with no row
        anywhere to reconcile them.

        Fixed here rather than in the aggregates on purpose. A `Deal` semi-join added to
        `unbilled_backlog` would fix that one pair and break another -- `week_hours` and
        `TimeEntryRepository.list` agree today precisely because neither joins `deals` --
        and adding the join to the lists too would make an archived deal's hours
        unlistable, which is not what a soft delete is for. One guard at the write makes
        every read consistent without any of them changing.

        `TimeEntryService.restore` carries the mirror of this check, for the reason
        `DealService.restore` gives about its own: an invariant enforced on only one side
        has a back door, and the back door here is archive-the-hours, archive-the-deal,
        restore-the-hours.
        """
        actor.require_write("delete_deal")
        deal = self.repo.get(deal_id)
        if deal is None:
            raise NotFound(ENTITY, deal_id)

        active_hours = self.repo.count_active_time_entries(deal_id)
        if active_hours:
            raise Conflict(
                ENTITY,
                "il deal ha ore registrate: fatturale o archiviale prima",
                active_hours=active_hours,
            )

        deal.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, deal.id, "deleted", actor)
        self.session.commit()

    def restore(self, deal_id: UUID, actor: Actor) -> DealRead:
        """Refuses when the deal's customer is archived: `soft_delete` on a
        customer refuses while it has active deals precisely to rule out an
        active deal sitting on an archived customer, but that invariant has a
        back door if `restore` does not check it too -- archive the deal,
        archive the now deal-free customer, restore the deal, and the exact
        state `soft_delete` exists to prevent is back. `GET /api/deals` would
        list it, and the MCP resource `deal://{id}` would then fail to render
        because it cannot load the customer. Checked unconditionally, not only
        when the deal itself was actually archived: the invariant is about the
        deal's *current* state after this call, not about what changed."""
        actor.require_write("restore_deal")
        deal = self.repo.get(deal_id, include_deleted=True)
        if deal is None:
            raise NotFound(ENTITY, deal_id)

        # Past the row-level policies (REB-634): the customer may sit in a legal entity the
        # deal's reader cannot see, and «not found» must not read as «not archived».
        archived = self.session.execute(
            text("SELECT organization_archived(:customer_id)"), {"customer_id": deal.customer_id}
        ).scalar_one_or_none()
        if archived is None:
            raise NotFound("organization", deal.customer_id)
        if archived:
            raise Conflict(
                ENTITY,
                "il cliente è archiviato: ripristina prima il cliente",
                customer_id=str(deal.customer_id),
            )

        # Recorded only when the deal really was deleted: unconditionally logging
        # "restored" here -- even for a deal that was never soft-deleted -- would
        # write a timeline entry claiming a recovery that never happened. Mirrors the
        # identical guard on OrganizationService.restore/PersonService.restore.
        was_deleted = deal.deleted_at is not None
        deal.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, deal.id, "restored", actor)
        self.session.commit()
        return self._read(deal)

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (see `FieldDefinitionService.specs_for`'s docstring and
    # `OrganizationService`/`PersonService`'s own ordering): defining a method named
    # `list` rebinds that name in the *class* namespace, so any later method whose
    # own return annotation is a bare `list[...]` would resolve `list` to this method
    # instead of the builtin and fail at import time. No method in this class has
    # that shape today, but the rule has no "only when it would currently break"
    # exception.
    def list(self, query: DealListQuery, actor: Actor) -> DealPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # See `OrganizationService.list` for why the cursor carries the sort value as well
        # as the id.
        spec = DEAL_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return DealPage(
            items=self._reads(items),
            next_cursor=next_cursor,
        )
