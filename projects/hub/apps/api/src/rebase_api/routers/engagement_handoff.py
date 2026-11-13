"""«Crea match» hands the engagement over to the CRM (REB-1105), behind the admin cookie.

The engagement itself (its letter, its signature, its hours) is the CRM's; the hub keeps the
talent side and the read model of the engagements (`hub_engagements`). This is the one route of
the old matches router that stayed: it opens the CRM's engagement form for a freelancer and a
company request. It answers the split `routers/pigro.py` makes: 503 when this environment has
no token for the CRM, 502 with the seam's sentence when the CRM does not answer.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from rebase_api.deps import AdminDep, FreelancerDoorDep, SessionDep, SettingsDep
from rebase_core.engagement_handoff import EngagementHandoff, HandoffFailed, HandoffRead

router = APIRouter(prefix="/api/hub", tags=["hub-admin"])

PIGRO_NOT_CONFIGURED = "Consuntivo non configurato su questo ambiente."


class HandoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID


@router.post("/freelancers/{freelancer_id}/engagement-handoff", response_model=HandoffRead)
def hand_off_engagement(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    door: FreelancerDoorDep,
    freelancer_id: UUID,
    body: HandoffBody,
) -> HandoffRead:
    """«Crea match» (REB-1105): the CRM finds or writes the freelancer's organization, the hub
    keeps a `handoff` row under a freshly minted engagement id with the company's budget
    snapshot, and the answer carries the CRM's engagement form to open, filled in. A request not
    in the CRM yet answers `no_deal` with its sentence and writes nothing. 503 without the CRM's
    token, 409 with the CRM's sentence when it refuses the card, 502 when it does not answer."""
    if not settings.pigro_engagements_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, PIGRO_NOT_CONFIGURED)
    try:
        return EngagementHandoff(session, door).open(freelancer_id, body.company_id)
    except HandoffFailed as exc:
        code = status.HTTP_409_CONFLICT if exc.rejected else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, exc.sentence) from exc
