"""Reminders by email: urgent at once, the rest in one daily summary (spec 2026-10-26 §6.2,
REB-737).

**Which mail a reminder goes in is the person's `email_mode`** (spec §5): `immediate`,
or `summary`, and while they have not fixed one, urgent (at or above their `urgent_from`)
is immediate and everything else is summary. The ledger knows the two apart, because the
mode is part of its identity: a reminder summarised at `warning` that climbs to
`critical` is mailed again, at once.

- **Immediate**: every reminder of a person that is due in this pass goes in one mail, so
  the first pass after a night of quiet hours is a mail and not a dozen.
- **Summary**: one mail per person per space per day, in the first pass at or after their
  summary hour that is outside their quiet hours and has no `reminder_summary_run` row
  for the day, carrying every summary-mode reminder not yet delivered, severest first. A
  day with nothing to say sends nothing and writes nothing, so a reminder that appears at
  noon goes out at noon, and not a day later, on a day whose summary has not gone.

**Quiet hours hold both**, urgent mail included (§6.3): a reminder that falls in the
window stays undelivered and the first pass after it delivers. **A failed send writes
nothing** (§8.3): `EmailSender.send` answers `False` and the next pass tries again. No
transport at all (no Resend key) is the same: nothing is sent and nothing is recorded, so
the pass after the key is configured sends what waited.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import time
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.mail import EmailSender, ReminderLine, reminder_mail
from pigrocrm.core.reminders.catalogue import SEVERITY_RANK
from pigrocrm.core.reminders.dispatch import (
    Accepted,
    Candidate,
    Channel,
    ChannelResult,
    PassContext,
    Prepared,
)
from pigrocrm.core.reminders.ledger import DeliveryIdentity, ReminderLedger
from pigrocrm.core.reminders.links import (
    AVVISI_PATH,
    NOTIFICATIONS_PATH,
    absolute,
    record_path,
)

EMAIL = "email"
Mode = Literal["immediate", "summary"]


class _Mail:
    """One mail to plan: who, which kind, and the reminders it carries with their ledger
    identities."""

    def __init__(
        self, to: str, user_id: UUID, kind: Mode, lines: list[tuple[ReminderLine, Accepted]]
    ) -> None:
        self.to = to
        self.user_id = user_id
        self.kind = kind
        self.lines = lines


class EmailChannel(Channel):
    name = EMAIL

    def __init__(self, sender: EmailSender | None) -> None:
        self.sender = sender

    def prepare(
        self, session: Session, context: PassContext, candidates: Sequence[Candidate]
    ) -> Prepared:
        sender = self.sender
        if sender is None:
            return Prepared(EMAIL, 0, ChannelResult)
        already = ReminderLedger(session).summaries_sent(context.today)
        immediate: dict[UUID, list[tuple[Candidate, DeliveryIdentity]]] = defaultdict(list)
        summary: dict[UUID, list[tuple[Candidate, DeliveryIdentity]]] = defaultdict(list)
        for candidate in candidates:
            reminder = candidate.reminder
            if EMAIL not in candidate.effective.channels or candidate.quiet:
                continue
            mode = candidate.effective.email_mode_for(reminder.severity)
            identity = candidate.identity(EMAIL, mode)
            if context.covered(identity, reminder.severity):
                continue
            (immediate if mode == "immediate" else summary)[reminder.user_id].append(
                (candidate, identity)
            )

        mails: list[_Mail] = []
        for user_id, items in immediate.items():
            mails.append(self._mail(context, user_id, "immediate", items))
        local_time = context.local_now.time()
        for user_id, items in summary.items():
            hour = items[0][0].general.summary_hour
            if user_id in already or local_time < time(hour):
                continue
            mails.append(self._mail(context, user_id, "summary", items))

        def send() -> ChannelResult:
            result = ChannelResult()
            for mail in mails:
                lines = [line for line, _ in mail.lines]
                message = reminder_mail(
                    mail.to,
                    lines,
                    kind=mail.kind,
                    avvisi_url=absolute(context.public_url, AVVISI_PATH),
                    settings_url=absolute(context.public_url, NOTIFICATIONS_PATH),
                )
                if not sender.send(message):
                    continue
                result.accepted.extend(accepted for _, accepted in mail.lines)
                result.sent[EMAIL] = result.sent.get(EMAIL, 0) + 1
                if mail.kind == "summary":
                    result.summaries.append((mail.user_id, context.today))
            return result

        return Prepared(EMAIL, len(mails), send)

    @staticmethod
    def _mail(
        context: PassContext,
        user_id: UUID,
        kind: Mode,
        items: list[tuple[Candidate, DeliveryIdentity]],
    ) -> _Mail:
        """Severest first, then by key so two passes order a tie the same way."""
        ordered = sorted(
            items,
            key=lambda item: (-SEVERITY_RANK[item[0].reminder.severity], item[0].reminder.key),
        )
        lines: list[tuple[ReminderLine, Accepted]] = []
        for candidate, identity in ordered:
            reminder = candidate.reminder
            line = ReminderLine(
                severity=reminder.severity,
                title=reminder.title,
                detail=reminder.detail,
                url=absolute(context.public_url, record_path(reminder.entity, reminder.entity_id)),
                urgent=candidate.effective.is_urgent(reminder.severity),
            )
            lines.append((line, Accepted(identity, reminder.severity)))
        return _Mail(items[0][0].person.email, user_id, kind, lines)
