"""What the root door answers the hub about a freelancer, by the hub's own freelancer id
(collaborators design § 5, «What the hub reads from the CRM through the door»).

Every list is empty, and every identifier null, for a freelancer this installation holds no
organization for: the hub asks about every card it has, and a card the CRM has never heard of is
an answer, not a missing page. The vocabulary is the CRM's (`framework_agreement`,
`engagement_letter`, `ordinary`); the hub maps it onto its own at the seam.
"""

import datetime as dt
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from pigrocrm.core.collaborators.schemas import AvailabilityWeek, SignatureState


class DoorAvailability(BaseModel):
    """`FreelancerAvailability` for a freelancer who may be unknown here: `freelancer_id` is the
    organization's, null when the CRM holds none, and then every week is `free`."""

    freelancer_id: UUID | None
    hub_freelancer_id: UUID
    date_from: dt.date
    date_to: dt.date
    weeks: list[AvailabilityWeek]


class DoorDocument(BaseModel):
    """One document as its freelancer reads it in the hub's «Contratti»: never the PDF's bytes and
    never the engagement's fee. `customer`, `start` and `end` are a letter's, the dates as the
    letter prints them (`1° ottobre 2026`); `signing_url` is there only while the document waits
    for the signature, since its path is the signer's token. `pigro_url` is the engagement's
    deal in the freelancer's own space, once the door wrote it."""

    id: UUID
    kind: str = Field(description="framework_agreement | engagement_letter")
    letter_type: str = Field(description="ordinary | occasional")
    number: str | None
    state: SignatureState
    customer: str | None
    start: str | None
    end: str | None
    sent_at: datetime | None
    signed_at: datetime | None
    signing_url: str | None
    has_signed_copy: bool
    filename: str | None = Field(description="What the signed copy is saved under")
    active: bool = Field(description="A framework agreement that is signed and has no notice")
    renewal: dt.date | None
    last_notice_day: dt.date | None
    pigro_url: str | None


class DoorContracts(BaseModel):
    """The documents that reached the freelancer, newest first: every one that went out for
    signature, and a letter waiting for its framework agreement once its engagement was sent. A
    draft engagement, and a document cancelled before it left, are the admin's business."""

    documents: list[DoorDocument]


class DoorFiscalIdentifiers(BaseModel):
    """What a freelancer's identity papers print, for the anonymous CV's redaction list. Archived
    organizations answer too: a CV must never carry what an archived record held."""

    tax_code: str | None
    vat_number: str | None
    certified_email: str | None


class DoorMergeCheck(BaseModel):
    """What stops two hub cards being merged, as the CRM sees the two freelancers.
    `framework_regimes` are the letter types both hold a live framework agreement of (out for
    signature, or signed with no notice), `shared_clients` how many clients both have an
    engagement with that is out for signature or active, and `tax_code_conflict` that both
    organizations carry a tax code and the codes differ. `organization` and `other_organization`
    say whether the CRM holds an organization for the first and for the other hub id: a hub
    merge does not re-key it, so the card that is absorbed must have none."""

    framework_regimes: list[str]
    shared_clients: int
    tax_code_conflict: bool
    organization: bool
    other_organization: bool
