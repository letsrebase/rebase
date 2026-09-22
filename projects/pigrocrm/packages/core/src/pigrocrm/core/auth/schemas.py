from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from pigrocrm.core.actor import Role
from pigrocrm.core.validation import SafeStr

MIN_PASSWORD_LENGTH = 10

# `tariffa_oraria_default`/`costo_orario_default` are Numeric(12,6) -- factors, not
# amounts. Declared locally rather than imported from `deals/schemas.py`: `deals`
# and `auth` do not depend on each other today and this is not the reason to start.
FACTOR_MAX_DIGITS = 12
FACTOR_DECIMAL_PLACES = 6

# Mirrors `users.nome`'s column width (auth/models.py: String(200)). Predates every
# other domain's *_MAX_LENGTH sweep and was never itself swept until the final
# review: without this, an over-length value sails past Pydantic, reaches flush(),
# and comes back as a raw sqlalchemy.exc.DataError (StringDataRightTruncation) --
# not a subclass of IntegrityError, so no handler catches it, and it poisons the
# session. `email` needs no equivalent bound: it is `EmailStr`, and email-validator
# already refuses an address longer than RFC 5321's own limit (~254 characters),
# comfortably under this column's `String(320)` -- verified directly against the
# installed email-validator, not assumed. `password` is never stored: only its
# argon2 hash is, in a column of its own, so no column width applies to it here at
# all.
NOME_MAX_LENGTH = 200


class InvitationCreate(BaseModel):
    """The body of `POST /api/users/invites` (spec 2026-09-17 §3). `nome` is optional:
    the acceptance page asks for one when the invitation carried none, so an address
    the admin knows only as an address is invitable. `email` is an `EmailStr` and not
    a `SafeStr` here: like `UserCreate`'s, email-validator refuses a NUL byte (and
    anything over RFC 5321's limit) before it can reach a column, and it is compared
    only against `users.email` and `invitations.email` through this same lowercased
    form."""

    email: EmailStr
    nome: SafeStr | None = Field(default=None, max_length=NOME_MAX_LENGTH)
    ruolo: Role = "collaboratore"

    @field_validator("email", mode="before")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class InvitationRead(BaseModel):
    """What «Inviti in attesa» lists. Never `token_hash`: an open invitation's hash
    is, for every practical purpose, the credential (the same line `PatService` draws
    for its own timeline payloads)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nome: str | None
    ruolo: Role
    invited_by: UUID
    expires_at: datetime
    created_at: datetime


class InvitationPeek(BaseModel):
    """The acceptance page's read before the spend (spec §1): who to thank, which
    space, and whether a name is still missing. Deliberately not a `InvitationRead`:
    the invitee has no business seeing the row's ids or expiry arithmetic."""

    spazio: str
    invitato_da: str
    nome: str | None


class UserCreate(BaseModel):
    email: EmailStr
    # `None` only for a space's first admin, created by the signup wizard (spec
    # 2026-09-12 §6.4): the person enters with a link by mail and never had a password.
    # `UserService.create` refuses it from anyone but the system.
    password: str | None = None
    nome: SafeStr = Field(max_length=NOME_MAX_LENGTH)
    ruolo: Role = "collaboratore"
    tariffa_oraria_default: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES, ge=0
    )
    costo_orario_default: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES, ge=0
    )

    @field_validator("email", mode="before")
    @classmethod
    def _normalise_email(cls, value: str) -> str:
        return value.strip().lower()


class UserUpdate(BaseModel):
    nome: SafeStr | None = Field(default=None, max_length=NOME_MAX_LENGTH)
    ruolo: Role | None = None
    attivo: bool | None = None
    digest_settimanale: bool | None = None
    tariffa_oraria_default: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES, ge=0
    )
    costo_orario_default: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES, ge=0
    )


class MeUpdate(BaseModel):
    """The one field a person may change about their own account with no admin role
    required -- see `UserService.update_own_digest`. Deliberately its own schema
    rather than a reuse of `UserUpdate`: `PATCH /api/auth/me` must never grow a
    second field that only `update`'s `actor.require_admin` was meant to gate."""

    digest_settimanale: bool


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nome: str
    ruolo: Role
    attivo: bool
    digest_settimanale: bool
    tariffa_oraria_default: Decimal | None
    costo_orario_default: Decimal | None
    created_at: datetime
