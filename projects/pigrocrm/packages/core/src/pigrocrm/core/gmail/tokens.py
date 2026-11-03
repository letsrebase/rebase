"""Access tokens: obtained, cached in memory, never stored.

Three things the previous system got wrong and this module exists to get right:

1. It discarded `expires_in` and re-exchanged on every call site -- two OAuth
   round-trips to send one invoice email.
2. Its Gmail refresh token fell back to the Drive one (`effectiveGmailRefresh`),
   putting two different capabilities on one credential.
3. It could not tell `invalid_grant` from a transient failure, so a revoked token and
   a flaky network produced the same screen and the same wrong reaction.

Only the refresh token is persisted, and only encrypted (`core/crypto.py`). An access
token is valid for an hour; persisting it would add a second secret to protect for no
gain.

Nothing in this module logs, and nothing that leaves it carries a token. Both dataclass
types below mark their token fields `repr=False`: a dataclass has a generated `repr`,
and a generated `repr` prints itself into every traceback that has one of these in a
frame, into `logger.debug("%s", grant)`, and into a pytest failure dump. The cache is
covered by the same rule for the same reason -- a cache is one more place a secret sits.
"""

import base64
import binascii
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pigrocrm.core.gmail.errors import (
    CredentialRevoked,
    GmailUnavailable,
    GoogleCallFailed,
)
from pigrocrm.core.gmail.transport import GmailTransport

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Google's `invalid_grant` covers revoked, expired and never-issued grants alike. All
# three are terminal for us, and all three mean the same thing to the user: re-consent.
_INVALID_GRANT = "invalid_grant"
# Refresh a minute early rather than discovering expiry mid-sync.
_EXPIRY_MARGIN_SECONDS = 60

# The two Italian labels that reach a user through `GmailUnavailable`. Named because
# both appear in an error the user reads, and B1-6 raises the first of them too.
_WHAT_EXCHANGE = "scambio del codice di autorizzazione"
_WHAT_REFRESH = "rinnovo del token di accesso"
# `CredentialRevoked` names the mailbox in the sentence it shows. When the row does not
# exist yet -- the code exchange -- there is no address to name.
_UNNAMED_MAILBOX = "la casella collegata"


@dataclass(frozen=True)
class TokenGrant:
    """What one authorization-code exchange produced.

    `scopes` is what Google *granted*, never what was requested: capability is derived
    from the granted set at the point of use (see `GoogleAccount.scopes_granted`), and
    a subset grant is a normal outcome of the consent screen, not an error.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    scopes: tuple[str, ...]
    subject: str
    email_address: str
    expires_in: int


@dataclass(frozen=True)
class _CachedToken:
    value: str = field(repr=False)
    expires_at: float


def _decode_id_token_claims(id_token: str) -> dict[str, str]:
    """Reads the payload of the ID token *without* verifying its signature, and that
    is correct here: the token arrived over TLS directly from Google's token endpoint
    in response to a request carrying our client secret, which is precisely the case
    OpenID Connect exempts from signature verification (OIDC Core 3.1.3.7). It is
    never accepted from a browser, a redirect, or any other party.

    Every malformed shape returns `{}` rather than raising: a callback that answered
    500 because Google sent a segment this parser did not expect would be an outage
    with no user-visible cause. The caller refuses on the empty subject instead, which
    is a sentence somebody can act on.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        return {}
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded).decode())
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(claims, dict):
        return {}
    return {k: str(v) for k, v in claims.items() if isinstance(k, str)}


class GoogleTokenClient:
    """One per process, shared across requests: the cache is the whole point.

    The cache is in memory and per process. Two API workers therefore each hold their
    own, which is correct -- an access token is not worth a shared store, and a second
    exchange per worker per hour is not the failure this class exists to prevent.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        transport: GmailTransport,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        # Monotonic, not wall clock: an NTP step backwards must not extend a token's
        # apparent life, and a step forwards must not expire every cached token at
        # once. This is the one clock in this codebase that is deliberately not
        # `clock.today_local()` -- a token's remaining life is a duration, not a
        # date, and no timezone or calendar is involved in measuring it.
        self._clock = clock
        self._cache: dict[UUID, _CachedToken] = {}

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> TokenGrant:
        payload = self._post(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            what=_WHAT_EXCHANGE,
            account_id=None,
            email_address="",
        )
        refresh_token = str(payload.get("refresh_token") or "")
        claims = _decode_id_token_claims(str(payload.get("id_token") or ""))
        email_address = claims.get("email", "")
        if not refresh_token:
            # Without `prompt=consent`, Google omits the refresh token when the user has
            # already consented once. Storing the row anyway builds an account that can
            # never refresh and fails on day two instead of now. `CredentialRevoked` is
            # the right class even though nothing was revoked: it is the exception that
            # means "this grant is unusable and only re-consenting fixes it", which is
            # exactly the situation and exactly the instruction the user needs.
            raise CredentialRevoked(UUID(int=0), email_address or "la casella selezionata")
        granted = str(payload.get("scope") or "").split()
        # The exchange does not seed `_cache`: there is no account id yet, and keying
        # this token on anything invented here would either collide with a real row or
        # never be read again. B1-6 calls `forget` after writing the row instead.
        return TokenGrant(
            access_token=str(payload.get("access_token") or ""),
            refresh_token=refresh_token,
            scopes=tuple(granted),
            subject=claims.get("sub", ""),
            email_address=email_address,
            expires_in=int(payload.get("expires_in") or 0),
        )

    def access_token(self, *, account_id: UUID, email_address: str, refresh_token: str) -> str:
        cached = self._cache.get(account_id)
        now = self._clock()
        if cached is not None and cached.expires_at > now:
            return cached.value

        payload = self._post(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            what=_WHAT_REFRESH,
            account_id=account_id,
            email_address=email_address,
        )
        token = str(payload.get("access_token") or "")
        if not token:
            # A 200 with no token is what a captive portal or a misrouted proxy
            # answers. Caching the empty string would send `Authorization: Bearer `
            # for the next hour and fail far away from the cause.
            raise GmailUnavailable(_WHAT_REFRESH, 200)
        expires_in = int(payload.get("expires_in") or 0)
        self._cache[account_id] = _CachedToken(
            value=token,
            expires_at=now + max(0, expires_in - _EXPIRY_MARGIN_SECONDS),
        )
        return token

    def forget(self, account_id: UUID) -> None:
        """Drop a cached token, on disconnect or on re-authorisation. In both cases the
        cached value belongs to a grant that no longer applies."""
        self._cache.pop(account_id, None)

    def _post(
        self,
        fields: dict[str, str],
        *,
        what: str,
        account_id: UUID | None,
        email_address: str,
    ) -> dict[str, Any]:
        try:
            return self._transport.form(GOOGLE_TOKEN_URL, fields, what=what)
        except GoogleCallFailed as failed:
            if failed.failure.error_code == _INVALID_GRANT:
                # Terminal. Not cached as a failure either: the user may re-consent
                # between two calls, and a cached refusal would hide that. Nothing is
                # written to `_cache` on any failure path, so a transient outage does
                # not leave a poisoned entry behind either.
                raise CredentialRevoked(
                    account_id or UUID(int=0), email_address or _UNNAMED_MAILBOX
                ) from failed
            raise GmailUnavailable(what, failed.failure.status) from failed
