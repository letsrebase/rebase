"""Reminders on Slack: a direct message to the person, a post to the space's channel
(spec 2026-10-26 §6.4, REB-740).

**One direct message per person per pass**, carrying every reminder of theirs that is due,
severest first. `chat.postMessage` is limited to about one message a second per channel, and
a person reading a dozen messages in a row reads none of them, so this is the shape the email's
immediate mode has (`email.py`): the first pass after a night of quiet hours is one message.
At most `MAX_LINES` reminders go in one message; what does not fit is not recorded and goes in
the next pass, so nothing is hidden and nothing arrives as a burst.

**Who can be told.** A person with Slack among their effective channels, outside their quiet
hours (§6.3), and matched to a Slack member (`slack_user_links`). One not matched yet is
looked up by their address in this pass, when the reminder is new to their list, and the
answer is written with the ledger, so an address that is not in the workspace costs one
lookup a day and not one a quarter of an hour (`slack/matching.py`). Nobody is written to
who is not in the workspace.

**A reminder is announced if it reached the person's list after they were matched**, like push
(`push.py`): connecting a workspace does not send the backlog of everything that was open, and a
reminder that climbs to a higher severity is a new arrival and is sent.

**The space's channel**, when one is chosen (`slack_workspaces.channel_id`), also gets the
space-wide reminders, the types whose audience is the admins (`catalogue.py`), once per
occurrence and rank, in one post per pass. It follows the space's defaults and not a person's:
the type's channels (`slack` must be on for the space) and the space's quiet hours. Its ledger is
`slack_channel_posts`, since a post has no user, written through `ChannelResult.writes`.

**A failed send writes nothing** (§8.3), and `send` never raises. A token Slack says is dead
ends the pass for the space and is recorded on the workspace (`status = revoked`) in the
pass's own transaction, so the Slack page shows a workspace to reconnect; an outage ends it
without recording anything; a refusal for one person (a closed account) skips that person and
tries again next pass. No Slack configured, no workspace, or a token that cannot be opened:
nothing is sent and nothing is recorded, and the pass that follows the fix sends what waited.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import partial
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pigrocrm.core.config import slack_configured
from pigrocrm.core.errors import Conflict
from pigrocrm.core.reminders.catalogue import SEVERITY_RANK, Severity, get_type
from pigrocrm.core.reminders.dispatch import (
    Accepted,
    Candidate,
    Channel,
    ChannelResult,
    PassContext,
    Prepared,
)
from pigrocrm.core.reminders.links import AVVISI_PATH, absolute, record_path
from pigrocrm.core.slack.client import SlackClient
from pigrocrm.core.slack.errors import SlackRefused, SlackTokenRevoked
from pigrocrm.core.slack.matching import RETRY_UNMATCHED_AFTER
from pigrocrm.core.slack.models import SlackChannelPost, SlackUserLink, SlackWorkspace
from pigrocrm.core.slack.repository import SlackRepository
from pigrocrm.core.slack.token import bot_token
from pigrocrm.core.tenants import space_base_settings

logger = logging.getLogger(__name__)

SLACK = "slack"
# How many reminders one message carries; the rest wait for the next pass.
MAX_LINES = 20

# The word each severity wears in the app and in the mail (`mail.py`).
_SEVERITY_WORD: dict[Severity, str] = {
    "critical": "Urgente",
    "serious": "Grave",
    "warning": "Da vedere",
}


def escape(text: str) -> str:
    """Slack's three control characters, the only ones its mrkdwn asks to be escaped: a
    title that holds `<!channel>` must not mention a channel."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class _Line:
    """One reminder in a message. `accepted` is the ledger row a direct message writes once
    Slack took it, and `None` for a line of the channel post, which writes its own."""

    candidate: Candidate
    accepted: Accepted | None

    @property
    def severity(self) -> Severity:
        return self.candidate.reminder.severity


@dataclass
class _Dm:
    """One person's message to plan. `slack_user_id` is `None` while they are still to be
    looked up by `email`."""

    user_id: UUID
    email: str
    slack_user_id: str | None
    lines: list[_Line]


def _ordered(lines: list[_Line]) -> list[_Line]:
    return sorted(
        lines, key=lambda line: (-SEVERITY_RANK[line.severity], line.candidate.reminder.key)
    )


def _body(lines: list[_Line], public_url: str, header: str, footer: bool) -> str:
    rows = []
    for line in lines:
        reminder = line.candidate.reminder
        title = escape(reminder.title)
        if public_url:
            url = absolute(public_url, record_path(reminder.entity, reminder.entity_id))
            title = f"<{url}|{title}>"
        row = f"• *{_SEVERITY_WORD[line.severity]}* · {title}"
        if reminder.detail:
            row += f" — {escape(reminder.detail)}"
        rows.append(row)
    parts = [header, *rows]
    if footer and public_url:
        parts.append(f"<{absolute(public_url, AVVISI_PATH)}|Apri gli avvisi>")
    return "\n".join(parts)


