from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import encode_cursor, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.people.models import Person
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.tasks.repository import TaskRepository
from pigrocrm.core.tasks.schemas import (
    DAYS_NEXT,
    REFERENCES,
    TASK_SORTS,
    TaskCreate,
    TaskGroup,
    TaskListQuery,
    TaskPage,
    TaskRead,
    TaskToday,
    TaskTodayQuery,
    TaskUpdate,
)

ENTITY: EntityType = "task"

# The two closed states, and the one open one. As a frozenset rather than repeated
# literals because three methods ask the same question.
CLOSED = frozenset({"completed", "cancelled"})

# Which table each reference column points at, so `_check_reference` is a loop and not
# four near-identical blocks. The order is `REFERENCES`'s.
_TABELLE: dict[str, type[Customer] | type[Person] | type[Deal] | type[Invoice]] = {
    "customer_id": Customer,
    "person_id": Person,
    "deal_id": Deal,
    "invoice_id": Invoice,
}


class TaskService:
    """Commitments: create, rewrite, close, and the two ways of closing.

    Every write goes through `actor.require_write`, and none of these operations is on
    the MCP ban list of slice 3 §11: nothing here consumes a number, touches the fiscal
    register or rewrites a rate. An activity created in error is archived; one that is
    no longer needed is *cancelled*, which is a different statement and the reason the
    state machine has three states.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = TaskRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    # ---- checks -----------------------------------------------------------------------

    def _check_reference(self, payload: dict[str, Any]) -> None:
        """At most one reference, and it must exist.

        The database has the same check (`ck_tasks_single_reference`) and is the
        line that holds under concurrency; this is the one that turns a syntactically
        valid but unknown UUID into this project's own `NotFound` instead of a raw
        `ForeignKeyViolation` arriving from `flush()`.

        Zero references is legitimate and says so in the spec: «do March's e-invoicing»
        belongs to no customer.
        """
        named = [field for field in REFERENCES if payload.get(field) is not None]
        if len(named) > 1:
            raise ValidationFailed(
                ENTITY,
                named[1],
                "un'attività può riferirsi a un solo cliente, persona, deal o fattura",
                expected=f"al massimo uno fra {', '.join(REFERENCES)}",
            )
        for field in named:
            identifier = payload[field]
            if self.session.get(_TABELLE[field], identifier) is None:
                raise NotFound(field.removesuffix("_id"), identifier)

    def _check_assigned(self, payload: dict[str, Any]) -> None:
        """`None` is accepted and means «whoever opens the list», not «nobody» -- the
        spec's own words. A supplied id has to be a user that exists, and an inactive
        one is refused: assigning work to a disabled account is a commitment nobody will
        ever see."""
        user_id = payload.get("assigned_to")
        if user_id is None:
            return
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user", user_id)
        if not user.active:
            raise Conflict(ENTITY, "l'utente è disattivato", user_id=str(user_id))

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _require(self, task_id: UUID) -> Task:
        row = self.repo.get(task_id)
        if row is None:
            raise NotFound(ENTITY, task_id)
        return row

    def _flush_or_conflict(self, task_id: UUID) -> None:
        """Flush, answering the automation index as a `Conflict` and not a 500.

        `uq_tasks_rule_open` allows one open attivita per automation rule and
        subject, and the daily pass opens a new one as soon as the old one is closed. So
        reopening, restoring or re-linking an old one can collide with it, and that is a
        sentence for the person («chiudi o annulla l'altra»), not a database error."""
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != (
                "uq_tasks_rule_open"
            ):
                raise
            raise Conflict(
                ENTITY,
                "esiste già un'attività aperta della stessa regola per lo stesso soggetto: "
                "chiudi o annulla quella prima",
                task_id=str(task_id),
            ) from exc

    # ---- writes -----------------------------------------------------------------------

    def create(self, data: TaskCreate, actor: Actor) -> TaskRead:
        actor.require_write("create_task")
        payload = data.model_dump()
        self._check_reference(payload)
        self._check_assigned(payload)
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})
        # `origin` is not a field of `TaskCreate`: everything created through this
        # method is manual by definition. An automation writes its own rows with
        # `origin='automation'` and a `rule`, and the day slice 7 §6 is built it will
        # do so through a method of its own -- not by letting a caller claim to be one.
        row = self.repo.add(Task(**payload, status="open", origin="manual"))
        self.activities.record(
            ENTITY, row.id, "created", actor, {"title": row.title, "due_date": row.due_date}
        )
        self.session.commit()
        return TaskRead.model_validate(row)

    def update(self, task_id: UUID, data: TaskUpdate, actor: Actor) -> TaskRead:
        actor.require_write("update_task")
        row = self._require(task_id)

        changes = supplied_changes(
            data,
            exclude={"custom_fields", "due_date_to_remove", "reference_to_remove"},
        )
        reject_cleared_columns(ENTITY, Task, changes)

        # The two explicit clears, applied before the checks so that «move this activity
        # from the deal to nobody, and give it a date» is one coherent request.
        if data.due_date_to_remove:
            if "due_date" in changes:
                raise ValidationFailed(
                    ENTITY,
                    "due_date",
                    "non si può indicare una scadenza e chiederne la rimozione nella stessa "
                    "richiesta",
                    expected="una scadenza oppure scadenza_da_rimuovere",
                )
            changes["due_date"] = None
        if data.reference_to_remove:
            if any(field in changes for field in REFERENCES):
                raise ValidationFailed(
                    ENTITY,
                    "reference_to_remove",
                    "non si può collegare un'attività e scollegarla nella stessa richiesta",
                    expected="un riferimento oppure riferimento_da_rimuovere",
                )
            for field in REFERENCES:
                changes[field] = None

        # The check reads the *result*, not the patch: a request that sets `deal_id` on
        # an activity already hanging off a customer would pass a check that only looked
        # at what was supplied, and then fail at the database.
        risultante = {
            field: changes.get(field, getattr(row, field)) for field in (*REFERENCES, "assigned_to")
        }
        self._check_reference(risultante)
        self._check_assigned(risultante)

        if data.custom_fields is not None:
            changes["custom_fields"] = self._validated_custom(data.custom_fields)

        for field, value in changes.items():
            setattr(row, field, value)
        self._flush_or_conflict(row.id)
        if changes:
            self.activities.record(ENTITY, row.id, "updated", actor, {"fields": sorted(changes)})
        self.session.commit()
        return TaskRead.model_validate(row)

    def complete(self, task_id: UUID, actor: Actor) -> TaskRead:
        """«Done», with the day it was done.

        `today_local()` and never `date.today()`: slice 6B reduced this project to one
        clock and an AST scan fails the build on a second one. Idempotent on purpose --
        completing something already completed is what a second click on a checkbox is,
        and it must not move the date, because the date is when the work happened.
        """
        actor.require_write("complete_task")
        row = self._require(task_id)
        if row.status == "completed":
            return TaskRead.model_validate(row)
        if row.status == "cancelled":
            raise Conflict(
                ENTITY,
                "un'attività annullata non si completa: riaprila prima",
                task_id=str(task_id),
            )
        row.status = "completed"
        row.completed_at = today_local()
        self.activities.record(ENTITY, row.id, "completed", actor, {"at": row.completed_at})
        self.session.commit()
        return TaskRead.model_validate(row)

    def cancel(self, task_id: UUID, actor: Actor) -> TaskRead:
        """«It is no longer needed», which is not «it never existed».

        The whole reason the third state exists: a commitment that stopped mattering
        carries the fact that it stopped, and the same row deleted carries nothing.
        Completing is not available afterwards without reopening, so the two closed
        states cannot be confused for one another.
        """
        actor.require_write("cancel_task")
        row = self._require(task_id)
        if row.status == "cancelled":
            return TaskRead.model_validate(row)
        row.status = "cancelled"
        # Cleared, because the check constraint ties the date to the completed state and
        # because «cancelled on» is not a thing this table records: what is recorded is
        # that it was cancelled, in the timeline, with who did it.
        row.completed_at = None
        self.activities.record(ENTITY, row.id, "cancelled", actor)
        self.session.commit()
        return TaskRead.model_validate(row)

    def reopen(self, task_id: UUID, actor: Actor) -> TaskRead:
        """Back to `open`, from either closed state.

        Exists because both closures are reversible decisions about the future, unlike
        anything in slice 3: I completed the wrong row, or the client came back. The
        completion date goes with it -- keeping it would leave a row that is open and
        also says when it was finished.
        """
        actor.require_write("reopen_task")
        row = self._require(task_id)
        if row.status == "open":
            return TaskRead.model_validate(row)
        row.status = "open"
        row.completed_at = None
        self._flush_or_conflict(row.id)
        self.activities.record(ENTITY, row.id, "reopened", actor)
        self.session.commit()
        return TaskRead.model_validate(row)

    def soft_delete(self, task_id: UUID, actor: Actor) -> None:
        """For the typo, not for the change of plan -- which is `cancel`."""
        actor.require_write("archive_task")
        row = self._require(task_id)
        row.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, row.id, "archived", actor)
        self.session.commit()

    def restore(self, task_id: UUID, actor: Actor) -> TaskRead:
        actor.require_write("restore_task")
        row = self.repo.get(task_id, include_deleted=True)
        if row is None:
            raise NotFound(ENTITY, task_id)
        # Recorded only if it really was archived: an unconditional «restored» would
        # claim a recovery that never happened -- the guard `PersonService.restore`
        # carries, for the same reason.
        was_deleted = row.deleted_at is not None
        row.deleted_at = None
        self._flush_or_conflict(row.id)
        if was_deleted:
            self.activities.record(ENTITY, row.id, "restored", actor)
        self.session.commit()
        return TaskRead.model_validate(row)

    def get(self, task_id: UUID, actor: Actor) -> TaskRead:
        return TaskRead.model_validate(self._require(task_id))

    def today(self, actor: Actor, query: TaskTodayQuery) -> TaskToday:
        """The four sections of the Oggi panel, cut on the emitter's today.

        `query.assigned_to` narrows every section to one person's; `None` is the whole space.
        With `query.include_unassigned` it keeps the attività nobody holds too.
        The day is read once and every section is cut on it, so a request that straddles
        midnight cannot list the same commitment under two headings.
        """
        day = today_local()

        def group(**window: Any) -> TaskGroup:
            total, lines = self.repo.open_in_the_window(
                assigned_to=query.assigned_to,
                include_unassigned=query.include_unassigned,
                limit=query.limit,
                **window,
            )
            return TaskGroup(total=total, items=[TaskRead.model_validate(riga) for riga in lines])

        return TaskToday(
            today=day,
            overdue=group(before_of=day),
            today_due=group(dal=day, al=day),
            next_seven_days=group(dal=day + timedelta(days=1), al=day + timedelta(days=DAYS_NEXT)),
            without_due_date=group(without_due_date=True),
        )

    # `list` stays the last method of this class -- the unconditional project rule: the
    # name is rebound in the class namespace, so any method defined after it whose
    # return annotation is a bare `list[...]` would resolve `list` to this method and
    # fail at import time.
    def list(self, query: TaskListQuery, actor: Actor) -> TaskPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        spec = TASK_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return TaskPage(
            items=[TaskRead.model_validate(row) for row in items],
            next_cursor=next_cursor,
        )
