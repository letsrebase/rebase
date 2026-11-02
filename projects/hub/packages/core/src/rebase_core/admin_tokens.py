"""Personal tokens of the admins, for agents: the credential the MCP server takes.

The shape of the CRM's `PatService`, written again because the hub imports nothing from
the CRM, and without the activity timeline the hub does not have. The value is minted
here, returned once and never stored; what the table holds is its sha256, which is a
deterministic lookup key and safe because the value is 32 random bytes, not a password
somebody chose. Every way `resolve` can fail is the same sentence: an unknown value, a
revoked token and a deactivated or demoted owner are not told apart, so a leaked token
is not an oracle for whether it is still worth using.

Since REB-278 the owner is a `users` row with `role == 'admin'` (`user_id`); since
REB-281 that table is the only one an admin has ever lived in, `admin_users` gone with
the password login. `AdminRead` moved here from the retired `rebase_core.admin`, its
shape unchanged: a thin read of a `users` row, never the card fields, never a join."""

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.models import NAME_MAX_LENGTH, AdminToken, User
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.schema_common import ResponseModel
from rebase_core.search import matches_any, similarity_score

ENTITY = "token"
TOKEN_PREFIX = "reb_"
PREFIX_VISIBLE_CHARS = 8
INVALID_TOKEN = "Token non valido"
DEFAULT_NAME = "Claude Code"
TOKEN_LIST_LIMIT_DEFAULT = 100
TOKEN_LIST_LIMIT_MAX = 500
_TOKEN_SEARCH_COLUMNS = (AdminToken.nome,)


class AdminRead(ResponseModel):
    """An admin as the API and the MCP server both read them: never the card, never a
    join. `attivo` and `created_at` are here for the list of admins (ORB-123)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nome: str
    attivo: bool
    created_at: datetime


class AdminTokenRead(ResponseModel):
    """A token as the admin area lists it: never the hash, never the value."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class AdminList(ResponseModel):
    """Every admin (ORB-123), oldest first with no search term so the page still reads
    as a history, or best-match first once `q` narrows it (REB-313, the shape REB-285
    gives Talenti and Aziende). `next_cursor` is `None` on the last page."""

    items: list[AdminRead]
    next_cursor: str | None = None


class AdminTokenList(ResponseModel):
    """The admin's own tokens (REB-313), the same search-and-cursor shape beside
    `AdminTokenService.list`'s full, unpaginated read. `next_cursor` is `None` on the
    last page."""

    items: list[AdminTokenRead]
    next_cursor: str | None = None


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def is_token(raw: str | None) -> bool:
    """Whether a string could be one of ours: a bearer that is not is no credential at
    all, and the caller refuses it without a read."""
    return bool(raw) and raw.startswith(TOKEN_PREFIX)  # type: ignore[union-attr]