def direct_message(lines: list[_Line], public_url: str) -> str:
    count = len(lines)
    header = "*Hai un promemoria*" if count == 1 else f"*Hai {count} promemoria*"
    return _body(lines, public_url, header, footer=True)


def channel_post(lines: list[_Line], public_url: str) -> str:
    count = len(lines)
    header = (
        "*Un promemoria per lo spazio*" if count == 1 else f"*{count} promemoria per lo spazio*"
    )
    return _body(lines, public_url, header, footer=True)


class SlackChannel(Channel):
    name = SLACK

    def __init__(self, client: SlackClient | None) -> None:
        self.client = client

    def prepare(
        self, session: Session, context: PassContext, candidates: Sequence[Candidate]
    ) -> Prepared:
        client = self.client
        empty = Prepared(SLACK, 0, ChannelResult)
        if client is None:
            return empty
        settings = space_base_settings(context.settings, context.slug)
        if not slack_configured(settings):
            return empty
        repo = SlackRepository(session)
        workspace = repo.active_workspace()
        if workspace is None:
            return empty
        try:
            token = bot_token(workspace, settings)
        except Conflict:
            # A key that does not open the token is the operator's to fix, and a channel
            # that cannot read its credential must not stop the others.
            logger.warning("slack: il token memorizzato non è leggibile")
            return empty

        links = repo.links()
        taken = {link.slack_user_id: link.user_id for link in links.values() if link.slack_user_id}
        workspace_id = workspace.id
        dms = self._plan_dms(context, candidates, links)
        post = self._plan_post(session, context, candidates, workspace)

        def send() -> ChannelResult:
            result = ChannelResult()
            try:
                self._send_dms(client, token, dms, taken, context, result)
                if post is not None:
                    self._send_post(client, token, post, context, result)
            except SlackTokenRevoked as refusal:
                code = str(refusal.details.get("error", ""))
                result.writes.append(lambda s: _revoke(s, workspace_id, code))
            except Conflict:
                logger.warning("slack: Slack non ha risposto")
            except Exception:  # noqa: BLE001 - the seam's contract is "never raises"
                logger.warning("slack: invio non riuscito")
            return result

        return Prepared(SLACK, len(dms) + (1 if post is not None else 0), send)

    # -- planning ------------------------------------------------------------

    @staticmethod
    def _plan_dms(
        context: PassContext, candidates: Sequence[Candidate], links: dict[UUID, SlackUserLink]
    ) -> list[_Dm]:
        by_user: dict[UUID, _Dm] = {}
        for candidate in candidates:
            reminder = candidate.reminder
            if SLACK not in candidate.effective.channels or candidate.quiet:
                continue
            identity = candidate.identity(SLACK)
            if context.covered(identity, reminder.severity):
                continue
            link = links.get(reminder.user_id)
            slack_user_id = link.slack_user_id if link is not None else None
            # Looked up and not found: asked again after a day, never every pass.
            if (
                link is not None
                and slack_user_id is None
                and context.now - link.checked_at < RETRY_UNMATCHED_AFTER
            ):
                continue
            # Announced only what reached the list after the person was matched, as push
            # does; a person about to be looked up is matched now, so only what is new to
            # their list in this pass.
            if slack_user_id is not None:
                matched_at = link.matched_at if link is not None else None
                if matched_at is not None and candidate.listed_at < matched_at:
                    continue
            elif candidate.listed_at < context.now:
                continue
            dm = by_user.get(reminder.user_id)
            if dm is None:
                dm = by_user[reminder.user_id] = _Dm(
                    reminder.user_id, candidate.person.email, slack_user_id, []
                )
            dm.lines.append(_Line(candidate, Accepted(identity, reminder.severity)))
        dms = list(by_user.values())
        for dm in dms:
            dm.lines = _ordered(dm.lines)[:MAX_LINES]
        return sorted(dms, key=lambda dm: str(dm.user_id))

    @staticmethod
    def _plan_post(
        session: Session,
        context: PassContext,
        candidates: Sequence[Candidate],
        workspace: SlackWorkspace,
    ) -> _Post | None:
        channel_id = workspace.channel_id
        if channel_id is None:
            return None
        space = context.preferences.space()
        local = context.local_now
        if space.general().quiet_at(local.weekday(), local.time()):
            return None
        # One line per key and day, at the highest severity anybody was raised it at.
        by_key: dict[tuple[str, date | None], _Line] = {}
        for candidate in candidates:
            reminder = candidate.reminder
            if get_type(reminder.type).audience != "admins":
                continue
            if SLACK not in space.type(reminder.type).channels:
                continue
            slot = (reminder.key, reminder.period)
            current = by_key.get(slot)
            if (
                current is None
                or SEVERITY_RANK[reminder.severity] > SEVERITY_RANK[current.severity]
            ):
                by_key[slot] = _Line(candidate, None)
        if not by_key:
            return None
        posted = {
            (row.key, row.period): row.severity_rank
            for row in session.scalars(
                select(SlackChannelPost).where(
                    SlackChannelPost.channel_id == channel_id,
                    SlackChannelPost.key.in_([key for key, _ in by_key]),
                )
            )
        }
        due = [
            line
            for slot, line in by_key.items()
            if posted.get(slot, 0) < SEVERITY_RANK[line.severity]
        ]
        if not due:
            return None
        return _Post(channel_id, _ordered(due)[:MAX_LINES])

    # -- sending ---------------------------------------------------------------

    def _send_dms(
        self,
        client: SlackClient,
        token: str,
        dms: list[_Dm],
        taken: dict[str, UUID],
        context: PassContext,
        result: ChannelResult,
    ) -> None:
        for dm in dms:
            slack_user_id = dm.slack_user_id
            if slack_user_id is None:
                slack_user_id = self._look_up(client, token, dm, taken, context, result)
                if slack_user_id is None:
                    continue
            try:
                channel = client.open_dm(token, slack_user_id)
                client.post_message(token, channel, direct_message(dm.lines, context.public_url))
            except SlackRefused as refused:
                # One person's refusal (a closed account, a user who cannot be written to)
                # is theirs: skipped, and tried again next pass.
                logger.warning("slack: messaggio rifiutato (%s)", refused.code)
                continue
            result.accepted.extend(line.accepted for line in dm.lines if line.accepted is not None)
            result.sent[SLACK] = result.sent.get(SLACK, 0) + 1

    @staticmethod
    def _look_up(
        client: SlackClient,
        token: str,
        dm: _Dm,
        taken: dict[str, UUID],
        context: PassContext,
        result: ChannelResult,
    ) -> str | None:
        """The Slack member of a person not matched yet, written with the pass's ledger.
        `None` when nobody there has their address, or the member is another user's."""
        user_id = dm.user_id
        now = context.now
        try:
            member = client.lookup_user_by_email(token, dm.email)
        except SlackRefused as refused:
            logger.warning("slack: ricerca rifiutata (%s)", refused.code)
            return None
        if member is None or taken.get(member.id, user_id) != user_id:
            result.writes.append(lambda s: _remember(s, user_id, None, None, now))
            return None
        taken[member.id] = user_id
        result.writes.append(lambda s: _remember(s, user_id, member.id, member.email, now))
        return member.id

    @staticmethod
    def _send_post(
        client: SlackClient,
        token: str,
        post: _Post,
        context: PassContext,
        result: ChannelResult,
    ) -> None:
        try:
            client.post_message(
                token, post.channel_id, channel_post(post.lines, context.public_url)
            )
        except SlackRefused as refused:
            # A channel that was archived or that the bot cannot reach: the page shows
            # nothing wrong until somebody looks, and the next pass tries again.
            logger.warning("slack: post nel canale rifiutato (%s)", refused.code)
            return
        for line in post.lines:
            reminder = line.candidate.reminder
            result.writes.append(
                partial(
                    _record_post,
                    key=reminder.key,
                    type_=reminder.type,
                    channel_id=post.channel_id,
                    period=reminder.period,
                    severity=reminder.severity,
                )
            )
        result.sent[SLACK] = result.sent.get(SLACK, 0) + 1


