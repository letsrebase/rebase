"""Which Slack member a PigroCRM user is: by email, with a way to say it by hand.

`SlackMatcher` is the one place that asks Slack «who has this address». The page calls it
for «Cerca di nuovo» and when a workspace is connected, and the reminder pass calls it for a
user with a Slack reminder due and no answer yet, so the rule is not written twice.

**A manual match is never overwritten by an automatic one.** An admin who said that
`mario@example.it` is `m.rossi@acme.example` in Slack made a decision the automatic lookup
cannot improve on, so `match_all` and `match_user` skip a `manual` link and only the admin's
own «ripristina» removes it.

**A lookup that finds nobody is remembered for a day** (`source = 'none'`): a pass runs every
quarter of an hour and `users.lookupByEmail` is rate limited, so a user whose address is not
in the workspace must not cost a request per pass. An admin's «Cerca di nuovo» ignores the
clock.

**One Slack member is one person.** Two PigroCRM users with the same Slack address (an
old address kept on a second user) would both write to one inbox, so the second is left
unmatched rather than sharing the member.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from pigrocrm.core.auth.models import User
from pigrocrm.core.slack.client import SlackClient
from pigrocrm.core.slack.models import SlackUserLink
from pigrocrm.core.slack.repository import SlackRepository

# How long «nobody there» stands before the pass asks again.
RETRY_UNMATCHED_AFTER = timedelta(days=1)


class SlackMatcher:
    def __init__(self, repo: SlackRepository, client: SlackClient, token: str) -> None:
        self.repo = repo
        self.client = client
        self.token = token

    def match_user(
        self, user: User, *, force: bool = False, now: datetime | None = None
    ) -> SlackUserLink:
        """The user's link after asking Slack by their address, unless the link already
        answers: a manual one, or an unmatched one younger than a day (`force` ignores the
        day)."""
        now = now or datetime.now(UTC)
        link = self.repo.link_for(user.id)
        if link is not None:
            if link.source == "manual":
                return link
            if (
                link.source == "none"
                and not force
                and now - link.checked_at < RETRY_UNMATCHED_AFTER
            ):
                return link
        member = self.client.lookup_user_by_email(self.token, user.email)
        # An admin may have said who this person is while Slack was answering: that stands.
        current = self.repo.fresh_link_for(user.id)
        if current is not None and current.source == "manual":
            return current
        if member is None:
            return self.repo.upsert_link(
                user.id, source="none", slack_user_id=None, slack_email=None, now=now
            )
        holder = self.repo.link_of_slack_user(member.id)
        if holder is not None and holder.user_id != user.id:
            # The member is somebody else's already: this user stays unmatched.
            return self.repo.upsert_link(
                user.id, source="none", slack_user_id=None, slack_email=None, now=now
            )
        return self.repo.upsert_link(
            user.id,
            source="email",
            slack_user_id=member.id,
            slack_email=member.email,
            now=now,
        )

    def match_all(self, users: Iterable[User], *, force: bool = False) -> None:
        for user in users:
            self.match_user(user, force=force)
