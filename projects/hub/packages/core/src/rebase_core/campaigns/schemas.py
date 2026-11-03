"""Every shape campaigns read and answer, API and service alike."""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    ValidationInfo,
    computed_field,
    field_validator,
)
from pydantic_core import PydanticCustomError

from rebase_core.models import (
    CAMPAIGN_BUTTON_MAX_LENGTH,
    CAMPAIGN_NAME_MAX_LENGTH,
    CAMPAIGN_SUBJECT_MAX_LENGTH,
    CAMPAIGN_TEXT_MAX_LENGTH,
)
from rebase_core.schema_common import ResponseModel
from rebase_core.search import SEARCH_MAX_LENGTH
from rebase_core.vocabulary import (
    CampaignAction,
    CampaignDestination,
    CampaignSource,
    CampaignState,
    RecipientKind,
    RecipientState,
)

# A filter's amount is kept like every other amount the hub stores: euro with cents at
# most and never below zero, with no ceiling the editor's own field lacks. The editor
# sends two decimals (REB-485) and reads a stored one back with two decimals, which is
# exact only because nothing here keeps a third (REB-526, Greptile on #432).
FilterAmount = Annotated[Decimal, Field(ge=0, decimal_places=2)]


class TalentsFilters(ResponseModel):
    """The Talenti list's own filters (`GET /api/hub/talent`, `talents.py:191`), stored
    on the campaign and replayed through `TalentsService` when the list is shown and when
    a mail is about to leave. `origin` is the marketing page the person started from
    (`home`, `pigrocrm`), stored under that key since before the list named it
    `origin_page` (REB-798); `audience._from_talents` translates it on replay."""

    list: Literal["talents"]
    status: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    posizione: str | None = Field(default=None, max_length=160)
    remote: str | None = Field(default=None, max_length=10)
    min_rate: FilterAmount | None = None
    max_rate: FilterAmount | None = None
    origin: str | None = Field(default=None, max_length=40)
    utm_source: str | None = Field(default=None, max_length=200)
    has_cv: bool | None = None
    with_logins: bool | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


class CompaniesFilters(ResponseModel):
    """The company list's own filters (`companies.py:160`)."""

    list: Literal["companies"]
    status: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    budget_min: FilterAmount | None = None
    budget_max: FilterAmount | None = None
    period_from: date | None = None
    origin: str | None = Field(default=None, max_length=40)
    created_from: datetime | None = None
    created_to: datetime | None = None


Filters = Annotated[TalentsFilters | CompaniesFilters, Field(discriminator="list")]

NAME_IS_BLANK = "Il nome della campagna non può essere vuoto."


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _not_blank(value: str) -> str:
    if not value:
        # A custom error, not a `ValueError`: its message reaches the page as it
        # stands, with no «Value error,» in front of the sentence.
        raise PydanticCustomError("nome_vuoto", NAME_IS_BLANK)
    return value


# Stripped first, then checked (REB-524): `min_length=1` on the raw value let «   »
# through, and the service's own strip stored it empty. `max_length` is checked on the
# stripped name too.
CampaignName = Annotated[
    str,
    BeforeValidator(_strip),
    Field(max_length=CAMPAIGN_NAME_MAX_LENGTH),
    AfterValidator(_not_blank),
]

Action = Literal[
    "signed_in",
    "cv",
    "card_complete",
    "profile_created",
    "request_updated",
    "pigro_customer",
    "click",
]
Meta = Literal["area", "wizard", "pigro", "request", "link"]


class CampaignDraft(BaseModel):
    nome: CampaignName
    source: Literal["status", "filters"]
    journey_status: str | None = Field(default=None, max_length=30)
    filters: Filters | None = None
    subject: str = Field(default="", max_length=CAMPAIGN_SUBJECT_MAX_LENGTH)
    text: str = Field(default="", max_length=CAMPAIGN_TEXT_MAX_LENGTH)
    button_text: str = Field(default="", max_length=CAMPAIGN_BUTTON_MAX_LENGTH)
    button_meta: Meta
    # With «Un link» only (REB-530). The service checks it with the destination and the
    # action, in Italian, since whether it may be there depends on both.
    button_url: str | None = None
    action: Action


# The `CampaignPatch` fields whose column is `NOT NULL` in `models.py`'s `Campaign`:
# `None` on one of them is how a field is typed here (so leaving it out of the request
# means "no change"), never a value the row can hold. Left unchecked, an explicit
# `null` on one reaches `CampaignService.update`'s blanket `setattr` and raises an
# `IntegrityError`, a 500, instead of a sentence naming the field. `journey_status` and
# `filters` are not here: the row legitimately holds `NULL` in one of them, depending on
# `source`.
_NOT_NULLABLE = ("nome", "source", "subject", "text", "button_text", "button_meta", "action")


