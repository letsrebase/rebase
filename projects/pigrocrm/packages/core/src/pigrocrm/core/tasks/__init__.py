"""Attività: a commitment with a due date and a state.

The table slice 7 specified on 2026-09-03 and slice 10 built on 2026-09-09, when the
calendar needed something to put in a day. Everything about the shape -- why `due_date`
is nullable, why there are three states and not two, why at most one reference -- is in
`2026-09-03-slice-7-attivita-e-promemoria-design.md` §3 and repeated where the code
would otherwise be surprising.
"""

from pigrocrm.core.tasks.models import Task
from pigrocrm.core.tasks.repository import TaskRepository
from pigrocrm.core.tasks.schemas import (
    TASK_SORTS,
    TaskCreate,
    TaskListQuery,
    TaskOrigin,
    TaskPage,
    TaskRead,
    TaskStatus,
    TaskUpdate,
)
from pigrocrm.core.tasks.service import TaskService

__all__ = [
    "TASK_SORTS",
    "Task",
    "TaskCreate",
    "TaskListQuery",
    "TaskOrigin",
    "TaskPage",
    "TaskRead",
    "TaskRepository",
    "TaskService",
    "TaskStatus",
    "TaskUpdate",
]
