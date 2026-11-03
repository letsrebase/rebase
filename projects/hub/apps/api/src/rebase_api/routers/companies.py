"""`POST /api/hub/companies`: a company describes what it needs. JSON, public,
rate-limited, mute -- the same three properties as the other two public writes. The
completion is reported to PostHog after the answer, as the freelancers' is
(`rebase_core.analytics`, REB-215), and filed in rebase's CRM after it too (REB-868)."""

from fastapi import APIRouter, BackgroundTasks, Request, status

from rebase_api.deps import CrmFilingDep, SessionDep, TrackerDep
from rebase_api.ratelimit import spend_one
from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate
from rebase_core.schema_common import Ack

router = APIRouter(prefix="/api/hub", tags=["hub"])


@router.post("/companies", response_model=Ack, status_code=status.HTTP_201_CREATED)
def request_people(
    data: CompanyCreate,
    session: SessionDep,
    request: Request,
    background: BackgroundTasks,
    tracker: TrackerDep,
    crm_filing: CrmFilingDep,
) -> Ack:
    spend_one(request)
    # `richiedente_esistente` (whether this address already had a request) is
    # computed by the service and used internally (REB-355's audit trail could read
    # it later), but never answered to this public, unauthenticated caller: doing so
    # would let anyone probe an arbitrary email to learn whether it has submitted a
    # request before (Greptile, PR #312).
    filed, _ = CompanyService(session).request(data)
    # The request is already `da_collegare`, committed with its row; this is the first
    # attempt at the CRM, after the answer. The sweep recovers whatever it leaves.
    background.add_task(crm_filing, filed.id)
    if tracker is not None:
        background.add_task(tracker.application, "azienda", data.distinct_id, utm=data.utm)
    return Ack()
