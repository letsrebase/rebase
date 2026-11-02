"""What the hub's schema modules share: the bounds of a typed value, the cleaners every wizard and
the team request run their text through, the completeness bar of a card, and the bare `Ack`."""

import re
from decimal import Decimal
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from rebase_core.linkedin import reject_control_characters
from rebase_core.models import REFERRAL_CODE_LENGTH

# What an address may be before it is normalised: a share link carries its tracking, so
# the raw value is bounded well above the column and the stored one is checked again.
LINKEDIN_INPUT_MAX_LENGTH = 2000


# The id PostHog gave the browser (`shared/analytics` `distinctId()`), sent with an
# application so the server's completion event lands on the same person (REB-215). An
# opaque string somebody else chose: bounded, never parsed, never stored.
DISTINCT_ID_MAX_LENGTH = 200


Remoto = Literal["remoto", "ibrido", "in_sede"]


# A day's worth of work, in euro. Wide enough for anyone, narrow enough that a typo of
# one extra zero is still a number the admin can read and correct.
TARIFFA_MIN = Decimal("1")
TARIFFA_MAX = Decimal("99999.99")
LINKS_MAX = 10
LINK_MAX_LENGTH = 300
PROGETTO_MAX_LENGTH = 4000


def clean_text(value: str, *, what: str) -> str:
    """A one-line value a person types (a name, a company, a phone): trimmed, no
    control or direction character, never only spaces. Shared by the wizards' schemas
    and the team request's (`team_schemas.TeamRequestCreate`)."""
    trimmed = reject_control_characters(value).strip()
    if not trimmed:
        raise ValueError(f"serve {what}, non solo spazi")
    return trimmed


def clean_multiline(value: str, *, what: str) -> str:
    """A text a person writes in several lines: newlines and tabs stay, every other
    control character and the bidi overrides are refused as everywhere else, and a
    value that is only whitespace is refused rather than stored as nothing. Shared by
    the company's project description and by the comments, so the API and the MCP
    server agree on what a paragraph may contain."""
    for character in value:
        if character in "\n\r\t":
            continue
        reject_control_characters(character)
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"serve {what}, non solo spazi")
    return trimmed


def https_url(value: str) -> str:
    """Any https address: the additional links are the person's own (a site, a GitHub,
    a portfolio), so the host is not checked, only that it is a link worth following."""
    trimmed = reject_control_characters(value).strip()
    parts = urlsplit(trimmed)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("serve un indirizzo https completo")
    return trimmed


_RIF_SHAPE = re.compile(r"^[A-Za-z0-9]+$")


def clean_rif(value: object) -> str | None:
    """`rif=` as the wizard's own funnel already treats it (`resolveReferral`,
    `lib/utm.ts`): shaped like a code or it is not one, never a reason to fail the
    whole signup. `ReferralService.resolve_referrer` mutes an unknown code the same
    way, so a public client sending punctuation or a stray length here gets a
    signup with no referral, not a 422 (CodeRabbit)."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed or len(trimmed) > REFERRAL_CODE_LENGTH or not _RIF_SHAPE.match(trimmed):
        return None
    return trimmed


class Ack(BaseModel):
    """What every public POST of the hub answers, and all it answers: accepted."""

    ok: bool = True


def card_is_complete(
    cv_size: int | None,
    tariffa_giornaliera: Decimal | None,
    posizione: str | None,
    remoto: str | None,
) -> bool:
    """The same four values `is_complete` reads, over the raw columns rather than a
    card object: CV, rate, position and remote option all there. Anything else that
    reads these four fields -- a campaign's journey state among them -- calls this
    instead of copying the check."""
    return all(value is not None for value in (cv_size, tariffa_giornaliera, posizione, remoto))


class CardFields(Protocol):
    """What the completeness bar reads: a card as the member or the admin reads it."""

    @property
    def cv_size(self) -> int | None: ...
    @property
    def tariffa_giornaliera(self) -> Decimal | None: ...
    @property
    def posizione(self) -> str | None: ...
    @property
    def remoto(self) -> str | None: ...


def is_complete(card: CardFields) -> bool:
    """CV, rate, position and remote option all there. What «Da completare» in the admin
    area and the notice in the member area read (ORB-155), and what the welcome mailing
    reads before it tells somebody their card is complete. The name and the address are
    never missing, so they are not checked.

    No longer "a card the wizard would have accepted": the wizard accepts one without a
    CV, and this stays the fuller bar, which is the point of having it."""
    return card_is_complete(card.cv_size, card.tariffa_giornaliera, card.posizione, card.remoto)
