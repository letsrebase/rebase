"""What the cron commands last did in this space (REB-764): the four host jobs, each with
its last run, its last failure and how that reads. An admin's, like every section of
Impostazioni but the two personal ones; the record is `reminder_job_run`."""

from fastapi import APIRouter

from pigrocrm.core.reminders.jobs import ScheduledJobService, ScheduledJobsRead
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(tags=["scheduled-jobs"], responses=PROBLEM_RESPONSES)


@router.get("/api/scheduled-jobs", response_model=ScheduledJobsRead)
def describe(session: SessionDep, actor: ActorDep) -> ScheduledJobsRead:
    """Le operazioni che il cron lancia sul server (sincronizzazione Gmail, passaggio dei
    promemoria, automazioni, riepilogo settimanale): per ciascuna l'ultimo giro finito,
    l'ultimo tentativo fallito e se è in ritardo. Solo per gli amministratori."""
    return ScheduledJobService(session).describe(actor)
