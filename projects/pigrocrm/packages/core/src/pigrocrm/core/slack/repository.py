"""Queries only. Never commits -- the service owns the transaction.

The in-flight authorisation is not here: `google_oauth_states` serves the Gmail, the Drive
and the Slack flow (`GoogleOAuthState.purpose`), and `GmailRepository.add_state` and
`consume_state` already implement the one-statement, race-safe redemption that a fourth
copy could only get wrong by drifting from it. This repository owns the tables that are
Slack's alone.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.slack.models import SlackUserLink, SlackWorkspace


class SlackRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def workspace(self) -> SlackWorkspace | None:
        """The space's workspace, connected or not: there is at most one row."""
        return self.session.execute(select(SlackWorkspace).limit(1)).scalar_one_or_none()

    def active_workspace(self) -> SlackWorkspace | None:
        found = self.workspace()
        return found if found is not None and found.status == "active" else None

    def add_workspace(self, workspace: SlackWorkspace) -> SlackWorkspace:
        self.session.add(workspace)
        self.session.flush()
        return workspace

    # -- who is whom ------------------------------------------------------------

    def links(self) -> dict[UUID, SlackUserLink]:
        return {link.user_id: link for link in self.session.scalars(select(SlackUserLink))}

    def link_for(self, user_id: UUID) -> SlackUserLink | None:
        return self.session.execute(
            select(SlackUserLink).where(SlackUserLink.user_id == user_id)
        ).scalar_one_or_none()

    def link_of_slack_user(self, slack_user_id: str) -> SlackUserLink | None:
        return self.session.execute(
            select(SlackUserLink).where(SlackUserLink.slack_user_id == slack_user_id)
        ).scalar_one_or_none()

    def upsert_link(
        self,
        user_id: UUID,
        *,
        source: str,
        slack_user_id: str | None,
        slack_email: str | None,
        now: datetime | None = None,
    ) -> SlackUserLink:
        link = self.link_for(user_id)
        if link is None:
            link = SlackUserLink(user_id=user_id, source=source)
            self.session.add(link)
        link.source = source
        link.slack_user_id = slack_user_id
        link.slack_email = slack_email
        link.checked_at = now or datetime.now(UTC)
        self.session.flush()
        return link

    def delete_link(self, link: SlackUserLink) -> None:
        self.session.delete(link)
        self.session.flush()

    def delete_links(self, *, except_sources: Iterable[str] = ()) -> None:
        statement = delete(SlackUserLink)
        kept = list(except_sources)
        if kept:
            statement = statement.where(SlackUserLink.source.not_in(kept))
        self.session.execute(statement)

    def people(self) -> list[User]:
        """The users who can be told: active, with an address, in a stable order."""
        return list(
            self.session.scalars(
                select(User).where(User.attivo.is_(True)).order_by(User.nome, User.id)
            )
        )
