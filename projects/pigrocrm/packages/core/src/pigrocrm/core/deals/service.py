from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.db import encode_cursor, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import (
    DEAL_SORTS,
    DealCreate,
    DealListQuery,
    DealPage,
    DealRead,
    DealUpdate,
)
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.pipeline.schemas import PipelineStageRead
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`), matching
# CustomerService.ENTITY/PersonService.ENTITY exactly: passing a plain `str` into
# `specs_for` fails mypy strict, which requires the narrower
# Literal["customer", "person", "deal"].
ENTITY: EntityType = "deal"


def _check_numbers(values: dict[str, Any]) -> None:
    """Shared by `create` and `update`: `probabilita` must be a percentage, and none
    of the money/hours fields can be negative. Values arriving here are already
    Decimal/int (or None) courtesy of the Pydantic schemas -- this only checks range,
    it does not coerce or normalize anything, unlike `_check_fiscal`/`_check_email`
    in the Customer/Person services."""
    probabilita = values.get("probabilita")
    if probabilita is not None and not 0 <= probabilita <= 100:
        raise ValidationFailed(ENTITY, "probabilita", "fuori intervallo", expected="0-100")
    for field in ("valore_previsto", "valore_preventivato", "ore_preventivate", "tariffa_oraria"):
        value = values.get(field)
        if value is not None and Decimal(value) < 0:
            raise ValidationFailed(ENTITY, field, "non può essere negativo", expected=">= 0")


def _settle_probability(stage: PipelineStageRead, probabilita: int | None) -> int:
    """The single authority on what a deal's `probabilita` becomes once its stage is
    known -- shared by `create`, `move_stage`, and `update` so "won at 60%" cannot be
    reached through any one of them individually. Before this function existed, only
    `move_stage` settled the probability on a terminal transition, and the class
    docstring's own claim ("'Won at 60%' is not a state a deal can be left in") was
    false in two different ways the tests never caught: `create()` with an explicit
    `pipeline_stage_id` on a terminal stage and a contradicting `probabilita` kept
    that value verbatim, and an ordinary `update(probabilita=...)` after a
    `move_stage()` had already settled it to 100/0 could walk it back down with no
    check at all.

    A terminal stage's `tipo` overrides any candidate value outright, the same way
    `move_stage` already did before this fix -- silently, not by raising. Overriding
    was kept rather than switched to rejecting an inconsistent explicit value: the
    override already had a passing, documented precedent
    (`test_moving_to_a_won_stage_sets_probability_to_one_hundred`) that a reject-based
    design would have had to invalidate, and consistency across all three write paths
    mattered more than which of the two reasonable designs was picked. A non-terminal
    stage keeps the candidate if one was supplied, or falls back to the stage's own
    `probabilita_default` -- unchanged from `create`'s original default-from-stage
    behavior."""
    if stage.tipo == "won":
        return 100
    if stage.tipo == "lost":
        return 0
    return probabilita if probabilita is not None else stage.probabilita_default


def _settle_closure_date(
    deal: Deal, previous: PipelineStageRead, target: PipelineStageRead
) -> None:
    """The single authority on `deals.chiuso_il`, at module level for the same reason
    `_settle_probability` is: `AutomationRunner` reaches it through
    `set_stage_in_transaction` (slice 6 §9.3), and an invariant reachable through two
    paths must live in one function or it holds on one of them.

    `today_local()` and never `date.today()`: at 00:30 on 1 April in Rome it is still
    31 March in UTC, and a deal won just after midnight would land in the previous
    month's conversion rate.

    The three cases are deliberately asymmetric. `target.tipo` is tested first, so a
    reopening clears the stamp whatever the deal came from; `previous.tipo` then decides
    whether entering a terminal stage is a closure at all.
    """
    if target.tipo == "open":
        # Reopened. A reopened deal is not a deal closed in March, and leaving the stamp
        # would put it in the conversion rate and in the open pipeline at once.
        deal.chiuso_il = None
        return
    if previous.tipo != "open":
        # won -> lost or lost -> won: a correction, not a closure.
        return
    deal.chiuso_il = today_local()


class DealService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = DealRepository(session)
        self.customers = CustomerRepository(session)
        self.users = UserRepository(session)
        self.pipeline = PipelineService(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    def _check_owner(self, owner_id: UUID | None) -> None:
        """`owner_id` is nullable -- see `Deal`'s own docstring: a deal may have no
        assigned owner. A supplied id that does not resolve to a real user is
        rejected: a dangling FK is worse than no FK. Mirrors
        `PersonService._check_customer` exactly, on the one real foreign key this
        class never validated -- `deals.owner_id` -- confirmed reachable before this
        fix by constructing a `Deal` with a random UUID and watching a raw, uncaught
        `sqlalchemy.exc.IntegrityError` (`ForeignKeyViolation`) come back from
        `flush()`/`commit()` instead of this project's own `NotFound`."""
        if owner_id is not None and self.users.get(owner_id) is None:
            raise NotFound("user", owner_id)

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        """Used by `create` only: `values` is the *complete* desired set of custom
        fields for a brand-new row, so it is validated against every active
        definition -- a required-but-absent field is genuinely missing here, not
        merely untouched, unlike on a partial `update` (see `_update_custom_fields`).
        Mirrors `CustomerService._validated_custom`/`PersonService._validated_custom`
        exactly."""
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _update_custom_fields(self, deal: Deal, provided: dict[str, Any]) -> dict[str, Any]:
        """Copies `CustomerService._update_custom_fields`'s contract exactly (see that
        method's docstring in customers/service.py for the full reasoning): validates
        only the keys the caller is touching, against active definitions -- never the
        union with what is already stored on `deal`. Validating the union would
        contradict Task 7's contract for archiving a field ("hide it, keep the data
        readable"): an archived key still present in storage would fail the "campo non
        definito" check on every future update, even one that never mentions that key.

        A key supplied with `None` removes that entry from the stored dict, unless the
        key currently belongs to an active, `required=True` definition -- in which case
        it raises the same "campo obbligatorio" `ValidationFailed` that
        `validate_custom_fields` raises for `""`. `None` and `""` are two spellings of
        "this field has no value"; on a *required*, currently active field the two must
        be rejected identically, or a caller strips the value just by choosing the
        other spelling. Archived, undefined, or non-required keys keep allowing removal
        via `None` even if the definition was required back when it was active --
        clearing an archived field's stored value must stay possible.

        A key supplied with any other (non-blank) value must belong to a currently
        active definition. Keys already stored that `provided` does not mention --
        archived or not, required or not -- are carried over untouched.
        """
        active_by_key = {spec.key: spec for spec in self.fields.specs_for(ENTITY)}

        to_remove: set[str] = set()
        for key, value in provided.items():
            if value is not None:
                continue
            spec = active_by_key.get(key)
            if spec is not None and spec.required:
                raise ValidationFailed(
                    ENTITY, key, "campo obbligatorio", expected="un valore non vuoto"
                )
            to_remove.add(key)

        to_set = {key: value for key, value in provided.items() if value is not None}
        touched_specs = [spec for spec in active_by_key.values() if spec.key in to_set]
        validated = validate_custom_fields(ENTITY, touched_specs, to_set)

        merged = {k: v for k, v in deal.custom_fields.items() if k not in to_remove}
        merged.update(validated)
        return merged

    def _read(self, deal: Deal) -> DealRead:
        """One deal's read shape, customer name included.

        Every read path in this class goes through here rather than calling
        `DealRead.model_validate` directly, so a deal's customer cannot depend on which
        method the caller happened to use -- `create`, `update`, `move_stage`, `restore`,
        `get` and `list` all return the same shape. Mirrors `PersonService._read`.
        """
        return self._reads([deal])[0]

    def _reads(self, deals: list[Deal]) -> list[DealRead]:
        """The batched form, and the reason `customer_ragione_sociale` is resolved here
        and not inside `DealRead` itself: one lookup for the whole page (see
        `DealRepository.customer_names`), so a page of deals costs two queries rather
        than one per row. A schema-level validator or a lazy ORM relationship would both
        put the lookup on the row, which is exactly the N+1 this avoids.

        `model_copy` and not a second `model_validate`: the name is not an attribute of
        `Deal` at all, so there is nothing on the ORM object for `from_attributes` to
        read -- and the value comes from a `String` column, already the right type.
        """
        names = self.repo.customer_names({deal.customer_id for deal in deals})
        return [
            DealRead.model_validate(deal).model_copy(
                update={"customer_ragione_sociale": names.get(deal.customer_id)}
            )
            for deal in deals
        ]

    def create(self, data: DealCreate, actor: Actor) -> DealRead:
        actor.require_write("create_deal")
        payload = data.model_dump()
        _check_numbers(payload)

        # A deal without a customer has no economic meaning -- see Deal's own
        # docstring. Unlike Person.customer_id (optional), this is never skipped.
        customer = self.customers.get(payload["customer_id"])
        if customer is None:
            raise NotFound("customer", payload["customer_id"])
        self._check_owner(payload.get("owner_id"))
        # The customer's azienda at this moment, kept from now on (REB-623, spec §1.7);
        # refused when that azienda is deactivated, since nothing new is born on one.
        payload["azienda_id"] = LegalEntityService(self.session).inherited(
            customer.azienda_id, ENTITY
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
        payload["probabilita"] = _settle_probability(stage, payload.get("probabilita"))
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})

        deal = self.repo.add(Deal(**payload))
        self.activities.record(
            ENTITY, deal.id, "created", actor, {"nome": deal.nome, "stage": stage.nome}
        )
        self.session.commit()
        return self._read(deal)

    def update(self, deal_id: UUID, data: DealUpdate, actor: Actor) -> DealRead:
        actor.require_write("update_deal")
        deal = self.repo.get(deal_id)
        if deal is None:
            raise NotFound(ENTITY, deal_id)

        # custom_fields is handled separately from the rest of the payload, reading
        # `data.custom_fields` directly rather than through `model_dump`: this method
        # must see a caller-supplied `None` *inside* the dict (e.g. {"fonte": None},
        # meaning "remove this key") exactly as given, with no risk of it being
        # confused with the field itself being absent -- see `_update_custom_fields`.
        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Deal, changes)
        _check_numbers(changes)
        # `is not None` on the value, not `in changes` on the key: with `exclude_unset`
        # an explicit `owner_id: null` now *reaches* this branch, and sending `None` to
        # the repository would raise `NotFound("user", None)` for what is actually a
        # valid instruction ("clear the owner"). `_check_owner` already skips a `None`,
        # so the guard is here only to make that reading explicit at the call site.
        if changes.get("owner_id") is not None:
            self._check_owner(changes["owner_id"])
        if "probabilita" in changes:
            # `update` never changes `deal.pipeline_stage_id` -- that is
            # `move_stage`'s job alone -- so the deal's *current* stage is what
            # settling must be checked against here. Without this, an ordinary
            # `update(probabilita=60)` on a deal a previous `move_stage` had already
            # settled to 100/0 could walk it back down with no check at all -- the
            # third of the three gates "won at 60%" needed closed.
            current_stage = self.pipeline.get(deal.pipeline_stage_id)
            changes["probabilita"] = _settle_probability(current_stage, changes["probabilita"])
        if data.custom_fields is not None:
            changes["custom_fields"] = self._update_custom_fields(deal, data.custom_fields)
        for key, value in changes.items():
            setattr(deal, key, value)

        self.activities.record(ENTITY, deal.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return self._read(deal)

    def move_stage(self, deal_id: UUID, stage_id: UUID, actor: Actor) -> DealRead:
        """The only supported way to change a deal's stage -- see `DealUpdate`'s own
        docstring for why it is not also a plain field on `update`. `_settle_probability`
        -- also used by `create` and `update` -- is what actually keeps "won at 60%"
        unreachable through *any* of the three; this method no longer settles the
        probability by itself.

        Since slice 6 it also maintains `chiuso_il`, and the three cases are not
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
        deal.probabilita = _settle_probability(target, deal.probabilita)
        _settle_closure_date(deal, previous, target)

        self.activities.record(
            ENTITY, deal.id, "stage_changed", actor, {"from": previous.nome, "to": target.nome}
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
        load-bearing: `_settle_closure_date` decides on `previous.tipo`, so looking it up
        afterwards would hand it the target as the previous stage and every move would take
        the "correction between two terminal stages" branch, which stamps nothing at all.
        """
        previous = self.pipeline.get(deal.pipeline_stage_id)
        deal.pipeline_stage_id = stage.id
        deal.probabilita = _settle_probability(stage, deal.probabilita)
        _settle_closure_date(deal, previous, stage)

    def get(self, deal_id: UUID, actor: Actor) -> DealRead:
        deal = self.repo.get(deal_id)
        if deal is None:
            raise NotFound(ENTITY, deal_id)
        return self._read(deal)

    def soft_delete(self, deal_id: UUID, actor: Actor) -> None:
        """Sets deleted_at. No physical delete exists in this slice: a misread
        instruction from an agent must be reversible.

        Refuses while the deal still carries live hours, exactly as
        `CustomerService.soft_delete` refuses while a customer still has active deals. The
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

        ore_attive = self.repo.count_active_time_entries(deal_id)
        if ore_attive:
            raise Conflict(
                ENTITY,
                "il deal ha ore registrate: fatturale o archiviale prima",
                ore_attive=ore_attive,
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

        # Past the row-level policies (REB-634): the customer may sit in an azienda the
        # deal's reader cannot see, and «not found» must not read as «not archived».
        archived = self.session.execute(
            text("SELECT cliente_archiviato(:customer_id)"), {"customer_id": deal.customer_id}
        ).scalar_one_or_none()
        if archived is None:
            raise NotFound("customer", deal.customer_id)
        if archived:
            raise Conflict(
                ENTITY,
                "il cliente è archiviato: ripristina prima il cliente",
                customer_id=str(deal.customer_id),
            )

        # Recorded only when the deal really was deleted: unconditionally logging
        # "restored" here -- even for a deal that was never soft-deleted -- would
        # write a timeline entry claiming a recovery that never happened. Mirrors the
        # identical guard on CustomerService.restore/PersonService.restore.
        was_deleted = deal.deleted_at is not None
        deal.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, deal.id, "restored", actor)
        self.session.commit()
        return self._read(deal)

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (see `FieldDefinitionService.specs_for`'s docstring and
    # `CustomerService`/`PersonService`'s own ordering): defining a method named
    # `list` rebinds that name in the *class* namespace, so any later method whose
    # own return annotation is a bare `list[...]` would resolve `list` to this method
    # instead of the builtin and fail at import time. No method in this class has
    # that shape today, but the rule has no "only when it would currently break"
    # exception.
    def list(self, query: DealListQuery, actor: Actor) -> DealPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # See `CustomerService.list` for why the cursor carries the sort value as well
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
