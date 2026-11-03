from datetime import date
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.db import decode_cursor, keyset_predicate, order_by
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.tasks.schemas import TASK_SORTS, TaskListQuery


def _assigned(user_id: UUID, include_unassigned: bool) -> ColumnElement[bool]:
    """One person's attività, and with `include_unassigned` the ones nobody holds too."""
    if include_unassigned:
        return or_(Task.assigned_to == user_id, Task.assigned_to.is_(None))
    return Task.assigned_to == user_id


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, task_id: UUID, *, include_deleted: bool = False) -> Task | None:
        row = self.session.get(Task, task_id)
        if row is None:
            return None
        if row.deleted_at is not None and not include_deleted:
            return None
        return row

    def add(self, attivita: Task) -> Task:
        self.session.add(attivita)
        self.session.flush()
        return attivita

    def in_range(self, da: date, a: date) -> list[Task]:
        """Every live activity whose `due_date` falls in `[da, a]`, earliest first.

        For the calendar (slice 10 §6), which asks by month and not by page: no cursor,
        no whitelist, and no limit -- a month of commitments is bounded by the month.
        Ordered by `(scadenza, created_at)` so a day with several is stable between two
        reads of the same month, which a grid redrawing itself needs.

        Activities with no `due_date` are **not** here, by construction: a range cannot
        answer for a row that has no date. `without_due_date` below is how they are asked
        for, and the calendar shows them outside the grid.
        """
        return list(
            self.session.scalars(
                select(Task)
                .where(
                    Task.deleted_at.is_(None),
                    Task.due_date.is_not(None),
                    Task.due_date >= da,
                    Task.due_date <= a,
                )
                .order_by(Task.due_date, Task.created_at)
            )
        )

    def without_due_date(self, *, limit: int) -> list[Task]:
        """The open commitments with no date, newest first, capped.

        Only `open`: a completed activity with no date is history, and the calendar's
        «senza scadenza» section is a list of things still to do. Capped because that
        section sits under a grid and is not a page -- an installation with four hundred
        undated to-dos would otherwise send all four hundred with every month.
        """
        return list(
            self.session.scalars(
                select(Task)
                .where(
                    Task.deleted_at.is_(None),
                    Task.due_date.is_(None),
                    Task.status == "open",
                )
                .order_by(Task.created_at.desc(), Task.id.desc())
                .limit(limit)
            )
        )

    def open_in_the_window(
        self,
        *,
        assigned_to: UUID | None,
        limit: int,
        include_unassigned: bool = False,
        before_of: date | None = None,
        dal: date | None = None,
        al: date | None = None,
        without_due_date: bool = False,
    ) -> tuple[int, list[Task]]:
        """The open, live commitments in one window of dates, and how many there are in all.

        One window per call and never a union: the Oggi panel (spec 2026-09-03 §5) is four
        lists, and the digest asks for two of them, so each is one predicate over the same
        base. `before_of` is exclusive («due before that day»); `dal` and `al` are
        inclusive; `without_due_date` is the undated, which no date predicate can ask for
        because `NULL <= date` is not true. Only `open`: an overdue count that included a
        completed commitment would say somebody is late for something they did.

        The count is the window's own and is not capped by `limit`: the sidebar shows the
        number, and a number that stopped at the page size would be a different fact.
        """
        base = [Task.deleted_at.is_(None), Task.status == "open"]
        if assigned_to is not None:
            base.append(_assigned(assigned_to, include_unassigned))
        if without_due_date:
            base.append(Task.due_date.is_(None))
        if before_of is not None:
            base.append(Task.due_date < before_of)
        if dal is not None:
            base.append(Task.due_date >= dal)
        if al is not None:
            base.append(Task.due_date <= al)
        total = self.session.scalar(select(func.count()).select_from(Task).where(*base)) or 0
        order = (
            (Task.created_at.desc(), Task.id.desc())
            if without_due_date
            else (Task.due_date, Task.created_at, Task.id)
        )
        lines = list(self.session.scalars(select(Task).where(*base).order_by(*order).limit(limit)))
        return total, lines

    def list(self, query: TaskListQuery) -> list[Task]:
        archived = Task.deleted_at.is_not(None) if query.archived else Task.deleted_at.is_(None)
        stmt = select(Task).where(archived)

        if query.status:
            stmt = stmt.where(Task.status == query.status)
        if query.assigned_to:
            stmt = stmt.where(_assigned(query.assigned_to, query.include_unassigned))
        if query.customer_id:
            stmt = stmt.where(Task.customer_id == query.customer_id)
        if query.person_id:
            stmt = stmt.where(Task.person_id == query.person_id)
        if query.deal_id:
            stmt = stmt.where(Task.deal_id == query.deal_id)
        if query.invoice_id:
            stmt = stmt.where(Task.invoice_id == query.invoice_id)
        if query.due_within is not None:
            # Inclusive, and it excludes the undated rows on its own -- `NULL <= date`
            # is NULL, which is not true. Stated in the schema and asserted in the
            # tests, because «a filter that silently drops rows» is the shape of defect
            # §3.2 is written to prevent.
            stmt = stmt.where(Task.due_date <= query.due_within)
        if query.without_due_date is True:
            stmt = stmt.where(Task.due_date.is_(None))
        elif query.without_due_date is False:
            stmt = stmt.where(Task.due_date.is_not(None))

        spec = TASK_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )
