"""The campaigns' commands: the mail loop's pass and the Conversions API check."""

import sys
from datetime import UTC, datetime

from rebase_core.cli.common import Command, no_arguments, open_session
from rebase_core.config import get_settings


def conversions_check() -> int:
    """`rebase conversions-check`: does the Conversions API key work?

    Sends one event with `validate_only: true`, which asks OpenAI to check it and record
    nothing. The real events are deliberately invisible -- sent from a background task,
    never logged -- so a wrong key would mean a campaign with no conversions and no sign
    anywhere that the key was the reason. This is how somebody finds out in ten seconds.
    Prints the status and nothing else: the key is never echoed.
    """
    from rebase_core.conversions import pixel_from_settings

    settings = get_settings()
    pixel = pixel_from_settings(settings)
    if pixel is None:
        print(
            "No pixel configured: REBASE_OPENAI_PIXEL_ID and "
            "REBASE_OPENAI_CONVERSIONS_API_KEY are required.",
            file=sys.stderr,
        )
        return 1
    outcome = pixel.send(
        # A validation-only event needs an id like any other, and this one must not look
        # like a conversion in case the flag is ever ignored.
        event_id=f"check-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
        source_url=settings.signup_url,
        validate_only=True,
    )
    if outcome.sent:
        print(f"The key works: event validated ({outcome.status}), nothing recorded.")
        return 0
    print(
        f"OpenAI rejected the check: {outcome.status} {outcome.detail}".strip(),
        file=sys.stderr,
    )
    return 1


def campaigns_tick() -> int:
    """`rebase campaigns-tick`: one pass of the campaigns loop (P-REB-41). Runs every
    minute from the `campaigns` service in `docker-compose.yml`. Without a Resend key it
    sends nothing and says so, and exits 0 so the loop keeps running."""
    from rebase_core.campaigns.sender import campaign_sender_from_settings
    from rebase_core.campaigns.tick import run_tick
    from rebase_core.mail import sender_from_settings

    settings = get_settings()
    sender = campaign_sender_from_settings(settings)
    if sender is None:
        print("sending not configured: no campaign goes out without REBASE_RESEND_API_KEY")
        return 0
    session = open_session(settings)()
    try:
        result = run_tick(session, sender, settings, mailer=sender_from_settings(settings))
    finally:
        session.close()
    line = (
        f"{result.campaigns} campaigns, {result.sent} sent, "
        f"{result.skipped} skipped, {result.failed} failed, "
        f"{result.stamped} outcomes recorded"
    )
    if result.confirmations:
        line += f", {result.confirmations} confirmation mails"
    print(line)
    return 0


COMMANDS = {
    "conversions-check": Command(no_arguments, lambda args: conversions_check()),
    "campaigns-tick": Command(no_arguments, lambda args: campaigns_tick()),
}
