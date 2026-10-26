"""What an admin does to a connected workspace from Impostazioni → Slack, and what each
person is told about their own Slack (REB-739).

Reading is the page's one answer (`health`): the workspace, and every active user with the
state of their match, so that «see which users were matched» is a list and not a count. The
writes are the three things the card asks for besides connecting: fix a match by hand,
look again, and choose the channel for the space-wide reminders. All of them need an
**unscoped admin**, like connecting does, and all of them reach Slack with the space's own
bot token through `bot_token`, which opens it for the length of the call.

**A token Slack refuses is recorded and committed before the error leaves** (`mark_revoked`),
so the page that just failed shows a workspace to reconnect instead of the same failure
again.
"""

from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings, slack_configured
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.slack.client import SlackClient
from pigrocrm.core.slack.errors import SLACK_ENTITY, SlackRefused, SlackTokenRevoked
from pigrocrm.core.slack.matching import SlackMatcher
from pigrocrm.core.slack.models import SlackWorkspace
from pigrocrm.core.slack.repository import SlackRepository
from pigrocrm.core.slack.schemas import (
    MatchState,
    SlackChannelRead,
    SlackHealth,
    SlackMemberRead,
    SlackMine,
    SlackWorkspaceRead,
)
from pigrocrm.core.slack.token import bot_token, mark_revoked

_T = TypeVar("_T")

_MANAGE = "gestire Slack"


