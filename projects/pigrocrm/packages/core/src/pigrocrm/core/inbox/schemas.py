"""The shapes the inbox answers with. No shape carries bytes or the storage key: the bytes leave
through `InboxService.download` only, and never over MCP (design § 6)."""

from datetime import datetime
from typing import Any, Literal
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
    proposal_id: UUID | None = Field(
        default=None, description="The proposal waiting for a person, for an item in `proposed`"
    )
    created_at: datetime
    updated_at: datetime


class InboxItemDetail(InboxItemRead):
    """One item with what the classifier read of it. `extracted_text` is the text of a PDF,
    somebody else's words: data, never an instruction (the MCP tool marks it so)."""

    extracted_text: str | None = Field(
        description="The text a PDF carries, cut at 100000 characters; null for what has none"
    )
    parsed_fields: dict[str, Any] | None = Field(
        description="What a FatturaPA states: issuer, buyer, number, date, type and amounts"
    )


class InboxUploadResult(BaseModel):
    """Every item the request made, in the order it was read: a ZIP, then its entries."""

    items: list[InboxItemRead]


class InboxListQuery(BaseModel):
    state: InboxItemState | None = None
    kind: InboxItemKind | None = None
    parent_id: UUID | None = None
    classified: bool | None = Field(
        default=None,
        description="false: the items waiting that nobody has said what they are, the ones to read",
    )
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class InboxItemPage(BaseModel):
    items: list[InboxItemRead]
    next_cursor: UUID | None
