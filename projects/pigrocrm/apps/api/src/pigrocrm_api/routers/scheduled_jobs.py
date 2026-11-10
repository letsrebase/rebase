"""What the cron commands last did in this space (REB-764): the four host jobs, each with
its last run, its last failure and how that reads. An admin's, like every section of
Impostazioni but the two personal ones; the record is `reminder_job_run`."""

from fastapi import APIRouter, Depends

from pigrocrm.core.reminders.jobs import ScheduledJobService, ScheduledJobsRead
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    tags=["scheduled-jobs"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.get("/api/scheduled-jobs", response_model=ScheduledJobsRead)
def describe(session: SessionDep, actor: ActorDep) -> ScheduledJobsRead:
    """The operations the cron launches on the server (Gmail sync, reminder pass,
    automations, weekly digest): for each, the last finished run,
    the last failed attempt and whether it is late. Admins only."""
    return ScheduledJobService(session).describe(actor)
