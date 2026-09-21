"""The spaces of PigroCRM, read-only, behind the admin cookie (ORB-142).

One route. The CRM is reached through the HTTP seam with the token the two `.env` files
share; the hub's own database is touched only to name the member who owns a space.
"""

from fastapi import APIRouter, HTTPException, status

from rebase_api.deps import AdminDep, HttpCallDep, SessionDep, SettingsDep
from rebase_core.pigro import PigroRegistry, PigroSpaceList, PigroUnavailable

router = APIRouter(prefix="/api/hub/pigro", tags=["hub-admin"])


@router.get("/instances", response_model=PigroSpaceList)
def list_spaces(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    http: HttpCallDep,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> PigroSpaceList:
    """Every space in PigroCRM's registry, newest first, each with the hub member who
    opened it when the address is one the wizard knows; `q` searches the slug and the
    owner's address, `cursor`/`limit` page the result (REB-313). 503 with a sentence
    when the token is not configured, 502 when the CRM does not answer with a list."""
    if not settings.pigro_registry_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Il registro di Pigro non è configurato: manca REBASE_PIGRO_REGISTRY_TOKEN.",
        )
    try:
        return PigroRegistry(settings, http).list_spaces(session, q=q, cursor=cursor, limit=limit)
    except PigroUnavailable as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
