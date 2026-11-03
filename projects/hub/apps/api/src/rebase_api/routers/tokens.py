"""An admin's personal tokens, behind the admin cookie (REB-213): the credential an
agent presents to the MCP server. The value appears in the `POST` answer and nowhere
else; the list carries names, prefixes and dates."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from rebase_api.deps import AdminDep, SessionDep
from rebase_api.params import Cursor, Limit, SearchQ
from rebase_core.admin_tokens import (
    DEFAULT_NAME,
    AdminTokenList,
    AdminTokenRead,
    AdminTokenService,
)
from rebase_core.models import NAME_MAX_LENGTH
from rebase_core.validation import SafeStr

router = APIRouter(prefix="/api/hub/tokens", tags=["hub-admin"])


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(default=DEFAULT_NAME, min_length=1, max_length=NAME_MAX_LENGTH)
    # When the token stops working (REB-803): a timezone-aware instant in the future, or
    # omitted for a token that never expires. A past or naive one is a 422 sentence.
    expires_at: datetime | None = None


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
    record, raw = AdminTokenService(session).create(admin.id, payload.nome, payload.expires_at)
    return CreatedToken(**record.model_dump(), token=raw)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(admin: AdminDep, session: SessionDep, token_id: UUID) -> None:
    """Revokes one of the caller's tokens; another admin's is 404, as if it did not exist."""
    AdminTokenService(session).revoke(admin.id, token_id)
