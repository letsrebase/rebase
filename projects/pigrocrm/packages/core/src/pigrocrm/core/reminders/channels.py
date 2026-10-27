"""The channels of a pass, in the order they are prepared and sent (spec 2026-10-26 §6)."""

from __future__ import annotations

from pigrocrm.core.mail import EmailSender
from pigrocrm.core.reminders.dispatch import Channel, ChannelDispatcher
from pigrocrm.core.reminders.email import EmailChannel
from pigrocrm.core.reminders.push import PushChannel, PushSender
from pigrocrm.core.reminders.slack import SlackChannel
from pigrocrm.core.slack.client import SlackClient


def dispatcher_for(
    *,
    sender: EmailSender | None,
    pusher: PushSender | None,
    slack: SlackClient | None = None,
) -> ChannelDispatcher:
    """Every channel the pass can carry a reminder by besides the app. A channel with no
    transport is still listed: it plans nothing and a rehearsal says so, and the pass that
    follows the transport's configuration sends what waited."""
    channels: list[Channel] = [PushChannel(pusher), SlackChannel(slack), EmailChannel(sender)]
    return ChannelDispatcher(channels)
