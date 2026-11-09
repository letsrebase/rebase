from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from pigrocrm.core.auth.pat_service import PatRead, PatService, TeamPatRead
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.person_only import person_only

router = APIRouter(
    prefix="/api/tokens",
    tags=["tokens"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)

# Mirrors PersonalAccessToken.nome's column width (auth/pat_models.py: String(120)).
# Without this, an over-length value sails past Pydantic, reaches flush(), and comes
# back as a raw sqlalchemy.exc.DataError (StringDataRightTruncation) -- not a subclass
# of IntegrityError, so no handler catches it, and it poisons the session. Same class
# of gap every other Create schema in this codebase already closes (see e.g.
# OrganizationCreate.RAGIONE_SOCIALE_MAX_LENGTH). `PatService.create` takes a bare `nome:
# str`, not a packages/core schema of its own (see pat_service.py), so this router's
# own request model is the only Pydantic layer standing between a caller and
# `personal_access_tokens.name` -- both the length bound and the SafeStr NUL guard
# have to live here, not in packages/core.
NAME_MAX_LENGTH = 120


class CreateTokenRequest(BaseModel):
    name: SafeStr = Field(max_length=NAME_MAX_LENGTH)
    # Closed unless asked for (REB-810): the invoice, rate and period-lock tools are a
    # decision about one agent, taken when its token is made.
    full_access: bool = False


class FullAccessRequest(BaseModel):
    full_access: bool


class TokenOptions(BaseModel):
    """What a member making a token may choose here, which the space decides."""

    # False when the space's `mcp_full_access` (the ceiling) is closed: the box to give a
    # token the invoice tools is then shown as unavailable instead of failing on submit.
    full_access_available: bool


class CreatedToken(PatRead):
    """The only response in the whole API that carries the raw token. It is shown
    once, at creation, and never again."""

    token: str


@router.post(
    "",
    response_model=CreatedToken,
    status_code=status.HTTP_201_CREATED,
    dependencies=[person_only("create_token")],
)
def create(
    data: CreateTokenRequest, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> CreatedToken:
    # `settings`: the space's own, so the ceiling the token is checked against is the
    # one `resolve` will stamp it with, not the process environment's.
    record, raw = PatService(session, settings).create(
        data.name, actor, full_access=data.full_access
    )
    return CreatedToken(**record.model_dump(), token=raw)


@router.get("", response_model=list[PatRead], dependencies=[person_only("list_tokens")])
def list_tokens(session: SessionDep, actor: ActorDep) -> list[PatRead]:
    return PatService(session).list_own(actor)


@router.get("/options", response_model=TokenOptions)
def token_options(settings: SettingsDep) -> TokenOptions:
    """Readable by any member, unlike the space settings page that holds the switch: a
    collaborator who makes a token has to be told why the box is off."""
    return TokenOptions(full_access_available=settings.mcp_full_access)


@router.get("/team", response_model=list[TeamPatRead])
def list_team_tokens(session: SessionDep, actor: ActorDep) -> list[TeamPatRead]:
    """The other members' live tokens, for an administrator (REB-810)."""
    return PatService(session).list_team(actor)


@router.patch("/{token_id}", response_model=PatRead)
def set_full_access(
    token_id: UUID,
    data: FullAccessRequest,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> PatRead:
    """Opens or closes one token's privileged surface. Its owner or an administrator;
    never an agent credential, so a token cannot open itself."""
    return PatService(session, settings).set_full_access(token_id, data.full_access, actor)


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[person_only("revoke_token")],
)
def revoke(token_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    PatService(session).revoke(token_id, actor)
