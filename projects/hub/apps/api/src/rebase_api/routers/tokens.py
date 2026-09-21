"""An admin's personal tokens, behind the admin cookie (REB-213): the credential an
agent presents to the MCP server. The value appears in the `POST` answer and nowhere
else; the list carries names, prefixes and dates."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from rebase_api.deps import AdminDep, SessionDep
from rebase_core.admin_tokens import (
    DEFAULT_NAME,
    AdminTokenList,
    AdminTokenRead,
    AdminTokenService,
)
from rebase_core.models import NAME_MAX_LENGTH
from rebase_core.pagination import CURSOR_MAX_LENGTH
from rebase_core.search import SEARCH_MAX_LENGTH
from rebase_core.validation import SafeStr

router = APIRouter(prefix="/api/hub/tokens", tags=["hub-admin"])

Limit = Annotated[int, Query(ge=1, le=500)]
# The same bounded shape REB-285 gives `/talent` and `/companies`: an unbounded free-
# text query or cursor reaching the database is a denial of service with extra steps.
SearchQ = Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)]
Cursor = Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)]


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(default=DEFAULT_NAME, min_length=1, max_length=NAME_MAX_LENGTH)


class CreatedToken(AdminTokenRead):
    """The one response in the API that carries the raw value: shown once, never again."""

    token: str


@router.get("", response_model=AdminTokenList)
def list_tokens(
    admin: AdminDep,
    session: SessionDep,
    limit: Limit = 100,
    q: SearchQ = None,
    cursor: Cursor = None,
) -> AdminTokenList:
    """REB-313 adds `q` (by the token's own `nome`, trigram-ordered once searching) and
    `cursor` beside the newest-first order this list already had."""
    return AdminTokenService(session).list_page(admin.id, limit=limit, q=q, cursor=cursor)


@router.post("", response_model=CreatedToken, status_code=status.HTTP_201_CREATED)
def create_token(admin: AdminDep, session: SessionDep, payload: TokenCreate) -> CreatedToken:
    record, raw = AdminTokenService(session).create(admin.id, payload.nome)
    return CreatedToken(**record.model_dump(), token=raw)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(admin: AdminDep, session: SessionDep, token_id: UUID) -> None:
    """Revokes one of the caller's tokens; another admin's is 404, as if it did not exist."""
    AdminTokenService(session).revoke(admin.id, token_id)
