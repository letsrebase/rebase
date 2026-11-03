from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.validation import SafeStr

AttachmentKind = Literal["invoice_pdf", "register_pdf"]

NAME_MAX_LENGTH = 120
SUBJECT_MAX_LENGTH = 300
BODY_MAX_LENGTH = 20_000
EMAIL_ADDRESS_MAX_LENGTH = 320


class ContractEmailTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    subject: SafeStr = Field(min_length=1, max_length=SUBJECT_MAX_LENGTH)
    body: SafeStr = Field(min_length=1, max_length=BODY_MAX_LENGTH)
    attachments: list[AttachmentKind] = []


class ContractEmailTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    subject: SafeStr | None = Field(default=None, min_length=1, max_length=SUBJECT_MAX_LENGTH)
    body: SafeStr | None = Field(default=None, min_length=1, max_length=BODY_MAX_LENGTH)
    attachments: list[AttachmentKind] | None = None
    active: bool | None = None


class ContractEmailTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    nome: str
    subject: str
    body: str
    attachments: list[AttachmentKind]
    active: bool
    created_at: datetime
    updated_at: datetime


class ContractEmailVariable(BaseModel):
    nome: str
    label: str


class ContractEmailRequest(BaseModel):
    """Which invoice and which template: the register's period is the one the invoice's
    own days span, never typed, so the file and the invoice cannot disagree."""

    model_config = ConfigDict(extra="forbid")

    template_id: UUID
    invoice_id: UUID


class ContractEmailPrepare(ContractEmailRequest):
    to_addresses: list[Annotated[SafeStr, Field(max_length=EMAIL_ADDRESS_MAX_LENGTH)]]


class ContractEmailAttachment(BaseModel):
    type: AttachmentKind
    nome: str


class ContractEmailPreview(BaseModel):
    """What would leave, with nothing written: the rendered subject and body, the
    addresses the customer's people suggest, the files and the period of the register."""

    subject: str
    body: str
    to_suggested: list[str]
    attachments: list[ContractEmailAttachment]
    da: date
    a: date
