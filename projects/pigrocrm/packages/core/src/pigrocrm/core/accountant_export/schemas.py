"""What a stored export looks like to a caller: its files with where to fetch each, never the
bytes (accountant export design § 5)."""

import datetime as dt
from uuid import UUID

from pydantic import BaseModel, Field


class ExportPartRead(BaseModel):
    part: int = Field(description="The part's number, from 1")
    filename: str = Field(description="The ZIP's name")
    size_bytes: int
    sha256: str
    download_url: str = Field(description="The REST path that serves this ZIP")


class ExportRead(BaseModel):
    id: UUID
    legal_entity_id: UUID | None = Field(
        description="The legal entity, `null` for the export of every legal entity"
    )
    scope: str = Field(description="`entity` or `all_entities`")
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
        description="The index as it was when the export was built"
    )


class ExportListRead(BaseModel):
    items: list[ExportRead]
    total_count: int = Field(description="How many stored exports match, whatever the page")


class PartPreviewRead(BaseModel):
    key: str
    label: str = Field(description="The part as the index names it")
    date_rule: str = Field(description="Which date places a row in the period")
    contents: str = Field(description="What the part is made of: a CSV list, original files")


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
    parts: list[SharedExportPartRead]


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
