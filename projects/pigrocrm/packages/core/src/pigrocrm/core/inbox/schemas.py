"""The shapes the inbox answers with. No shape carries bytes or the storage key: the bytes leave
through `InboxService.download` only, and never over MCP (design § 6)."""

from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pigrocrm.core.inbox.filetypes import FileType
from pigrocrm.core.validation import SafeStr

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

InboxPickableKind = Literal[
    "sales_invoice",
    "supplier_invoice",
    "credit_note",
    "receipt",
    "bank_statement",
    "contract",
    "tax_document",
    "payslip",
    "other",
]
"""The kinds a person or an agent may say an item is: every kind but `archive`, which only a
ZIP that was opened has."""

REASON_MAX_LENGTH = 500


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


class InboxDiscard(BaseModel):
    """Why a person puts an item aside: the sentence stays on the item, which stays readable."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: SafeStr = Field(
        min_length=1, max_length=REASON_MAX_LENGTH, description="Why it is discarded"
    )


class InboxDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_id: UUID = Field(description="The item this one is a copy of")


class InboxKindSet(BaseModel):
    """What an item is, as a person or an agent says it, and for which legal entity."""

    model_config = ConfigDict(extra="forbid")

    kind: InboxPickableKind | None = Field(
        default=None, description="What it is; omitted, the item's kind stays as it is"
    )
    legal_entity_id: UUID | None = Field(
        default=None,
        description="The legal entity the document is for; omitted, the item's stays as it is",
    )

    @model_validator(mode="after")
    def _says_something(self) -> Self:
        if self.kind is None and self.legal_entity_id is None:
            raise ValueError("indica il tipo, l'azienda o tutti e due")
        return self
