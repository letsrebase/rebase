"""The distinction the previous system did not make.

`parseGoogleError` + `truncateMessage(400)` -> 500 gave a revoked token and a flaky
network the same screen, so they got the same wrong reaction: retry. Retrying an
`invalid_grant` is a bug -- it will never succeed. These types exist so that the
difference survives the trip from the socket to the user.
"""

from dataclasses import dataclass
from uuid import UUID

from pigrocrm.core.errors import Conflict
from pigrocrm.core.http import UpstreamUnavailable


@dataclass(frozen=True)
class UpstreamFailure:
    """What Google actually said. `error_code` is the machine-readable one --
    `invalid_grant`, `UNAUTHENTICATED`, `RESOURCE_EXHAUSTED` -- and never the prose,
    because the prose is what got truncated and thrown away last time. Empty string
    when the body carried none.

    Nothing here can hold a secret: the status is an integer, the code comes from a
    fixed vocabulary Google publishes, and `retry_after` is a number. That is a
    property to preserve, not a coincidence -- this dataclass has a generated `repr`,
    so any field added later is a field that prints itself into every traceback.
    """

    status: int
    error_code: str
    retry_after: float | None


class GoogleCallFailed(Exception):
    """Internal to `pigrocrm.core.gmail`, and meant never to cross the package
    boundary: a caller outside this package cannot be expected to know what an
    `UpstreamFailure` is, so every public method converts it into a `GmailUnavailable`
    first (`tokens.py`, `send.py` and `sync.py`'s `_unavailable_on_failure` do)."""

    def __init__(self, failure: UpstreamFailure, what: str) -> None:
        super().__init__(f"{what} fallita ({failure.status}/{failure.error_code or 'n/d'})")
        self.failure = failure
        self.what = what


class CredentialRevoked(Conflict):
    """`invalid_grant`. Terminal. Do not retry, and do not skip silently."""

    def __init__(self, account_id: UUID, email_address: str) -> None:
        super().__init__(
            "google_account",
            f"il consenso Google per {email_address} è stato revocato: "
            "ricollega la casella da Impostazioni → Gmail",
            account_id=str(account_id),
            email_address=email_address,
        )


class ConsentExpired(Conflict):
    """The consent window ran out. Terminal in the same way `CredentialRevoked` is, and
    cured by the same action, but it is not the same sentence.

    Google told us nothing here: this is the state we *predicted*, from
    `consent_expires_at`. Saying "è stato revocato" for it would be a small version of
    exactly the defect this module exists to prevent -- a true-sounding message about a
    thing that did not happen -- and it would also be the difference between "Google cut
    you off" and "the seven days Testing mode gives you are up", which are very
    different things to read about your own installation.
    """

    def __init__(self, account_id: UUID, email_address: str) -> None:
        super().__init__(
            "google_account",
            f"il consenso Google per {email_address} è scaduto: "
            "ricollega la casella da Impostazioni → Gmail",
            account_id=str(account_id),
            email_address=email_address,
        )


class ScopeMissing(Conflict):
    """A healthy credential that was granted less than was asked for. `status` stays
    `active` -- it is the feature that is unavailable, not the credential."""

    def __init__(self, scope: str, feature: str) -> None:
        super().__init__(
            "google_account",
            f"{feature} non è disponibile: manca l'autorizzazione {scope}. "
            "Usa «ri-autorizza» da Impostazioni → Gmail",
            scope=scope,
            feature=feature,
        )


class GmailUnavailable(UpstreamUnavailable):
    """Transient, after every retry was spent. Distinct from `CredentialRevoked`
    precisely because the reaction differs: wait and try again, versus re-consent."""

    def __init__(self, what: str, status: int) -> None:
        super().__init__("gmail", "Gmail", status, what=what)
