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
