"""What a mailbox looks like to a person and to a tool (REB-1015). There is no field for the
password, sealed or not, in any of them: nothing returns it and nothing here could."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pigrocrm.core.mailboxes.health import MailboxHealth

Security = Literal["tls", "starttls"]
Purpose = Literal["correspondence", "billing"]


def _single_line(value: str) -> str:
    """A name or a password goes to the server inside a command: a line break or a NUL in it would
    end the command and start another."""
    if any(char in value for char in "\r\n\0"):
        raise ValueError("must be a single line")
    return value


class ImapMailboxCreate(BaseModel):
    """Connecting a mailbox. `space_owned` is the space's shared address (fatture@, a PEC),
    connected by an administrator; without it the mailbox is the caller's own."""

    # `hide_input_in_errors`: a 422 echoes the input it refused, and the input holds the password.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    address: str = Field(min_length=3, max_length=320, description="The mailbox's own address.")
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    security: Security
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=512, repr=False)
    purposes: list[Purpose] = Field(min_length=1)
    space_owned: bool = False

    _username_is_a_line = field_validator("username", "password")(_single_line)


class ImapPasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    password: str = Field(min_length=1, max_length=512, repr=False)

    _password_is_a_line = field_validator("password")(_single_line)


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