class CampaignPatch(BaseModel):
    nome: CampaignName | None = None
    source: Literal["status", "filters"] | None = None
    journey_status: str | None = Field(default=None, max_length=30)
    filters: Filters | None = None
    subject: str | None = Field(default=None, max_length=CAMPAIGN_SUBJECT_MAX_LENGTH)
    text: str | None = Field(default=None, max_length=CAMPAIGN_TEXT_MAX_LENGTH)
    button_text: str | None = Field(default=None, max_length=CAMPAIGN_BUTTON_MAX_LENGTH)
    button_meta: Meta | None = None
    # `null` is a value here, not «no change»: the address of a button that no longer
    # leads to a link. Left out, it follows the destination (`CampaignService.update`).
    button_url: str | None = None
    action: Action | None = None

    @field_validator(*_NOT_NULLABLE, mode="after")
    @classmethod
    def _no_explicit_null_on_a_required_field(cls, value: object, info: ValidationInfo) -> object:
        """The database's own `NOT NULL` columns, enforced here too, the same way
        `CompanyFields._onsite_days_matches_remote` enforces a check constraint: a
        sentence naming the field rather than the `IntegrityError` a blanket `setattr`
        would otherwise reach. A `field_validator`, not a `model_validator`, so the
        error's `loc` ends in the field's own name: the hub web reads `ApiError.fields`
        from `detail[].loc[-1]` (`apps/web/src/lib/api.ts`), and a body-level `loc`
        pointed at nothing a wizard could show. Pydantic skips a default value's own
        validators (`validate_default` is off, the default here), so this never runs
        for a field the request left out -- only for one it set, `null` included, which
        is exactly the distinction "no change" needs."""
        if value is None:
            raise ValueError(f"{info.field_name}: il campo non può essere svuotato")
        return value


# An address as the list holds it, not as `EmailStr` would have it: the unticked are
# only compared with the list's own lowercase addresses, never mailed, so a legacy
# address `EmailStr` rejects must not 422 the whole send.
Unticked = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=320)]


class ScheduleRequest(BaseModel):
    """`day` and `ora` are Europe/Rome wall-clock time, both or neither; neither is
    «Invia adesso». `esclusi` are the addresses the admin unticked on step 1."""

    day: date | None = None
    ora: time | None = None
    excluded: list[Unticked] = []


class NeverWriteRequest(BaseModel):
    email: EmailStr


class CampaignCounts(ResponseModel):
    recipients: int = 0
    in_coda: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    delivered: int = 0
    bounced: int = 0
    clicked: int = 0
    income: int = 0
    actions: int = 0


class CampaignRead(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    source: CampaignSource
    journey_status: str | None
    filters: Filters | None
    segue_id: UUID | None
    subject: str
    text: str
    button_text: str
    button_meta: CampaignDestination
    button_url: str | None = None
    action: CampaignAction
    status: CampaignState
    content_at: datetime
    scheduled_for: datetime | None
    test_sent_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    # REB-524: set while a send is stopped on something that will not fix itself; the
    # page reads «Invio fermo: <fermo_motivo>».
    stalled_at: datetime | None
    stalled_reason: str | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        """A test has left since the last edit: what enables «Invia» (Review Focus 1)."""
        return self.test_sent_at is not None and self.test_sent_at >= self.content_at


class CampaignListItem(CampaignRead):
    counts: CampaignCounts


class CampaignList(ResponseModel):
    items: list[CampaignListItem]
    # Set only when a caller asked for a page (the MCP tools, REB-804): the admin screen
    # reads the whole list.
    next_cursor: str | None = None


class RecipientRead(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nome: str | None
    kind: RecipientKind
    status: RecipientState
    reason: str | None
    sent_at: datetime | None
    delivered_at: datetime | None
    bounced_at: datetime | None
    first_click_at: datetime | None
    complaint_at: datetime | None
    signed_in_at: datetime | None
    action_at: datetime | None
    # Read at detail time, never stored: the login or the card carries this campaign's
    # link (REB-426), so the mail was the door (spec § 4.3).
    signed_in_from_mail: bool = False
    action_from_mail: bool = False


class CampaignDetail(ResponseModel):
    campaign: CampaignRead
    counts: CampaignCounts
    recipients: list[RecipientRead]
    # The cursor after the last recipient listed, when a page was asked for (REB-804).
    next_cursor: str | None = None


class AudienceRowRead(ResponseModel):
    email: str
    nome: str | None
    kind: RecipientKind
    excluded: str | None


class AudiencePreview(ResponseModel):
    lines: list[AudienceRowRead]
    included: int
    excluded: int


class TemplateRead(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    journey_status: str
    label: str
    subject: str
    text: str
    button_text: str
    button_meta: CampaignDestination
    action: CampaignAction
