"""Settings, Space group, Modules section: the space's profile and which modules are on (spec
2026-11-09, B1 and B2).

The read is open to every member and every agent, since it says what the space can do. The
two writes are an unscoped admin's, and an agent's only with a full-access token
(`core/agent_actions.py`): both are the service's checks, never this router's. They are
routes of their own and not part of `PUT /api/settings/space`, so the generic settings page
cannot bypass the confirmation the profile change asks for. After a write the per-process
cache of overrides is dropped for this database, so the next request sees it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.modules.schemas import (
    SpaceModulesRead,
    SpaceModulesSet,
    SpaceProfileProposal,
    SpaceProfileSet,
)
from pigrocrm.core.modules.service import SpaceModulesService
from pigrocrm_api.deps import (
    ActorDep,
    BaseSettingsDep,
    SessionDep,
    get_actor,
    invalidate_space_settings,
)
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.tenancy import tenant_slug

router = APIRouter(
    prefix="/api/settings/modules",
    tags=["settings"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.get("", response_model=SpaceModulesRead)
def read(session: SessionDep, base: BaseSettingsDep, actor: ActorDep) -> SpaceModulesRead:
    """The profile and every module: whether it is on, what its switch and its default
    are, and whether the space has records in it."""
    return SpaceModulesService(session, base).describe(actor)


@router.put("/profile", response_model=SpaceProfileProposal)
def set_profile(
    data: SpaceProfileSet,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    base: BaseSettingsDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SpaceProfileProposal:
    """Without `confirm`: the modules the profile would switch on and off, and nothing is
    written. With `confirm`: the profile and a switch for every module are stored. Records
    are never touched."""
    result = SpaceModulesService(session, base).set_profile(data, actor)
    if result.applied:
        invalidate_space_settings(tenant_slug(request), settings)
    return result


@router.post("/onboarding/skip", response_model=SpaceModulesRead)
def skip_onboarding(
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    base: BaseSettingsDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SpaceModulesRead:
    """«Decido dopo» on the first step of the onboarding: every module stays on, nothing
    is chosen, and the onboarding waits in «Primi passi» (`onboarding` answers `skipped`).
    A space that already has a profile is left as it is."""
    result = SpaceModulesService(session, base).skip_onboarding(actor)
    invalidate_space_settings(tenant_slug(request), settings)
    return result


@router.put("", response_model=SpaceModulesRead)
def set_modules(
    data: SpaceModulesSet,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    base: BaseSettingsDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SpaceModulesRead:
    """Switches the modules in `on` and `off`. A module that holds records goes off only
    when it is named in `confirm_off`; its records stay and return with it."""
    result = SpaceModulesService(session, base).set_modules(data, actor)
    invalidate_space_settings(tenant_slug(request), settings)
    return result
