"""What a mailbox looks like to a person and to a tool (REB-1015). There is no field for the
password, sealed or not, in any of them: nothing returns it and nothing here could."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.mailboxes.health import MailboxHealth

Security = Literal["tls", "starttls"]
Purpose = Literal["correspondence", "billing"]


class ImapMailboxCreate(BaseModel):
    """Connecting a mailbox. `space_owned` is the space's shared address (fatture@, a PEC),
    connected by an administrator; without it the mailbox is the caller's own."""

    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=3, max_length=320, description="The mailbox's own address.")
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    security: Security
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512, repr=False)
    purposes: list[Purpose] = Field(min_length=1)
    space_owned: bool = False


class ImapPasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=512, repr=False)


class MailboxRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: Literal["gmail", "imap"]
    address: str | None
    owner_user_id: UUID | None
    space_owned: bool
    purposes: list[Purpose]
    health: MailboxHealth
    last_error: str | None
    last_error_at: datetime | None
    failing_since: datetime | None
    host: str | None = None
    port: int | None = None
    security: Security | None = None
    username: str | None = None
