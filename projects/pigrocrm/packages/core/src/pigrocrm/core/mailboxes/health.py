"""The health of a mailbox, one vocabulary for every provider (REB-1014, § 2.2).

Gmail's statuses (`GoogleAccount.status`: `active`, `expired`, `revoked`, `disconnected`) map
onto it: `expired` and `revoked` are both a grant a person must renew, so both are `auth_failed`.
Gmail records no transient failure, so a Gmail mailbox is never `failing`.

This is the mailbox's own word. The Gmail banner keeps its four reasons (`revoked`, `expiring`,
`expired`, `scope_missing`, `gmail/schemas.py`) and is not replaced by it.
"""

from typing import Final, Literal

MailboxHealth = Literal["active", "failing", "auth_failed", "disconnected"]

GMAIL_HEALTH: Final[dict[str, MailboxHealth]] = {
    "active": "active",
    "expired": "auth_failed",
    "revoked": "auth_failed",
    "disconnected": "disconnected",
}


def gmail_health(account_status: str) -> MailboxHealth:
    """A `GoogleAccount.status` in the mailbox's vocabulary. An unknown status raises: a fifth
    Gmail state has to be placed on purpose, not read as healthy."""
    return GMAIL_HEALTH[account_status]
