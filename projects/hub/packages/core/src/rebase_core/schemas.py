import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import unquote, urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator

from rebase_core.models import (
    AZIENDA_MAX_LENGTH,
    COMMENT_MAX_LENGTH,
    DURATA_MAX_LENGTH,
    LINKEDIN_URL_MAX_LENGTH,
    NAME_MAX_LENGTH,
    ORIGINE_MAX_LENGTH,
    POSIZIONE_MAX_LENGTH,
    UTM_MAX_LENGTH,
)
from rebase_core.validation import SafeStr

LINKEDIN_HOST = "linkedin.com"
# What the landing sends in `oppref`, bounded to the same 512 characters it bounds it to
# (`orbiters.js`'s `OPPREF_MAX_LENGTH`). Not a column width: nothing stores this.
OPPREF_MAX_LENGTH = 512

# Characters that must never reach a stored value. C0 (tab, CR, LF included), DEL and
# C1 are refused because `urllib.parse` strips tab/CR/LF from a URL *before* parsing
# it, so a value with a newline in it validated as a URL and was then stored whole --
# `.../in/ada\nBcc: qualcuno@altrove.it` was accepted. The same string is later read by
# an assistant drafting mail to these people, which is where a newline stops being
# cosmetic. The bidi controls are refused for the mirror-image reason: they make a
# stored name render as a different name, so what an admin reads is not what is there.
BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def _reject_control_characters(value: str) -> str:
    """Refuses rather than strips, and looks at the value *before* it is trimmed or
    parsed, so nothing invisible is deleted on the way to the column -- the reasoning
    `validation.py` records for `SafeStr`, which closes only `\x00`. Note that Python's
    own `str.strip()` treats `\n`, `\t` and even `\x85` as whitespace, so checking
    after trimming would let a trailing control character disappear in silence."""
    for character in value:
        if character < " " or character == "\x7f" or "\x80" <= character <= "\x9f":
            raise ValueError("il testo contiene un carattere di controllo, non ammesso")
        if character in BIDI_CONTROLS:
            raise ValueError("il testo contiene un carattere di direzione, non ammesso")
    return value


LINKEDIN_PROFILE = "https://www.linkedin.com/in/"
# What an address may be before it is normalised: a share link carries its tracking, so
# the raw value is bounded well above the column and the stored one is checked again.
LINKEDIN_INPUT_MAX_LENGTH = 2000
# A scheme written out (`https://`, `javascript:`), as opposed to an address pasted
# without one (`linkedin.com/in/ada`). No dot in it, so `linkedin.com:443/...` is not
# read as the scheme `linkedin.com`.
_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+-]*:", re.IGNORECASE)
# The name in `/in/<name>`, and whatever LinkedIn appends after it (`/details/`, `/it`).
_PROFILE_PATH = re.compile(r"^/in/([^/]*)(?:/.*)?$")
# What a profile's name is made of once decoded: letters and digits of any script, `-`
# and `_`. Anything else in that segment is not a profile, and is refused rather than
# stored, because the name is interpolated into a link an admin clicks.
_PROFILE_NAME = re.compile(r"[\w-]+")


