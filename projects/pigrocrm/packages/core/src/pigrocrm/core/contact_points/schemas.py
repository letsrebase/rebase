from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.contact_points.models import NOTE_MAX_LENGTH, VALUE_MAX_LENGTH
from pigrocrm.core.validation import SafeStr

ContactKind = Literal["email", "phone", "linkedin"]
ContactLabel = Literal["work", "personal", "billing", "other"]
# Generous for a person and a company, and a bound for an agent: a list is replaced whole.
MAX_CONTACT_POINTS = 30


class ContactPointIn(BaseModel):
    """One contact point as written. A write sends the whole list, which replaces the stored
    one. At most one point per kind is `is_primary`; when a kind has points and none is
    flagged, the first of that kind becomes the primary. The primary email is the address the
    CRM's mail goes to."""

    model_config = ConfigDict(extra="forbid")

    kind: ContactKind
    value: SafeStr = Field(max_length=VALUE_MAX_LENGTH)
    label: ContactLabel = "work"
    note: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    is_primary: bool = False


class ContactPointRead(ContactPointIn):
    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: UUID
    position: int
