"""The member area: a person's own card and request, the lookup the site makes and the
magic-link requests."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

from rebase_core.schema_common import ResponseModel, is_complete
from rebase_core.signup_schemas import SignupUtm


class MemberProfile(BaseModel):
    """The row as its owner reads it: what they gave, and nothing the admin wrote.
    No `stato`, no `note`, no attribution, and never the CV bytes."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    cognome: str
    email: str
    linkedin_url: str | None
    # `None` on a card an admin wrote from a signup and the person has not completed
    # yet (ORB-155): no CV, no rate, no position, no remote option until they say so.
    # `cv_filename` and `cv_size` are `None` on a card the wizard made too, where the
    # CV is an optional step.
    cv_filename: str | None
    cv_size: int | None
    tariffa_giornaliera: Decimal | None
    posizione: str | None
    remoto: str | None
    links: list[str]
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completa(self) -> bool:
        return is_complete(self)


class MemberRequest(ResponseModel):
    """One company request as the person who filed it reads it (REB-602): the eight
    answers they gave, plus its id, so the member area can open that very request for
    editing, and when it was filed. Never `stato`, `note` or the company's own name:
    what the admin wrote about a request stays the admin's, the same isolation
    `MemberProfile` keeps for the card."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    figura_richiesta: str
    progetto: str
    periodo_da: date
    durata: str
    budget_giornaliero: Decimal
    remoto: str
    giorni_presenza: int | None
    numero_risorse: int
    created_at: datetime


class MeRead(ResponseModel):
    """Whoever `rebase_user` resolves to, member or admin, replacing `MemberProfile`
    on `GET /me` (REB-278): a `users` row is not necessarily an applicant with a card
    any more, so `ha_scheda` says whether one exists, and the seven card fields answer
    blank -- `None`, `False`, `[]` -- when it does not, the shape a signed-in admin
    with no card now gets. `role` is `member` or `admin` (`USER_ROLES`). `telefono`
    (REB-380) is a top-level identity field like `nome`/`cognome`/`linkedin_url`,
    never blanked by `ha_scheda`/`ha_azienda`: it lives on `users` regardless of
    which of the two a person carries.

    The company side is a list, `richieste` (REB-602): every request the person filed
    that an admin has not soft-deleted, newest first, each one editable by its own id.
    `ha_azienda` is true exactly when the list is not empty, since the wizard and edit
    guards read the flag and not the list. A person can carry a card, requests, both
    or neither."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    cognome: str
    email: str
    linkedin_url: str | None
    telefono: str | None
    role: str
    created_at: datetime
    updated_at: datetime
    ha_scheda: bool
    cv_filename: str | None = None
    cv_size: int | None = None
    tariffa_giornaliera: Decimal | None = None
    posizione: str | None = None
    remoto: str | None = None
    links: list[str] = Field(default_factory=list)
    richieste: list[MemberRequest] = Field(default_factory=list)
    # REB-518: whether any talent cloud grant of this person is live, which puts «Talent
    # cloud» in the member area's nav (`rebase_core.cloud.TalentCloudService.for_user`).
    talent_cloud: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completa(self) -> bool:
        return is_complete(self)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ha_azienda(self) -> bool:
        return bool(self.richieste)


class MemberLookupRequest(BaseModel):
    """The address PigroCRM asks about, in a body and never in the URL: a query string is
    written by every access log and proxy on the way, a body is not. A plain `str`, not
    `EmailStr`: a malformed address is simply not a member's, never an error."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class MemberLookup(ResponseModel):
    """What the hub tells PigroCRM about an address (ORB-173): whether a freelancer with
    it exists, and if so the two names the CRM's signup would otherwise ask for again.
    Never the id, never the rest of the card: the CRM learns that the person is a
    member and how to greet them, nothing about who is at the keyboard."""

    membro: bool
    nome: str | None = None
    cognome: str | None = None


class LinkRequest(BaseModel):
    """The address to send a link to, and since REB-426 the attribution the login page
    arrived with, in the wizards' own shape and bounds: optional, and never a reason for
    the answer to differ between a known address and an unknown one."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    utm: SignupUtm | None = None


class EnterRequest(BaseModel):
    """The raw token from the link. `token_urlsafe(32)` is 43 characters; the bounds
    leave room without accepting a paragraph."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