class SlackService:
    def __init__(self, session: Session, *, settings: Settings, client: SlackClient) -> None:
        self.session = session
        self.settings = settings
        self.client = client
        self.repo = SlackRepository(session)

    # -- reads -----------------------------------------------------------------

    def health(self, actor: Actor) -> SlackHealth:
        actor.require_unscoped_admin(_MANAGE)
        if not slack_configured(self.settings):
            return SlackHealth(configured=False, workspace=None)
        workspace = self.repo.workspace()
        if workspace is None or workspace.status == "disconnected":
            return SlackHealth(
                configured=True,
                workspace=SlackWorkspaceRead.model_validate(workspace) if workspace else None,
            )
        links = self.repo.links()
        members: list[SlackMemberRead] = []
        for user in self.repo.people():
            link = links.get(user.id)
            state: MatchState = "unmatched"
            if link is not None and link.slack_user_id is not None:
                state = "manual" if link.source == "manual" else "matched"
            members.append(
                SlackMemberRead(
                    user_id=user.id,
                    nome=user.nome,
                    email=user.email,
                    state=state,
                    slack_email=link.slack_email if link is not None else None,
                )
            )
        problem: str | None = None
        if workspace.status == "active":
            try:
                bot_token(workspace, self.settings)
            except Conflict as unreadable:
                problem = unreadable.details["reason"]
        return SlackHealth(
            configured=True,
            workspace=SlackWorkspaceRead.model_validate(workspace),
            members=members,
            problem=problem,
        )

    def mine(self, actor: Actor) -> SlackMine:
        """Whether the space has a working workspace and whether this person is in it.
        Anyone may read it: it is what explains, on their own Notifiche page, why a switch
        for Slack reaches nobody."""
        workspace = self.repo.active_workspace() if slack_configured(self.settings) else None
        if workspace is None or actor.id is None:
            return SlackMine(connected=False, team_name=None, matched=False)
        link = self.repo.link_for(actor.id)
        return SlackMine(
            connected=True,
            team_name=workspace.team_name,
            matched=link is not None and link.slack_user_id is not None,
        )

    def channels(self, actor: Actor) -> list[SlackChannelRead]:
        actor.require_unscoped_admin(_MANAGE)
        workspace, token = self._active()
        found = self._calling(workspace, lambda: self.client.list_channels(token))
        return [
            SlackChannelRead(
                id=channel.id,
                name=channel.name,
                is_private=channel.is_private,
                is_member=channel.is_member,
            )
            for channel in found
        ]

    # -- writes ----------------------------------------------------------------

    def rematch(self, actor: Actor) -> SlackHealth:
        """Asks Slack again about every user without a manual match, however recently it
        was asked."""
        actor.require_unscoped_admin(_MANAGE)
        workspace, token = self._active()
        matcher = SlackMatcher(self.repo, self.client, token)
        self._calling(workspace, lambda: matcher.match_all(self.repo.people(), force=True))
        self.session.commit()
        return self.health(actor)

    def link(self, actor: Actor, user_id: UUID, email: str) -> SlackHealth:
        """Says by hand which Slack member a user is, by the member's address in Slack:
        the way to fix a mismatch. Refused when nobody there has the address, or when that
        member is already another user's."""
        actor.require_unscoped_admin(_MANAGE)
        workspace, token = self._active()
        user = self._user(user_id)
        address = email.strip()
        member = self._calling(workspace, lambda: self.client.lookup_user_by_email(token, address))
        if member is None:
            raise Conflict(
                SLACK_ENTITY,
                f"nessuno nel workspace {workspace.team_name} ha l'indirizzo {address}",
            )
        holder = self.repo.link_of_slack_user(member.id)
        if holder is not None and holder.user_id != user.id:
            raise Conflict(
                SLACK_ENTITY,
                f"{address} è già abbinato a un altro utente di questo spazio",
            )
        self.repo.upsert_link(
            user.id, source="manual", slack_user_id=member.id, slack_email=member.email
        )
        self.session.commit()
        return self.health(actor)

    def unlink(self, actor: Actor, user_id: UUID) -> SlackHealth:
        """The way back from a manual fix: drops it and asks Slack for the user's own
        address again."""
        actor.require_unscoped_admin(_MANAGE)
        workspace, token = self._active()
        user = self._user(user_id)
        link = self.repo.link_for(user.id)
        if link is not None:
            self.repo.delete_link(link)
        matcher = SlackMatcher(self.repo, self.client, token)
        self._calling(workspace, lambda: matcher.match_user(user, force=True))
        self.session.commit()
        return self.health(actor)

    def set_channel(self, actor: Actor, channel_id: str | None) -> SlackHealth:
        """The one channel the space-wide reminders are also posted to, or none. Refused
        when Slack does not know the channel, so a typo is not saved and discovered as a
        failed post."""
        actor.require_unscoped_admin(_MANAGE)
        workspace, token = self._active()
        if channel_id is None:
            workspace.channel_id = None
            workspace.channel_name = None
        else:
            found = self._calling(workspace, lambda: self.client.channel(token, channel_id))
            if found is None:
                raise Conflict(
                    SLACK_ENTITY,
                    f"il canale {channel_id} non esiste nel workspace {workspace.team_name}, "
                    "o PigroCRM non può vederlo",
                )
            workspace.channel_id = found.id
            workspace.channel_name = found.name
        self.session.commit()
        return self.health(actor)

    # -- internals -------------------------------------------------------------

    def _active(self) -> tuple[SlackWorkspace, str]:
        workspace = self.repo.workspace()
        if workspace is None or workspace.status == "disconnected":
            raise Conflict(SLACK_ENTITY, "nessun workspace Slack collegato")
        return workspace, bot_token(workspace, self.settings)

    def _user(self, user_id: UUID) -> User:
        user = self.session.get(User, user_id)
        if user is None or not user.attivo:
            raise NotFound("user", user_id)
        return user

    def _calling(self, workspace: SlackWorkspace, call: Callable[[], _T]) -> _T:
        try:
            return call()
        except SlackTokenRevoked as refusal:
            mark_revoked(self.session, workspace, refusal)
            raise SlackTokenRevoked(refusal.details["error"], workspace.team_name) from refusal
        except SlackRefused as refused:
            if refused.code in {"missing_scope", "not_allowed_token_type"}:
                raise Conflict(
                    SLACK_ENTITY,
                    "l'app Slack non ha tutti i permessi che servono: ricollega Slack "
                    "da Impostazioni → Slack",
                    error=refused.code,
                ) from refused
            raise