def normalise_linkedin(value: str | None) -> str | None:
    """The one place a LinkedIn address is checked and given its stored shape.

    Until ORB-203 the value was stored exactly as typed and anything short of
    `https://...linkedin.com/...` was refused, so `linkedin.com/in/ada` pasted from a
    phone was a 422 and `?utm_source=share` reached the admin list. Now a personal
    profile, however it arrives (no scheme, `http`, `it.` or `www.`, a trailing slash,
    query or fragment), is stored as `https://www.linkedin.com/in/<name>`, the name
    percent-decoded, so `jürgen` and `j%C3%BCrgen` are one person. Any other page on
    LinkedIn (a company, an old `/pub/` address) is rebuilt from its host and path over
    `https`. What is not on LinkedIn is still refused, and what is checked is what is
    stored: `urllib.parse` strips tab, CR and LF before parsing, so control characters
    are refused on the raw value first."""
    if value is None or not value.strip():
        return None
    trimmed = _reject_control_characters(value).strip()
    refused = ValueError(f"serve l'indirizzo di un profilo su {LINKEDIN_HOST}, oppure niente")
    # A browser reads `\` as `/` in an https address and `urlsplit` does not, so
    # `https://evil.com\.linkedin.com/x` is LinkedIn here and evil.com in the admin's
    # browser. No LinkedIn address has one.
    if "\\" in trimmed:
        raise refused
    candidate = trimmed if _HAS_SCHEME.match(trimmed) else f"https://{trimmed}"
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()
    # The host is compared, never searched: `https://evil.com/linkedin.com/x` and
    # `https://linkedin.com.evil.com/` are not profiles, and `urlsplit` decides which
    # part of the string is the host. `javascript:...//linkedin.com/` has a scheme of
    # its own and fails here too. Credentials or a port in front of the host are refused
    # rather than carried into a stored link.
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not (host == LINKEDIN_HOST or host.endswith(f".{LINKEDIN_HOST}"))
        or parts.username is not None
        or parts.netloc.lower() != host
    ):
        raise refused
    profile = _PROFILE_PATH.match(parts.path)
    if profile:
        name = unquote(profile.group(1))
        if not _PROFILE_NAME.fullmatch(name):
            raise refused
        stored = LINKEDIN_PROFILE + name
    else:
        stored = urlunsplit(("https", host, parts.path, parts.query, parts.fragment))
    if len(stored) > LINKEDIN_URL_MAX_LENGTH:
        raise ValueError(f"al massimo {LINKEDIN_URL_MAX_LENGTH} caratteri")
    return stored


# The id PostHog gave the browser (`shared/analytics` `distinctId()`), sent with an
# application so the server's completion event lands on the same person (REB-215). An
# opaque string somebody else chose: bounded, never parsed, never stored.
DISTINCT_ID_MAX_LENGTH = 200


class SignupUtm(BaseModel):
    """The attribution the landing read from its own URL, if any. Every key optional and
    bounded: an ad platform's macro left unexpanded (`{{AD_SET_ID}}`) is stored as the
    literal it arrived as, because that is what happened."""

    utm_source: str | None = Field(default=None, max_length=UTM_MAX_LENGTH)
    utm_medium: str | None = Field(default=None, max_length=UTM_MAX_LENGTH)
    utm_campaign: str | None = Field(default=None, max_length=UTM_MAX_LENGTH)
    utm_content: str | None = Field(default=None, max_length=UTM_MAX_LENGTH)
    utm_term: str | None = Field(default=None, max_length=UTM_MAX_LENGTH)
    utm_id: str | None = Field(default=None, max_length=UTM_MAX_LENGTH)
    # The page of the site the person started from (`home`, `pigrocrm`), a slug and
    # nothing else: it is ours, not an ad platform's, so it is held to a shape.
    origine: str | None = Field(
        default=None, max_length=ORIGINE_MAX_LENGTH, pattern=r"^[a-z0-9-]+$"
    )

    def is_empty(self) -> bool:
        return not any(self.model_dump().values())


