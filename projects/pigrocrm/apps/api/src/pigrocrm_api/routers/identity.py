"""Root-scoped identity endpoints: the layer proving who someone is before any
space's database opens (design 2026-09-23, REB-345/376). Registered plainly, the
same "root-only by convention rather than by a second registration" shape
`routers/tenants.py` already uses -- see that module's own docstring.

Only `POST /api/identity/logout` exists here for now: REB-376's own scope is the
identity table and the passwordless side effect three existing entry points gain
(`routers/auth.py`). The chooser (`GET /api/identity/spaces`,
`POST /api/identity/enter/{slug}`) is a later issue in the same milestone.
"""

from fastapi import APIRouter, Request, Response, status

from pigrocrm.core.auth.tokens import decode_token
from pigrocrm.core.errors import DomainError
from pigrocrm.core.identity.service import IdentityService
from pigrocrm_api.deps import IDENTITY_COOKIE, SettingsDep, TenantsRegistryDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.tenancy import first_cookie

router = APIRouter(prefix="/api/identity", tags=["identity"], responses=PROBLEM_RESPONSES)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, registry: TenantsRegistryDep, settings: SettingsDep
) -> None:
    """Revokes every live `IdentitySession` for this identity (§2's own "signs out of
    the identity everywhere it was used"), not only the one this browser presents,
    and clears the cookie at `path=/` in this browser. Idempotent, the same
    goal-state discipline the space-scoped `logout` already follows
    (`routers/auth.py`): an absent, malformed or already-expired cookie has nothing
    left to revoke, so that case answers 204 too, never a 401 -- this route has no
    actor dependency to refuse with one."""
    token = first_cookie(request, IDENTITY_COOKIE)
    if token:
        try:
            payload = decode_token(token, settings, expected_type="identity")
        except DomainError:
            payload = None
        if payload is not None:
            IdentityService(registry, settings).revoke_all(payload.sub)
    response.delete_cookie(IDENTITY_COOKIE, path="/")
