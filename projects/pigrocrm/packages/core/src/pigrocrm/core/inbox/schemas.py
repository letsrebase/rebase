"""The shapes the inbox answers with. No shape carries bytes or the storage key: the bytes leave
through `InboxService.download` only, and never over MCP (design § 6)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.inbox.filetypes import FileType

InboxItemState = Literal["new", "proposed", "filed", "duplicate", "discarded"]
InboxItemKind = Literal[
    "sales_invoice",
    "supplier_invoice",
    "credit_note",
    "receipt",
    "bank_statement",
    "contract",
    "tax_document",
    "payslip",
    "archive",
    "other",
]


class InboxItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: Literal["upload"]
    original_name: str
    file_type: FileType | None
    size_bytes: int
    has_file: bool = Field(description="Whether the item still holds the bytes of the file")
    sha256: str
    content_sha256: str = Field(
        description="SHA-256 of the document the file carries: the XML inside, for a `.p7m`"
    )
    natural_key: str | None = Field(
        description="Digest of issuer, number, date and type of a FatturaPA's invoices"
    )
    parent_id: UUID | None = Field(description="The ZIP this file was unpacked from")
    uploaded_by_id: UUID | None
    legal_entity_id: UUID | None
    kind: InboxItemKind | None
    state: InboxItemState
    duplicate_of_id: UUID | None
    reason: str | None
    created_at: datetime
    updated_at: datetime


class InboxUploadResult(BaseModel):
    """Every item the request made, in the order it was read: a ZIP, then its entries."""

    items: list[InboxItemRead]


class InboxListQuery(BaseModel):
    state: InboxItemState | None = None
    kind: InboxItemKind | None = None
    parent_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class InboxItemPage(BaseModel):
    items: list[InboxItemRead]
    next_cursor: UUID | None
