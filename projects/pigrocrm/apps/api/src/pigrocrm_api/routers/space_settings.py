"""Impostazioni → Spazio: the settings this database decides for itself.

Admin only, through `SpaceSettingsService`. The `base` the service lays the rows over is
`BaseSettingsDep` -- the environment as this request may see it, with a space's Google
blanked and its public URL derived -- and never `SettingsDep`, which already has the
rows applied and would count them twice. After a write the per-process cache of
overrides is dropped for this database, so the next request sees the new values.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.space_settings import (
    SpaceSettingsRead,
    SpaceSettingsService,
    SpaceSettingsUpdate,
)
from pigrocrm_api.deps import (
    ActorDep,
    BaseSettingsDep,
    SessionDep,
    invalidate_space_settings,
)
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.tenancy import tenant_slug

router = APIRouter(prefix="/api/settings/space", tags=["settings"], responses=PROBLEM_RESPONSES)


@router.get("", response_model=SpaceSettingsRead)
def read(
    request: Request, session: SessionDep, actor: ActorDep, base: BaseSettingsDep
) -> SpaceSettingsRead:
    return SpaceSettingsService(session, base).read(actor, spazio=tenant_slug(request))


@router.put("", response_model=SpaceSettingsRead)
def update(
    data: SpaceSettingsUpdate,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    base: BaseSettingsDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SpaceSettingsRead:
    """Only the keys sent change; with `updated_at` in the body (the read's own, `null`
    included), a save on settings somebody else saved since answers 409 `stale_row`
    (REB-622)."""
    result = SpaceSettingsService(session, base).update(data, actor, spazio=tenant_slug(request))
    # This request's own settings, so a test that overrides `get_settings` invalidates
    # the registry it read from rather than one built against the process environment.
    invalidate_space_settings(tenant_slug(request), settings)
    return result
