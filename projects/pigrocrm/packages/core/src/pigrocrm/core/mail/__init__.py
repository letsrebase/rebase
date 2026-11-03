"""Outbound mail: a seam, one provider, and the mails PigroCRM sends (spec 2026-09-12 §6.1).

`transport` is the seam (`Mail`, the senders, Resend behind them), `frame` the branded HTML
box every mail sits in, `formatting` the Italian dates and amounts, and each mail has its
own module: `digest`, `reminder`, `invitation`, `magic_link`, `welcome`. The rest of the
repository imports from this package, never from a submodule.
"""

from pigrocrm.core.mail.digest import digest_mail, digest_subject
from pigrocrm.core.mail.formatting import day_short, euro
from pigrocrm.core.mail.invitation import invitation_mail
from pigrocrm.core.mail.magic_link import magic_link_mail
from pigrocrm.core.mail.reminder import ReminderLine, reminder_mail
from pigrocrm.core.mail.transport import (
    OPEN_MAIL_STATES,
    RESEND_URL,
    USER_AGENT,
    EmailSender,
    Mail,
    MailState,
    Receipt,
    RecordingSender,
    ResendSender,
    TrackedSender,
    sender_from_settings,
    urllib_call,
)
from pigrocrm.core.mail.welcome import welcome_mail

__all__ = [
    "OPEN_MAIL_STATES",
    "RESEND_URL",
    "USER_AGENT",
    "EmailSender",
    "Mail",
    "MailState",
    "Receipt",
    "RecordingSender",
    "ReminderLine",
    "ResendSender",
    "TrackedSender",
    "digest_mail",
    "digest_subject",
    "euro",
    "day_short",
    "invitation_mail",
    "magic_link_mail",
    "reminder_mail",
    "sender_from_settings",
    "urllib_call",
    "welcome_mail",
]
