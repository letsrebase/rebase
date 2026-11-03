"""The Drive credential's own refusals.

`gmail/errors.py` exists because retrying an `invalid_grant` is a bug, and its types
keep that distinction alive from the socket to the user. This module exists for the
adjacent reason: the *sentence* has to survive the same trip.

One Google OAuth client authenticates both credentials, so the Gmail side's
`CredentialRevoked` and `ConsentExpired` are the exceptions a Drive code path naturally
ends up raising. They read «ricollega la casella da Impostazioni → Gmail» under the
entity `google_account`, and that text reaches the RFC 9457 problem document and the
MCP guidance message verbatim -- so a revoked *Drive* grant would tell somebody to
reconnect their mailbox. That instruction fixes nothing, and while the reader is
following it the thing that actually broke stays hidden. The `google_account` entity
compounds it: an adapter that routes on the entity would point at the wrong settings
tab.

So these two mirror the Gmail pair exactly -- same base, same terminal-versus-predicted
split, same `details` keys -- and change only the credential named. Nothing here may
hold a token: `details` carries the account id and the address, both of which the
settings page already shows, and the two `__init__` signatures are the only way to
build one, so there is no path by which a caller could attach a secret to it.
"""

from uuid import UUID

from pigrocrm.core.errors import Conflict
from pigrocrm.core.http import UpstreamUnavailable

DRIVE_ENTITY = "google_drive_account"
_RECONNECT = "ricollega Drive da Impostazioni → Drive"
# `DriveCredentialRevoked` names the account in the sentence it shows. The code
# exchange has no row yet -- `GoogleDriveOAuthService.complete` catches the Gmail-worded
# refusal before there is anything to name -- so it gets this label instead of Gmail's
# own «la casella collegata», which would reintroduce the defect this module removes.
_UNNAMED_DRIVE = "l'account Google scelto"


class DriveCredentialRevoked(Conflict):
    """`invalid_grant` on the Drive grant. Terminal: do not retry, and do not skip
    silently.

    Both arguments are optional, unlike Gmail's `CredentialRevoked`, because this one
    has a call site with neither: the authorization-code exchange in
    `GoogleDriveOAuthService.complete` fails before a `GoogleDriveAccount` row exists
    and before Google has named an identity. Passing `UUID(int=0)` there -- what
    `gmail/tokens.py` does -- would put an account id in `details` that names no
    account, which is a worse answer than saying nothing.
    """

    def __init__(self, account_id: UUID | None = None, email_address: str | None = None) -> None:
        super().__init__(
            DRIVE_ENTITY,
            f"il consenso Google Drive per {email_address or _UNNAMED_DRIVE} è stato "
            f"revocato: {_RECONNECT}",
            account_id=str(account_id) if account_id is not None else None,
            email_address=email_address,
        )


class DriveConsentExpired(Conflict):
    """The consent window ran out. Terminal in the same way `DriveCredentialRevoked` is,
    and cured by the same action, but not the same sentence -- for the reason
    `gmail/errors.py`'s `ConsentExpired` states at length: Google told us nothing here,
    this is the state the CRM *predicted* from `consent_expires_at`, and «è stato
    revocato» would be a true-sounding message about a thing that did not happen.

    Both arguments are required, unlike the pair above: this is only ever raised from a
    stored row, so the account and its address are always known.
    """

    def __init__(self, account_id: UUID, email_address: str) -> None:
        super().__init__(
            DRIVE_ENTITY,
            f"il consenso Google Drive per {email_address} è scaduto: {_RECONNECT}",
            account_id=str(account_id),
            email_address=email_address,
        )


class DriveUnavailable(UpstreamUnavailable):
    """Transient, after every retry was spent -- the Drive twin of `GmailUnavailable`, and
    distinct from `DriveCredentialRevoked` precisely because the reaction differs: wait and
    try again, versus re-consent. Both call sites (the token refresh in
    `drive/transport.py` and the code exchange in `drive/oauth.py`) raise it, and the
    sentence is `UpstreamUnavailable`'s, the same as Gmail's and Slack's."""

    def __init__(self, status: int) -> None:
        super().__init__("google_drive", "Google Drive", status)
