"""The public signup (the landing's list) and the UTM attribution every public form carries."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from rebase_core.linkedin import normalise_linkedin, reject_control_characters
from rebase_core.models import NAME_MAX_LENGTH, ORIGIN_MAX_LENGTH, UTM_MAX_LENGTH
from rebase_core.schema_common import LINKEDIN_INPUT_MAX_LENGTH, ResponseModel, clean_ref
from rebase_core.validation import SafeStr

# What the landing sends in `oppref`, bounded to the same 512 characters it bounds it to
# (the community page's script, gone with the page in REB-72). Not a column width:
# nothing stores this.
OPPREF_MAX_LENGTH = 512


class SignupUtm(ResponseModel):
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
    origin: str | None = Field(default=None, max_length=ORIGIN_MAX_LENGTH, pattern=r"^[a-z0-9-]+$")

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
    last_name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    # A profile, not any URL: the host has to be LinkedIn's, so a typo or somebody
    # else's site is a 422 and never a stored link nobody can use. A personal profile is
    # stored in one shape, `https://www.linkedin.com/in/<name>`, whatever was pasted
    # (ORB-203): see `normalise_linkedin`.
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    utm: SignupUtm | None = None
    # `?ref=` from the visitor's link (REB-554), shaped like the wizards' own: a string
    # that is not a code is no code, never a 422 and never a hint of which codes exist.
    # Stored on the signup and credited only when an admin drafts a card from it.
    ref: SafeStr | None = None
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
    # Whether this visitor said yes to the measurement cookie notice (`consent.js` keeps
    # the decision in `localStorage["rebase.consent"]`, `"granted"` or `"denied"`, and
    # loads the browser pixel only on a yes). The server half of the conversion sends the
    # visitor's address, user agent and click ids to OpenAI, so it follows the browser
    # half: it is sent only when this is `true`. Absent means no: a client that does not
    # know about the notice is never measured, and neither is a refusal.
    pixel_consent: bool = False

    @field_validator("ref", mode="before")
    @classmethod
    def _ref(cls, value: object) -> str | None:
        return clean_ref(value)

    @field_validator("oppref", mode="after")
    @classmethod
    def _oppref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = reject_control_characters(value).strip()
        return trimmed or None

    @field_validator("nome", "last_name", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        """Trimmed here rather than in the landing's JavaScript alone: the API is the
        one public write in the CRM, and `"  "` must not become a name of two spaces."""
        trimmed = reject_control_characters(value).strip()
        if not trimmed:
            raise ValueError("serve un valore, non solo spazi")
        return trimmed

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)


class SignupListItem(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    created_at: datetime
    # `None` only for the rows written before the form asked for a name.
    nome: str | None = None
    last_name: str | None = None
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


class SignupList(ResponseModel):
    """Newest first. `total` counts the whole list, not just the page returned."""

    total: int
    signups: list[SignupListItem]


class SignupRead(BaseModel):
    """The outcome of `SignupService.subscribe`, for its caller inside the process.

    Deliberately carries nothing that was *already* in the row. It used to echo the
    stored `nome`, `last_name`, `linkedin_url` and `utm_*`, and since a repeated signup
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


class SignupAck(ResponseModel):
    """What the public POST answers, and all it answers: the request was accepted.

    The same body and the same 201 for a first signup and for the hundredth, so the
    reply does not say whether the address was already on the list either. The landing
    needs nothing more -- it branches on the status alone -- and anything more would be
    readable by anyone who can guess an email address.
    """

    ok: bool = True
