from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.validation import SafeStr

AttachmentKind = Literal["fattura_pdf", "registro_pdf"]

NOME_MAX_LENGTH = 120
OGGETTO_MAX_LENGTH = 300
CORPO_MAX_LENGTH = 20_000
EMAIL_ADDRESS_MAX_LENGTH = 320


class ContractEmailTemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(min_length=1, max_length=NOME_MAX_LENGTH)
    oggetto: SafeStr = Field(min_length=1, max_length=OGGETTO_MAX_LENGTH)
    corpo: SafeStr = Field(min_length=1, max_length=CORPO_MAX_LENGTH)
    allegati: list[AttachmentKind] = []


class ContractEmailTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, min_length=1, max_length=NOME_MAX_LENGTH)
    oggetto: SafeStr | None = Field(default=None, min_length=1, max_length=OGGETTO_MAX_LENGTH)
    corpo: SafeStr | None = Field(default=None, min_length=1, max_length=CORPO_MAX_LENGTH)
    allegati: list[AttachmentKind] | None = None
    attivo: bool | None = None


class ContractEmailTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    nome: str
    oggetto: str
    corpo: str
    allegati: list[AttachmentKind]
    attivo: bool
    created_at: datetime
    updated_at: datetime


class ContractEmailVariable(BaseModel):
    nome: str
    etichetta: str


class ContractEmailRequest(BaseModel):
    """Which invoice and which template: the register's period is the one the invoice's
    own days span, never typed, so the file and the invoice cannot disagree."""

    model_config = ConfigDict(extra="forbid")

    template_id: UUID
    invoice_id: UUID


class ContractEmailPrepare(ContractEmailRequest):
    to_addresses: list[Annotated[SafeStr, Field(max_length=EMAIL_ADDRESS_MAX_LENGTH)]]


class ContractEmailAttachment(BaseModel):
    tipo: AttachmentKind
    nome: str


class ContractEmailPreview(BaseModel):
    """What would leave, with nothing written: the rendered subject and body, the
    addresses the customer's people suggest, the files and the period of the register."""

    subject: str
    body: str
    to_suggeriti: list[str]
    allegati: list[ContractEmailAttachment]
    da: date
    a: date