class SignupCreate(BaseModel):
    # A key this form does not have is a 422, not a value quietly dropped: a landing
    # served from a stale cache posts the body from before nome and cognome existed,
    # and pydantic's default `extra="ignore"` would store that signup without the two
    # fields this form exists to collect, with nothing anywhere saying so.
    model_config = ConfigDict(extra="forbid")

    # `EmailStr` already refuses anything past RFC 5321's ~254 characters, well under
    # the column's String(320) -- the same reasoning `auth/schemas.py` records.
    email: EmailStr
    # Required here even though the columns are nullable: the nulls belong to the rows
    # collected before the form asked, and nothing new is allowed to add one. Bounded to
    # the column, and `SafeStr` closes the NUL-byte gap a plain `str` would leave open.
    nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    # A profile, not any URL: the host has to be LinkedIn's, so a typo or somebody
    # else's site is a 422 and never a stored link nobody can use. A personal profile is
    # stored in one shape, `https://www.linkedin.com/in/<name>`, whatever was pasted
    # (ORB-203): see `normalise_linkedin`.
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    utm: SignupUtm | None = None
    # The two values below are for the conversion event and are **never stored**: no
    # column, no migration, nothing in `SignupListItem`. They travel in this body only
    # because the landing is where both are known, and because `extra="forbid"` above
    # means a key the schema does not declare is a 422 -- so an undeclared field is not
    # an option, and a stored one would be data nobody asked to keep.
    #
    # `pixel_event_id` is the id the landing generated for this submitted form and also
    # passed to `oaiq("measure", ...)`. Deduplication is on (pixel id, event name, id),
    # so the two halves of one conversion are one conversion only if this reaches the
    # server unchanged. Constrained to what an id may be: it is interpolated into a JSON
    # body sent to a third party, and there is no reason for it to contain anything else.
    pixel_event_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{8,64}$")
    # OpenAI's own click identifier, from the landing URL. "Pass unchanged" is their
    # instruction, so it is bounded and checked for control characters and nothing more:
    # its shape is theirs to change, and a value we do not recognise is still the value
    # that arrived.
    oppref: SafeStr | None = Field(default=None, max_length=OPPREF_MAX_LENGTH)

    @field_validator("oppref", mode="after")
    @classmethod
    def _oppref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = _reject_control_characters(value).strip()
        return trimmed or None

    @field_validator("nome", "cognome", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        """Trimmed here rather than in the landing's JavaScript alone: the API is the
        one public write in the CRM, and `"  "` must not become a name of two spaces."""
        trimmed = _reject_control_characters(value).strip()
        if not trimmed:
            raise ValueError("serve un valore, non solo spazi")
        return trimmed

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)


class SignupListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    created_at: datetime
    # `None` only for the rows written before the form asked for a name.
    nome: str | None = None
    cognome: str | None = None
    linkedin_url: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    # The freelancer card with this address, if one exists: what «Iscrizioni» links to
    # (ORB-155). Filled by `SignupService.list_recent` with one join, not a query per row.
    freelancer_id: UUID | None = None
    utm_id: str | None = None


class SignupList(BaseModel):
    """Newest first. `totale` counts the whole list, not just the page returned."""

    totale: int
    iscrizioni: list[SignupListItem]


