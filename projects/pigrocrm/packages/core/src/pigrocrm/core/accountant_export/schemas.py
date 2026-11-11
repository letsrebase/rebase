"""What a stored export looks like to a caller: its files with where to fetch each, never the
bytes (accountant export design § 5)."""

import datetime as dt
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ExportPartRead(BaseModel):
    part: int = Field(description="The part's number, from 1")
    filename: str = Field(description="The ZIP's name")
    size_bytes: int
    sha256: str
    download_url: str = Field(description="The REST path that serves this ZIP")


ExportKind = Literal["accountant", "payroll_changes"]


class ChecklistCheckRead(BaseModel):
    """One check of «Il periodo è pronto» (accountant export design § 2): a count of what waits
    and the first few named. The server computes it; a page draws it and works out nothing."""

    key: str = Field(description="Which check: `inbox`, `purchase_documents`, `invoice_files`, ...")
    label: str = Field(description="The check as the page words it")
    state: Literal["ok", "open"] = Field(description="`ok` when nothing waits, else `open`")
    count: int = Field(description="How many open points there are")
    items: list[str] = Field(description="The first open points, each as a sentence")
    more: int = Field(description="How many open points `items` leaves out")
    target: str = Field(
        description=(
            "Where the point is fixed: `inbox`, `purchases`, `invoices`, `cash`, `iva` or `staff`"
        )
    )


class ChecklistRead(BaseModel):
    period_from: dt.date
    period_to: dt.date
    open_count: int = Field(description="Open points over every check")
    checks: list[ChecklistCheckRead] = Field(
        description="Only the checks that apply to the company's modules and to what is exported"
    )


class ExportRead(BaseModel):
    id: UUID
    legal_entity_id: UUID | None = Field(
        description="The legal entity, `null` for the export of every legal entity"
    )
    scope: str = Field(description="`entity` or `all_entities`")
    kind: ExportKind = Field(
        description="`accountant`, the period bundle, or `payroll_changes`, «variazioni paghe»"
    )
    payroll: bool = Field(
        description="Whether the files carry pay: never delivered on a plain link, only by a code"
    )
    period_from: dt.date
    period_to: dt.date
    built_by: UUID | None
    built_at: dt.datetime
    expires_at: dt.datetime = Field(description="When its files are deleted: 13 months on")
    deleted_at: dt.datetime | None = Field(
        description="When its files were deleted, `null` while they are kept"
    )
    parts: list[ExportPartRead] = Field(
        description="One ZIP, or several when the bundle is too big for one file"
    )
    checklist: dict[str, object] = Field(
        description=(
            "The index as it was when the export was built, and under `readiness` the checklist "
            "of the period as it stood then (absent on an export made before it existed and on "
            "«variazioni paghe»)"
        )
    )


class ExportListRead(BaseModel):
    items: list[ExportRead]
    total_count: int = Field(description="How many stored exports match, whatever the page")


class PartPreviewRead(BaseModel):
    key: str
    label: str = Field(description="The part as the index names it")
    date_rule: str = Field(description="Which date places a row in the period")
    contents: str = Field(description="What the part is made of: a CSV list, original files")
    opt_in: str | None = Field(
        description=(
            "The name an administrator ticks to ask for the part (`payslips`); `null` for a "
            "part every build holds"
        )
    )


class ShareLinkOpeningRead(BaseModel):
    opened_at: dt.datetime
    address: str = Field(description="The client address the file was handed to")
    part: int = Field(description="The number of the ZIP that was opened, from 1")


class ShareLinkRead(BaseModel):
    """A share link as the administrators of the space see it. The address of the link is not
    here: it is shown once, when it is made, and only its hash is kept."""

    id: UUID
    export_id: UUID
    legal_entity_id: UUID | None
    created_at: dt.datetime
    created_by: UUID | None
    expires_at: dt.datetime
    revoked_at: dt.datetime | None
    state: str = Field(description="`active`, `expired` or `revoked`")
    recipient_email: str | None = Field(
        description=(
            "The address a one-time code goes to before the files open; `null` is a plain link"
        )
    )
    download_count: int = Field(description="How many files were handed out through the link")
    period_from: dt.date = Field(description="First day of the export's period")
    period_to: dt.date = Field(description="Last day of the export's period")
    scope: str = Field(description="`entity` or `all_entities`, as the export's")
    openings: list[ShareLinkOpeningRead] = Field(
        description="The latest openings, newest first; empty in a list, filled for one link"
    )


class ShareLinkListRead(BaseModel):
    items: list[ShareLinkRead]
    total_count: int = Field(description="How many links match, whatever the page")


class ShareLinkCreated(ShareLinkRead):
    url: str = Field(
        description=(
            "The address to hand to the accountant. Shown only in this answer: the link is "
            "stored as a hash, so a lost address is revoked and made again"
        )
    )


class SharedExportPartRead(BaseModel):
    part: int
    filename: str
    size_bytes: int


class SharedExportRead(BaseModel):
    """What the person holding a link sees before the download: no figure out of the bundle."""

    company: str = Field(description="The legal entity's name, or «Tutte le aziende»")
    period_from: dt.date
    period_to: dt.date
    built_at: dt.datetime
    shared_by: str | None = Field(description="The name of the person who made the link")
    expires_at: dt.datetime
    requires_code: bool = Field(
        description="Whether a one-time code sent to the recipient opens the files"
    )
    recipient_hint: str | None = Field(
        description="The address the code goes to with most of it hidden, `null` on a plain link"
    )
    unlocked: bool = Field(
        description="Whether the files are open: a plain link, or a code already given"
    )
    parts: list[SharedExportPartRead] = Field(
        description="The ZIP files; empty while a link with a code is locked"
    )


class DriveDeliveryStatus(BaseModel):
    available: bool = Field(description="Whether an export can be written to the space's Drive")
    reason: str | None = Field(
        description=(
            "Why not: `local_storage` when the space keeps its files on the server, "
            "`not_connected` when Drive is the storage but no account and folder are chosen"
        )
    )


class DriveWriteRead(BaseModel):
    id: UUID
    export_id: UUID
    prefix: str = Field(description="The Drive folder, `commercialista/<entity>/<period>/<stamp>`")
    files: list[str] = Field(description="The names of the ZIP files written")
    written_at: dt.datetime
    written_by: UUID | None
