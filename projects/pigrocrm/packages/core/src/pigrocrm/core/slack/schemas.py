"""What the Slack settings page reads and writes (REB-739).

No token in any form, not the plaintext, not the ciphertext, not the nonce: the same rule as
`GoogleAccountRead`. The page needs the workspace's name, whether it works, who in the space
is matched to whom, and the channel; nothing else of Slack's reaches it.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# The bot scopes the app asks for, and what each is for. The app manifest in the deploy docs
# lists these and nothing else, and a test holds the two to each other.
#   chat:write         post the direct messages, and the channel post once the bot is in it
#   chat:write.public  post to the chosen public channel without being invited to it
#   im:write           open the direct message with a member (`conversations.open`)
#   users:read         read a member (`users.lookupByEmail` answers the member's profile)
#   users:read.email   match a PigroCRM user to a Slack member by email
#   channels:read      list the public channels, for the channel picker
#   groups:read        list the private channels the bot was invited to, for the same picker
SLACK_BOT_SCOPES: tuple[str, ...] = (
    "chat:write",
    "chat:write.public",
    "im:write",
    "users:read",
    "users:read.email",
    "channels:read",
    "groups:read",
)

SlackStatus = Literal["active", "revoked", "disconnected"]
# `matched`: found by email. `manual`: an admin gave the Slack address. `unmatched`: nobody
# in the workspace has the address, so the user receives nothing on Slack.
MatchState = Literal["matched", "manual", "unmatched"]


class SlackWorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    team_id: str
    team_name: str
    status: SlackStatus
    channel_id: str | None
    channel_name: str | None
    last_error: str | None
    last_error_at: datetime | None
    connected_at: datetime
    disconnected_at: datetime | None


class SlackMemberRead(BaseModel):
    """One user of the space and what Slack knows of them."""

    user_id: UUID
    nome: str
    email: str
    state: MatchState
    # The address of the Slack member, shown when it is not the user's own (a manual fix).
    slack_email: str | None


class SlackChannelRead(BaseModel):
    id: str
    name: str
    is_private: bool
    is_member: bool


class SlackHealth(BaseModel):
    """The whole page in one answer. `configured` is false on an installation with no Slack
    app, which is a state with no workspace and no error, so the page renders it as a
    sentence and not as a failed query (the Gmail page's rule)."""

    configured: bool
    workspace: SlackWorkspaceRead | None
    members: list[SlackMemberRead] = Field(default_factory=list)
    # A sentence, in Italian, when the connection looks live and cannot work: the token on the
    # row does not open with this installation's key (a rotated `PIGROCRM_SLACK_TOKEN_KEY`).
    problem: str | None = None


class SlackMine(BaseModel):
    """What one person is told about their own Slack: whether the space has a workspace and
    whether they are in it, for the Notifiche page."""

    connected: bool
    team_name: str | None
    matched: bool


class SlackLinkWrite(BaseModel):
    """The Slack address of a user whose own address is not the one they have in Slack."""

    email: str = Field(min_length=3, max_length=320)


class SlackChannelWrite(BaseModel):
    """The channel for the space-wide reminders, or `null` for none."""

    channel_id: str | None = Field(default=None, max_length=32, pattern=r"^[A-Z0-9]+$")