@dataclass(frozen=True)
class _Post:
    channel_id: str
    lines: list[_Line]


# -- the writes a pass makes for Slack, in its own transaction -------------------------


def _remember(
    session: Session, user_id: UUID, slack_user_id: str | None, email: str | None, at: datetime
) -> None:
    SlackRepository(session).upsert_link(
        user_id,
        source="email" if slack_user_id else "none",
        slack_user_id=slack_user_id,
        slack_email=email,
        now=at,
    )


def _record_post(
    session: Session,
    *,
    key: str,
    type_: str,
    channel_id: str,
    period: date | None,
    severity: Severity,
) -> None:
    """Writes that the channel took a post, keeping the highest rank (an escalation is an
    upsert, as in `ReminderLedger.record`)."""
    statement = insert(SlackChannelPost).values(
        key=key,
        type=type_,
        channel_id=channel_id,
        period=period,
        severity_rank=SEVERITY_RANK[severity],
        posted_at=datetime.now(UTC),
    )
    session.execute(
        statement.on_conflict_do_update(
            constraint="uq_slack_channel_post_occurrence",
            set_={
                "severity_rank": func.greatest(
                    SlackChannelPost.severity_rank, statement.excluded.severity_rank
                ),
                "posted_at": statement.excluded.posted_at,
                "updated_at": func.now(),
            },
        )
    )


def _revoke(session: Session, workspace_id: UUID, code: str) -> None:
    workspace = session.get(SlackWorkspace, workspace_id)
    if workspace is None or workspace.status != "active":
        return
    workspace.status = "revoked"
    workspace.last_error = SlackTokenRevoked(code, workspace.team_name).details["reason"]
    workspace.last_error_at = datetime.now(UTC)