class AdminTokenService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: UUID, nome: str) -> tuple[AdminTokenRead, str]:
        """A fresh token for the admin, with the name they gave it. The raw value is the
        second element, returned exactly once."""
        nome = nome.strip()
        if not nome:
            raise ValidationFailed(ENTITY, "nome", "serve un nome")
        if len(nome) > NAME_MAX_LENGTH:
            raise ValidationFailed(ENTITY, "nome", f"al massimo {NAME_MAX_LENGTH} caratteri")
        owner = self.session.get(User, user_id)
        if owner is None or owner.role != "admin" or not owner.attivo:
            raise NotFound("admin", user_id)
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        row = AdminToken(
            user_id=user_id,
            nome=nome,
            token_hash=_digest(raw),
            prefix=raw[: len(TOKEN_PREFIX) + PREFIX_VISIBLE_CHARS],
        )
        self.session.add(row)
        self.session.commit()
        return AdminTokenRead.model_validate(row), raw

    def revoke(self, user_id: UUID, token_id: UUID) -> AdminTokenRead:
        """Ends a token of this admin. Another admin's token is `NotFound`, so the route
        cannot be used to learn that it exists. Revoking twice keeps the first date."""
        row = self.session.scalar(
            select(AdminToken).where(AdminToken.id == token_id, AdminToken.user_id == user_id)
        )
        if row is None:
            raise NotFound(ENTITY, token_id)
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            self.session.commit()
        return AdminTokenRead.model_validate(row)

    def resolve(self, raw: str | None) -> AdminRead:
        """The admin behind a token, or one `ValidationFailed` for every failure. Stamps
        `last_used_at`, which is what the list shows as «ultimo uso». `populate_existing`
        forces a fresh read of the owner: `role`/`attivo` can change between two calls
        against the same long-lived session (an MCP server's, in production; a test's
        raw `UPDATE` in this package's own suite), and a cached, stale copy would let a
        just-demoted or just-deactivated admin's token keep resolving."""
        if not is_token(raw):
            raise ValidationFailed(ENTITY, "token", INVALID_TOKEN)
        assert raw is not None
        row = self.session.scalar(select(AdminToken).where(AdminToken.token_hash == _digest(raw)))
        if row is None or row.revoked_at is not None:
            raise ValidationFailed(ENTITY, "token", INVALID_TOKEN)
        owner = self.session.get(User, row.user_id, populate_existing=True)
        if owner is None or owner.role != "admin" or not owner.attivo:
            raise ValidationFailed(ENTITY, "token", INVALID_TOKEN)
        row.last_used_at = datetime.now(UTC)
        self.session.commit()
        return AdminRead.model_validate(owner)

    def list_page(
        self,
        user_id: UUID,
        limit: int = TOKEN_LIST_LIMIT_DEFAULT,
        *,
        q: str | None = None,
        cursor: str | None = None,
    ) -> AdminTokenList:
        """The admin's own tokens with search and a cursor beside `list`'s full read
        (REB-313): newest first with no term, as `list` already orders them, or
        best-match first by name once `q` narrows it. Revoked ones included either
        way, since a list that hides what was revoked cannot show that somebody
        revoked it."""
        limit = max(1, min(limit, TOKEN_LIST_LIMIT_MAX))
        term = (q or "").strip()
        stmt = select(AdminToken).where(AdminToken.user_id == user_id)
        if term:
            stmt = stmt.where(matches_any(_TOKEN_SEARCH_COLUMNS, term))
        sort_spec = SortSpec("score", "float") if term else SortSpec("created_at", "datetime")
        sort_column: Any
        if term:
            score = similarity_score(_TOKEN_SEARCH_COLUMNS, term).label("score")
            stmt = stmt.add_columns(score)
            sort_column = score
        else:
            sort_column = AdminToken.created_at
        if cursor:
            value, row_id = decode_cursor(sort_spec, cursor)
            stmt = stmt.where(keyset_predicate(sort_column, AdminToken.id, value, row_id))
        rows = self.session.execute(
            stmt.order_by(sort_column.desc(), AdminToken.id.desc()).limit(limit + 1)
        ).all()
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            last_sort = last.score if term else last[0].created_at
            next_cursor = encode_cursor(sort_spec, last_sort, last[0].id)
        return AdminTokenList(
            items=[AdminTokenRead.model_validate(row[0]) for row in page_rows],
            next_cursor=next_cursor,
        )

    # Last on purpose: a method named `list` rebinds the name in the class body, and a
    # bare `list[...]` annotation below it would fail at import time.
    def list(self, user_id: UUID) -> list[AdminTokenRead]:
        """The admin's own tokens, newest first, revoked ones included: a list that hides
        what was revoked cannot show that somebody revoked it."""
        rows = self.session.scalars(
            select(AdminToken)
            .where(AdminToken.user_id == user_id)
            .order_by(AdminToken.created_at.desc(), AdminToken.id.desc())
        ).all()
        return [AdminTokenRead.model_validate(row) for row in rows]
