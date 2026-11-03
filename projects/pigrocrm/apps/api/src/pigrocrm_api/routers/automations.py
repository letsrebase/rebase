"""§9.6's configuration and §9.5's third observability surface.

`PUT` and not `PATCH`, with both fields optional: the body is a partial update read with
`exclude_unset=True`, and `PUT` is what the shipped `emitter` and `fiscal_profile`
single-row endpoints already use. One convention for single-row configuration.

There is no MCP counterpart for the write, and that is the slice's single declared
exclusion (§11.1). It is enforced by not registering a tool rather than by an
authorisation check, because residuo R10 leaves a personal access token carrying its
owner's full role -- an admin's token would pass any check written inside a tool.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from pigrocrm.core.automations.config_service import AutomationConfigService
from pigrocrm.core.automations.runs import recent_runs
from pigrocrm.core.automations.schemas import (
    AutomationConfigRead,
    AutomationConfigUpdate,
    AutomationRun,
    AutomationsDescription,
)
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    tags=["automations"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.get("/api/automation-config", response_model=AutomationConfigRead)
def get_config(session: SessionDep, actor: ActorDep) -> AutomationConfigRead:
    """The two switches on their own, for a settings form that renders nothing else.

    Served out of `describe_automations` rather than from a second service method: §11.1
    requires the slice-6 exclusion list to be exactly one name, and a public `get` beside
    the public `update` would put a second name on it.
    """
    return AutomationConfigService(session).describe_automations(actor).configurazione


@router.put("/api/automation-config", response_model=AutomationConfigRead)
def put_config(
    data: AutomationConfigUpdate, session: SessionDep, actor: ActorDep
) -> AutomationConfigRead:
    # `require_admin` lives in the service, not here: slice 1's own review found the same
    # check living only in a router and therefore absent for every other caller of the
    # shared service (see `PipelineService.seed_defaults`'s docstring). One place.
    return AutomationConfigService(session).update_automation_config(data, actor)


@router.get("/api/automations", response_model=AutomationsDescription)
def describe(session: SessionDep, actor: ActorDep) -> AutomationsDescription:
    """The two rules, their state and the last executions -- what the settings page renders
    in one request instead of three."""
    return AutomationConfigService(session).describe_automations(actor)


@router.get("/api/automation-runs", response_model=list[AutomationRun])
def runs(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[AutomationRun]:
    """A read of `activities` by `kind`, never a new table (§9.4).

    Bounded like every other list in the project. No role gate beyond authentication: the
    runs are the same timeline entries every role can already read on the entity itself.
    """
    return recent_runs(session, limit)