class SignupRead(BaseModel):
    """The outcome of `SignupService.subscribe`, for its caller inside the process.

    Deliberately carries nothing that was *already* in the row. It used to echo the
    stored `nome`, `cognome`, `linkedin_url` and `utm_*`, and since a repeated signup
    returns the existing row rather than the submitted values, that made the one
    unauthenticated write in the API an oracle: post somebody else's address with any
    name and the answer handed back their real name and LinkedIn profile. The whole row
    is `SignupListItem`, and the only way to it is admin-gated (`list_recent`).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    created_at: datetime
    # `True` the first time an address is seen, `False` when it was already there. Never
    # leaves the process: the router answers `SignupAck`, which does not say which.
    nuova: bool = False


class SignupAck(BaseModel):
    """What the public POST answers, and all it answers: the request was accepted.

    The same body and the same 201 for a first signup and for the hundredth, so the
    reply does not say whether the address was already on the list either. The landing
    needs nothing more -- it branches on the status alone -- and anything more would be
    readable by anyone who can guess an email address.
    """

    ok: bool = True


# ---- the hub proper -------------------------------------------------------------------

Remoto = Literal["remoto", "ibrido", "in_sede"]
FreelancerStato = Literal["nuovo", "contattato", "attivo", "scartato"]
CompanyStato = Literal["nuovo", "contattato", "in_corso", "chiuso"]

# A day's worth of work, in euro. Wide enough for anyone, narrow enough that a typo of
# one extra zero is still a number the admin can read and correct.
TARIFFA_MIN = Decimal("1")
TARIFFA_MAX = Decimal("99999.99")
LINKS_MAX = 10
LINK_MAX_LENGTH = 300
PROGETTO_MAX_LENGTH = 4000


def _clean_text(value: str, *, what: str) -> str:
    trimmed = _reject_control_characters(value).strip()
    if not trimmed:
        raise ValueError(f"serve {what}, non solo spazi")
    return trimmed


def clean_multiline(value: str, *, what: str) -> str:
    """A text a person writes in several lines: newlines and tabs stay, every other
    control character and the bidi overrides are refused as everywhere else, and a
    value that is only whitespace is refused rather than stored as nothing. Shared by
    the company's project description and by the comments, so the API and the MCP
    server agree on what a paragraph may contain."""
    for character in value:
        if character in "\n\r\t":
            continue
        _reject_control_characters(character)
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"serve {what}, non solo spazi")
    return trimmed


def _https_url(value: str) -> str:
    """Any https address: the additional links are the person's own (a site, a GitHub,
    a portfolio), so the host is not checked, only that it is a link worth following."""
    trimmed = _reject_control_characters(value).strip()
    parts = urlsplit(trimmed)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("serve un indirizzo https completo")
    return trimmed


class FreelancerFields(BaseModel):
    """The seven answers the wizard asks for and the person may later change. One set of
    rules for the wizard (`FreelancerCreate`) and the member area (`MemberUpdate`), so
    the two can never accept different things."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    tariffa_giornaliera: Decimal = Field(
        max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )
    posizione: SafeStr = Field(min_length=1, max_length=POSIZIONE_MAX_LENGTH)
    remoto: Remoto
    links: list[SafeStr] = Field(default_factory=list, max_length=LINKS_MAX)

    @field_validator("nome", "cognome", "posizione", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return _clean_text(value, what="un valore")

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("links", mode="after")
    @classmethod
    def _links(cls, value: list[str]) -> list[str]:
        cleaned = [_https_url(link) for link in value if link.strip()]
        if any(len(link) > LINK_MAX_LENGTH for link in cleaned):
            raise ValueError(f"un link può avere al massimo {LINK_MAX_LENGTH} caratteri")
        return cleaned


class FreelancerCreate(FreelancerFields):
    """What the wizard collects. The CV travels beside this body, not inside it: the API
    takes it as a multipart file and hands the bytes to the service with this schema."""

    email: EmailStr
    utm: SignupUtm | None = None


class MemberUpdate(FreelancerFields):
    """What a member changes about themselves: the wizard's answers, never the email
    (it is the identity the link proved) and never the admin's fields."""


FONTI_MAX = 10


class FreelancerDraft(BaseModel):
    """What an admin found about a signup on the public web (ORB-155): a name, maybe a
    LinkedIn profile, a position, some links. The same rules as `FreelancerFields` on
    the same names, with the answers nothing public states -- the rate, the remote
    option, even the position -- optional, and `None` meaning «not found». The CV is
    not here at all: it comes from the person. `fonti` is required and non-empty, because
    a card written from research with no source is a card nobody can check."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    posizione: SafeStr | None = Field(default=None, max_length=POSIZIONE_MAX_LENGTH)
    tariffa_giornaliera: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )
    remoto: Remoto | None = None
    links: list[SafeStr] = Field(default_factory=list, max_length=LINKS_MAX)
    fonti: list[SafeStr] = Field(min_length=1, max_length=FONTI_MAX)

    @field_validator("nome", "cognome", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return _clean_text(value, what="un valore")

    @field_validator("posizione", mode="after")
    @classmethod
    def _trimmed_or_none(cls, value: str | None) -> str | None:
        return None if value is None else _clean_text(value, what="una posizione")

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("links", "fonti", mode="after")
    @classmethod
    def _urls(cls, value: list[str]) -> list[str]:
        return FreelancerFields._links(value)


class CompanyFields(BaseModel):
    """The four answers about a request that its referente may write and later
    change: what `CompanyCreate` collects together with the company's own identity,
    and what `CompanyUpdate` alone accepts once signed in, mirroring
    `FreelancerFields`' split for the freelancer side (REB-314)."""

    model_config = ConfigDict(extra="forbid")

    progetto: SafeStr = Field(min_length=1, max_length=PROGETTO_MAX_LENGTH)
    periodo_da: date
    durata: SafeStr = Field(min_length=1, max_length=DURATA_MAX_LENGTH)
    budget_giornaliero: Decimal = Field(
        max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )

    @field_validator("durata", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return _clean_text(value, what="un valore")

    @field_validator("progetto", mode="after")
    @classmethod
    def _progetto(cls, value: str) -> str:
        """Multi-line is the point of a project description, so newlines stay."""
        return clean_multiline(value, what="una descrizione del progetto")


class CompanyCreate(CompanyFields):
    """What the wizard collects: the four `CompanyFields` answers plus the company's
    own identity and its referente's, get-or-created by email."""

    nome_azienda: SafeStr = Field(min_length=1, max_length=AZIENDA_MAX_LENGTH)
    referente_nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    referente_cognome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    email: EmailStr
    utm: SignupUtm | None = None
    distinct_id: SafeStr | None = Field(default=None, max_length=DISTINCT_ID_MAX_LENGTH)

    @field_validator("nome_azienda", "referente_nome", "referente_cognome", mode="after")
    @classmethod
    def _trimmed_identity(cls, value: str) -> str:
        return _clean_text(value, what="un valore")


class CompanyUpdate(CompanyFields):
    """What a company contact changes about their most recent request (REB-314): the
    four project answers, never `stato`, `note`, `nome_azienda` or the referente's
    identity -- the same field-isolation `MemberUpdate` keeps for the freelancer
    card."""


class Ack(BaseModel):
    """What every public POST of the hub answers, and all it answers: accepted."""

    ok: bool = True


class CommentRead(BaseModel):
    """One entry of a thread, as it was written: who, when, what. Nothing here is ever
    updated, so this is also the whole history of the row's comments."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str
    entity_id: UUID
    testo: str
    autore: str
    created_at: datetime


class CommentCreate(BaseModel):
    """What the admin API takes for a new comment: the text alone. The author is the
    logged-in admin, never a field the client fills in."""

    model_config = ConfigDict(extra="forbid")

    testo: SafeStr = Field(min_length=1, max_length=COMMENT_MAX_LENGTH)


def _is_complete(card: "MemberProfile | FreelancerRead | MeRead") -> bool:
    """CV, rate, position and remote option all there. What «Da completare» in the admin
    area and the notice in the member area read (ORB-155), and what the welcome mailing
    reads before it tells somebody their card is complete. The name and the address are
    never missing, so they are not checked.

    No longer "a card the wizard would have accepted": the wizard accepts one without a
    CV, and this stays the fuller bar, which is the point of having it."""
    return all(
        value is not None
        for value in (card.cv_size, card.tariffa_giornaliera, card.posizione, card.remoto)
    )


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
        return _is_complete(self)


class MeRead(BaseModel):
    """Whoever `orbiters_user` resolves to, member or admin, replacing `MemberProfile`
    on `GET /me` (REB-278): a `users` row is not necessarily an applicant with a card
    any more, so `ha_scheda` says whether one exists, and the seven card fields answer
    blank -- `None`, `False`, `[]` -- when it does not, the shape a signed-in admin
    with no card now gets. `role` is `member` or `admin` (`USER_ROLES`).

    `ha_azienda` and the four request fields mirror `ha_scheda`'s own shape for the
    company side (REB-314): populated from the signed-in person's most recent
    `Company` row when one exists, blank otherwise. A person can carry both, or
    neither, or just one -- the two pairs are independent."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    cognome: str
    email: str
    linkedin_url: str | None
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
    ha_azienda: bool
    progetto: str | None = None
    periodo_da: date | None = None
    durata: str | None = None
    budget_giornaliero: Decimal | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completa(self) -> bool:
        return _is_complete(self)


class MemberLookupRequest(BaseModel):
    """The address PigroCRM asks about, in a body and never in the URL: a query string is
    written by every access log and proxy on the way, a body is not. A plain `str`, not
    `EmailStr`: a malformed address is simply not a member's, never an error."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class MemberLookup(BaseModel):
    """What the hub tells PigroCRM about an address (ORB-173): whether a freelancer with
    it exists, and if so the two names the CRM's signup would otherwise ask for again.
    Never the id, never the rest of the card: the CRM learns that the person is a
    member and how to greet them, nothing about who is at the keyboard."""

    membro: bool
    nome: str | None = None
    cognome: str | None = None


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class EnterRequest(BaseModel):
    """The raw token from the link. `token_urlsafe(32)` is 43 characters; the bounds
    leave room without accepting a paragraph."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")


class FreelancerRead(BaseModel):
    """The row as an admin reads it. Never the CV bytes: those have their own download,
    so a list of two hundred people is not two hundred PDFs in one response.

    `commenti` is the thread, newest first, and only `get` fills it: a list of two
    hundred people is not two hundred threads either, so the list leaves it empty."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    cognome: str
    email: str
    linkedin_url: str | None
    # `None` on a card born from a signup that the person has not completed (ORB-155),
    # and on one whose owner skipped the wizard's optional CV step.
    cv_filename: str | None
    cv_mime: str | None
    cv_size: int | None
    tariffa_giornaliera: Decimal | None
    posizione: str | None
    remoto: str | None
    links: list[str]
    stato: str
    note: str | None
    # Who wrote the seven answers last: `persona` or `admin` (`COMPILATA_DA`).
    compilata_da: str
    # How many times the person entered through a magic link, and the last time (ORB-158).
    # Filled by the service with one grouped join; `0` and `None` for a card never opened.
    accessi: int = 0
    ultimo_accesso: datetime | None = None
    # Where the lead came from, in Ivan's words (ORB-161): «form» when the address also
    # left its email on the landing («Iscrizioni»), «landing» otherwise. Derived by the
    # service from the two tables, never stored, so it cannot drift.
    provenienza: str = "landing"
    origine: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    utm_id: str | None = None
    created_at: datetime
    updated_at: datetime
    # `None` while the card is live; a moment once an admin soft-deletes it (REB-347).
    deleted_at: datetime | None = None
    commenti: list[CommentRead] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completa(self) -> bool:
        return _is_complete(self)


class FreelancerList(BaseModel):
    """The cards, and since ORB-163 the leads beside them: signups whose address has no
    card yet, as «Developer e CTO» shows them with a «Lead» state. `totale` counts the
    cards the filter selects, `totale_lead` the leads; `lead` is empty when a `stato`
    other than «lead» is asked for, `items` when «lead» is."""

    totale: int
    items: list[FreelancerRead]
    totale_lead: int = 0
    lead: list[SignupListItem] = Field(default_factory=list)


TalentoOrigine = Literal["form", "wizard", "admin"]


class TalentoRead(BaseModel):
    """One row of `talenti` (REB-282): every freelancer card and every bare sign-up (a
    `signups` row with no card, ORB-163) as one row, `stato` `lead` for the bare ones
    and the freelancer's own state otherwise. `origine` names how the row came to be --
    `form` for a bare sign-up (the landing's own sign-up form), `wizard` for a card the
    person filled in themselves (`compilata_da == "persona"`), `admin` for one an admin
    drafted from research (`compilata_da == "admin"`, ORB-155). Distinct from
    `Freelancer.origine`/`Signup`'s own UTM columns, which name the page and the
    campaign a submission started from, not the channel that created the row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str | None = None
    cognome: str | None = None
    email: str
    linkedin_url: str | None = None
    stato: str
    origine: TalentoOrigine
    utm_source: str | None = None
    created_at: datetime


class TalentoList(BaseModel):
    """Newest-or-best-match first across both tables (REB-282; REB-285 adds search and
    the cursor), at most `limit` rows. `totale` counts every row every active filter
    selects, `stato` included, not only the page returned; `per_stato` is the same
    count broken down by state with every filter but `stato` applied -- the numbers
    the admin area's tabs need beside the page itself, answering "how many if I picked
    this one" rather than "how many exist at all". `next_cursor` is `None` on the last
    page."""

    totale: int
    items: list[TalentoRead]
    per_stato: dict[str, int]
    next_cursor: str | None = None


class CompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome_azienda: str
    referente: str
    email: str
    progetto: str
    periodo_da: date
    durata: str
    budget_giornaliero: Decimal
    stato: str
    note: str | None
    origine: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    utm_id: str | None = None
    created_at: datetime
    updated_at: datetime
    # Same soft-delete as `FreelancerRead.deleted_at`.
    deleted_at: datetime | None = None
    # The thread, newest first; filled by `get` only, as on `FreelancerRead`.
    commenti: list[CommentRead] = Field(default_factory=list)


class CompanyList(BaseModel):
    """Newest-or-best-match first (REB-285 adds search, filters and the cursor beside
    `stato`). `totale` and `per_stato` follow `TalentoList`'s own reasoning; both are
    additive to the shape `GET /api/hub/companies` already answered, so an older caller
    that ignores unknown fields sees nothing change."""

    totale: int
    items: list[CompanyRead]
    per_stato: dict[str, int]
    next_cursor: str | None = None


class StatusChange(BaseModel):
    """What an admin changes on a row: where it stands, and a note to themselves."""

    stato: str = Field(min_length=1, max_length=20)
    note: SafeStr | None = Field(default=None, max_length=PROGETTO_MAX_LENGTH)


class FreelancerOverride(BaseModel):
    """What an admin may set or clear on a `Freelancer` beyond `stato`/`note`
    (REB-347): every field a person could have written through the wizard or the
    member area, plus the three identity fields (`nome`/`cognome`/`linkedin_url`)
    that live on the linked `users` row since REB-281, and `compilata_da`, which
    nothing else lets an admin touch by hand.

    Every field is optional, since one call changes only the ones it names --
    `rebase_core.audit.supplied_changes` reads which ones that is from
    `model_fields_set`, never from whether the value is `None`: an explicit `null`
    clears a nullable column and is refused on a `NOT NULL` one by
    `rebase_core.audit.reject_cleared_columns`. The CV has its own route
    (`FreelancerService.clear_cv`, clear-only): its bytes are personal data an audit
    payload must never carry, so it is not part of this schema at all."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    tariffa_giornaliera: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )
    posizione: SafeStr | None = Field(default=None, max_length=POSIZIONE_MAX_LENGTH)
    remoto: Remoto | None = None
    links: list[SafeStr] | None = Field(default=None, max_length=LINKS_MAX)
    stato: FreelancerStato | None = None
    note: SafeStr | None = Field(default=None, max_length=PROGETTO_MAX_LENGTH)
    compilata_da: Literal["persona", "admin"] | None = None

    @field_validator("nome", "cognome", "posizione", mode="after")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        return _clean_text(value, what="un valore") if value is not None else None

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("links", mode="after")
    @classmethod
    def _links(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [_https_url(link) for link in value if link.strip()]
        if any(len(link) > LINK_MAX_LENGTH for link in cleaned):
            raise ValueError(f"un link può avere al massimo {LINK_MAX_LENGTH} caratteri")
        return cleaned


class CompanyOverride(BaseModel):
    """What an admin may set or clear on a `Company` request beyond `stato`/`note`
    (REB-347): the four project answers a referente could have written
    (`CompanyFields`), the company's own name, and the referente's identity fields on
    the linked `users` row -- both admin-only even for a self-edit, as
    `MemberService.update_company`'s own docstring says. Same optional-and-`null`
    contract as `FreelancerOverride`."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    nome_azienda: SafeStr | None = Field(default=None, max_length=AZIENDA_MAX_LENGTH)
    progetto: SafeStr | None = Field(default=None, max_length=PROGETTO_MAX_LENGTH)
    periodo_da: date | None = None
    durata: SafeStr | None = Field(default=None, max_length=DURATA_MAX_LENGTH)
    budget_giornaliero: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )
    stato: CompanyStato | None = None
    note: SafeStr | None = Field(default=None, max_length=PROGETTO_MAX_LENGTH)

    @field_validator("nome", "cognome", "nome_azienda", "durata", mode="after")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        return _clean_text(value, what="un valore") if value is not None else None

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("progetto", mode="after")
    @classmethod
    def _progetto(cls, value: str | None) -> str | None:
        return (
            clean_multiline(value, what="una descrizione del progetto")
            if value is not None
            else None
        )


class GuideDownloadRead(BaseModel):
    """One download, with the member's name for the admin's list."""

    id: UUID
    user_id: UUID
    nome: str
    cognome: str
    email: str
    downloaded_at: datetime


class LoginRead(BaseModel):
    """One login, with the member's name for the admin's list (ORB-158)."""

    id: UUID
    user_id: UUID
    nome: str
    cognome: str
    email: str
    logged_at: datetime


class LoginStats(BaseModel):
    """The logins as the admin area reads them (ORB-158), the shape of `GuideStats`:
    `totale` every login, `membri` the distinct people behind them, `membri_totali`
    everybody on file, `ultimi_7_giorni` the last week -- four counters unaffected by
    `q` (REB-313), always read off the whole table. `recenti` is the searched, paged
    part: newest first with no term, best-match first once `q` narrows it by name or
    email, no longer capped at 20. `next_cursor` is `None` on the last page, additive
    to the shape `GET /api/hub/logins` already answered."""

    totale: int
    membri: int
    membri_totali: int
    ultimi_7_giorni: int
    recenti: list[LoginRead]
    next_cursor: str | None = None


class GuideStats(BaseModel):
    """The guide's numbers for the admin area (ORB-156). `totale` counts every download,
    `membri` the distinct people behind them, `membri_totali` everybody who could have
    (the freelancers on file), `ultimi_7_giorni` the downloads of the last week, and
    `recenti` the latest ones, newest first, with a name each."""

    totale: int
    membri: int
    membri_totali: int
    ultimi_7_giorni: int
    recenti: list[GuideDownloadRead]


class FreelancerDetail(FreelancerRead):
    """`FreelancerRead` plus every other place the hub already knows this address
    (REB-284): the sign-up's own UTM set (`iscrizione_utm`, never the card's own utm
    fields above -- an admin-drafted card copies the signup's UTM at creation but a
    wizard card carries its own, and the two can differ), the last handful of logins
    and guide downloads, and the PigroCRM space slug when the address owns one. Only
    `FreelancerService.get` fills these: the list stays `FreelancerRead` alone, since
    two hundred people are not two hundred fan-outs to four sources."""

    iscrizione_utm: SignupUtm | None = None
    ultimi_accessi: list[LoginRead] = Field(default_factory=list)
    ultimi_download_guida: list[GuideDownloadRead] = Field(default_factory=list)
    pigro_slug: str | None = None


class CvFile(BaseModel):
    """The bytes and the two headers a download needs."""

    filename: str
    mime: str
    content: bytes
