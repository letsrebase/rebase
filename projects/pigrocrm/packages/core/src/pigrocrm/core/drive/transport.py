"""Google Drive over the stdlib, and the two ways a token for it is obtained.

No Google client library: `urllib.request` and `json` are enough for the handful of
endpoints this needs, and adding `google-api-python-client` to `packages/core` would
drag a large transitive tree into the one package the architecture test keeps
deliberately small.

**Why this is a module of its own.** `storage/gdrive.py` used to do three jobs in one
class: sign a service-account JWT to get a token, run the HTTP mechanics (retry,
backoff, error decoding, the `Authorization` header), and decide where a document
lives on Drive. Slice 9 needs the middle job driven by a *user* OAuth token -- the
refresh token on `google_drive_accounts`, renewed through the `GoogleTokenClient`
slice 5 already has -- while the placement rules stay exactly as they are (spec 5.4).
So the credential became a seam: `DriveTransport` asks a `TokenProvider` for a bearer
token and knows nothing else about it, and there are two providers,
`ServiceAccountTokens` (what every existing installation is configured with) and
`UserTokens` (the connected-account credential). The fake in `tests/fakes/fake_drive.py`
does not change, because what it fakes -- the network -- did not move.

Nothing here logs. A refresh token, a private key and a bearer token all pass through
this module, and none of them may reach an exception message, a `repr` or a traceback:
`_decode` reports Google's own `error.message` and never the request, and neither
provider has a generated `repr` that would print its own credential (which is why
`UserTokens`, being a dataclass, marks its refresh token `repr=False`).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode
from uuid import UUID

import jwt

from pigrocrm.core.config import Settings
from pigrocrm.core.drive.errors import DriveCredentialRevoked, DriveUnavailable
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.errors import CredentialRevoked, GmailUnavailable
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.google.credential import open_refresh_token
from pigrocrm.core.http import HttpCall, SleepFn, call_with_retry, urllib_call

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Not "drive.file". That narrower scope only grants visibility into files the app
# itself created or that a user opened through an interactive picker -- neither of
# which happens for decision 4's second sanctioned setup, "a folder explicitly shared
# with [the service account]" by an admin who created it beforehand. A service
# account cannot drive a picker, so under `drive.file` that folder would simply not
# exist as far as this class could see, and every call would 404. The broad `drive`
# scope is what a service account managing a pre-existing, admin-shared root actually
# needs.
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
# Operator note, not a code fix: this scope is bearer-broad -- wider than a single
# Shared-Drive deployment strictly needs, since a leaked access token would grant
# access to every Shared Drive and folder this service account can see, not only
# PigroCRM's own root. Keep the blast radius small at the Google Cloud console
# instead: share only PIGROCRM_GDRIVE_ROOT_FOLDER_ID (and nothing else) with this
# service account's address, and never reuse the same service account for any other
# integration. A `UserTokens` installation has no such note to read: its scopes are
# the ones the titolare granted on the consent screen (`DRIVE_REQUESTED_SCOPES`).
TOKEN_LIFETIME_SECONDS = 3600
# Refresh a minute early rather than discovering expiry mid-upload.
TOKEN_REFRESH_MARGIN_SECONDS = 60
# The entity name a failure of this transport is reported under *by default*, which is
# the storage's subject: `GDriveStorage` reaches Drive for the bytes of a document, and
# the API and the MCP adapter both render `Conflict.details["entity"]`, so changing this
# would change what a user reads for a Google outage during an upload or a download.
#
# It is no longer the only subject. Slice 9C reaches the same transport for a file the
# titolare *named*, where "document_blob" is wrong twice over -- there is no CRM document
# involved, and an adapter routing on the entity would point at the wrong thing. The
# reader owns that correction rather than this module: it re-stamps exactly this entity
# on its way out (`reader.py::_guarded`), so the rule holds for any transport it was
# handed, including the doubles its tests compose. Hence public: that re-stamp names the
# constant instead of repeating the string.
TRANSPORT_ENTITY = "document_blob"


SignAssertion = Callable[[dict[str, Any]], str]


def _decode(status: int, payload: bytes, what: str) -> dict[str, Any]:
    """The parsed body, or a `Conflict` naming what failed and with which status.

    Google's own message is included but the raw body is not: it can carry the folder
    tree and the service-account address, neither of which belongs in a problem
    document a browser will render. Never the request headers or body either, so no
    bearer token or key material can reach an exception message from here.

    `what` travels in the details as well as in the sentence, and that is load-bearing
    rather than decorative: obtaining the token is itself an HTTP call through this
    same function, so a caller that inspects a failure -- `GDriveStorage.get` turning a
    404 into `NotFound`, `verify_root_accessible` turning any failure into "share the
    folder" -- would otherwise mistake a refused *credential* for a missing document or
    an unshared folder, and send somebody to fix the wrong thing.
    """
    if status >= 400:
        detail = ""
        try:
            detail = str(json.loads(payload.decode()).get("error", {}).get("message", ""))
        except (ValueError, AttributeError):
            detail = ""
        raise Conflict(
            TRANSPORT_ENTITY,
            f"{what} fallita ({status}){': ' + detail if detail else ''}",
            upstream_status=status,
            what=what,
        )
    return json.loads(payload.decode()) if payload else {}


class TokenProvider(Protocol):
    """Where a bearer token for Drive comes from, and how to say it is stale.

    `forget` is not a convenience: a provider caches, and a 401 from Drive is the one
    failure a *fresh* token fixes rather than a retry with the same one (see
    `DriveTransport.json`). Both implementations cache, so both need to be told.
    """

    def access_token(self) -> str: ...

    def forget(self) -> None: ...


class ServiceAccountTokens:
    """The credential every installation configured before slice 9: a service account
    whose private key signs a JWT that Google exchanges for an access token.

    Kept as-is, deliberately -- "il service account resta supportato per chi lo aveva
    configurato" (spec 5.4). Its operational requirement has not changed either: a
    service account has no Drive storage quota of its own, so the root folder must live
    on a Shared Drive or be shared with this account's address (see
    `GDriveStorage.verify_root_accessible`).
    """

    def __init__(
        self,
        service_account_json: str,
        *,
        http: HttpCall | None = None,
        sign_assertion: SignAssertion | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._credentials: dict[str, Any] = json.loads(service_account_json)
        self._http: HttpCall = http or urllib_call
        # Injectable so tests can prove the rest of this module -- URL building, query
        # escaping, multipart framing, error handling -- without a real RSA key ever
        # existing in this repository.
        self._sign: SignAssertion = sign_assertion or self._sign_with_private_key
        # Injectable so retry-with-backoff tests don't actually block for seconds at a
        # time; production gets real `time.sleep`.
        self._sleep: SleepFn = sleep or time.sleep
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _sign_with_private_key(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._credentials["private_key"], algorithm="RS256")

    def access_token(self) -> str:
        if self._token is not None and time.time() < self._token_expires_at:
            return self._token
        issued = int(time.time())
        assertion = self._sign(
            {
                "iss": self._credentials["client_email"],
                "scope": DRIVE_SCOPE,
                "aud": self._credentials.get("token_uri", GOOGLE_TOKEN_URL),
                "iat": issued,
                "exp": issued + TOKEN_LIFETIME_SECONDS,
            }
        )
        body = urlencode(
            {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
        ).encode()
        status, payload, _ = call_with_retry(
            self._http,
            self._sleep,
            "POST",
            self._credentials.get("token_uri", GOOGLE_TOKEN_URL),
            {"Content-Type": "application/x-www-form-urlencoded"},
            body,
        )
        parsed = _decode(status, payload, "autenticazione Google")
        self._token = str(parsed["access_token"])
        self._token_expires_at = time.time() + TOKEN_LIFETIME_SECONDS - TOKEN_REFRESH_MARGIN_SECONDS
        return self._token

    def forget(self) -> None:
        self._token = None
        self._token_expires_at = 0.0


@dataclass(frozen=True, kw_only=True)
class UserTokens:
    """The Drive credential of a connected Google account: a refresh token on
    `google_drive_accounts`, exchanged for an access token by the same
    `GoogleTokenClient` the Gmail side already uses.

    No cache of its own, on purpose. `GoogleTokenClient` already caches per account id
    on Google's own `expires_in`, and a second cache in front of it would be a second
    place a token sits, a second clock to get wrong, and a second thing `forget` would
    have to clear consistently. `forget` therefore just drops the client's entry --
    which is also what `GoogleDriveOAuthService` does on disconnect and on
    re-authorisation, for the same reason.

    `refresh_token` is `repr=False`: a dataclass has a generated `repr`, and a
    generated `repr` prints itself into every traceback that has one of these in a
    frame, into `logger.debug("%s", provider)`, and into a pytest failure dump. Same
    rule, and the same reason, as `gmail/tokens.py`'s own dataclasses.
    """

    account_id: UUID
    email_address: str
    refresh_token: str = field(repr=False)
    tokens: GoogleTokenClient

    def access_token(self) -> str:
        """The account's access token, with Google's refusals re-shaped as Drive ones.

        `GoogleTokenClient` is shared with the Gmail side and says so in its failures:
        `CredentialRevoked` reads "ricollega la casella da Impostazioni → Gmail" under
        the entity `google_account`, and `GmailUnavailable` reads "Gmail non ha
        risposto correttamente". Both reach the problem document and the MCP message
        verbatim, so a revoked *Drive* grant would tell somebody to reconnect their
        mailbox -- an instruction that fixes nothing and hides what actually broke --
        and a Drive outage would be reported as a Gmail outage.

        The distinction the failure carries is preserved exactly (terminal and cured
        by re-consenting, versus transient and cured by waiting); only the credential
        it names changes. Neither message mentions the refresh token, and neither
        `details` dict carries it.

        The terminal one is `DriveCredentialRevoked`, a *type* and not a re-worded
        `Conflict`: a caller that reacts to a revocation -- 9C marks the row `revoked`
        on it -- has to match on something, and flattening it here would throw away the
        one fact it needs while keeping the sentence. `DriveTransport` never touches
        this exception, so the class the caller sees is the class raised here.
        """
        try:
            return self.tokens.access_token(
                account_id=self.account_id,
                email_address=self.email_address,
                refresh_token=self.refresh_token,
            )
        except CredentialRevoked as revoked:
            raise DriveCredentialRevoked(self.account_id, self.email_address) from revoked
        except GmailUnavailable as unavailable:
            raise DriveUnavailable(
                int(unavailable.details.get("upstream_status", 0))
            ) from unavailable

    def forget(self) -> None:
        self.tokens.forget(self.account_id)


class DriveTransport:
    """The Drive HTTP surface: one bearer token per request, retry with backoff, and
    Google's errors turned into a `Conflict` a caller can render.

    Knows nothing about folders, keys or documents -- that is `GDriveStorage`'s job --
    and nothing about where its token comes from beyond `TokenProvider`.
    """

    def __init__(
        self,
        *,
        tokens: TokenProvider,
        http: HttpCall | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._tokens = tokens
        self._http: HttpCall = http or urllib_call
        self._sleep: SleepFn = sleep or time.sleep

    def _attempt(
        self,
        method: str,
        url: str,
        body: bytes | None,
        content_type: str | None,
        max_bytes: int | None,
    ) -> tuple[int, bytes]:
        headers = {"Authorization": f"Bearer {self._tokens.access_token()}"}
        if content_type:
            headers["Content-Type"] = content_type
        status, payload, _ = call_with_retry(
            self._http, self._sleep, method, url, headers, body, max_bytes=max_bytes
        )
        return status, payload

    def _send(
        self,
        method: str,
        url: str,
        body: bytes | None,
        content_type: str | None,
        max_bytes: int | None = None,
    ) -> tuple[int, bytes]:
        """One attempt, plus exactly one more if Drive answered 401.

        A 401 is the one failure a *fresh* token fixes: the cached one was minted
        before a revocation, a re-consent, or a scope change, and `call_with_retry`
        deliberately does not touch it because repeating the same request with the same
        stale token cannot succeed. So the provider is told to drop it and the request
        is made once more -- once, not in a loop: a grant that is genuinely revoked
        answers 401 to every attempt, and spinning against Google would replace a clear
        `Conflict` with an outage.
        """
        status, payload = self._attempt(method, url, body, content_type, max_bytes)
        if status == 401:
            self._tokens.forget()
            status, payload = self._attempt(method, url, body, content_type, max_bytes)
        return status, payload

    def json(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        what: str,
    ) -> dict[str, Any]:
        status, payload = self._send(method, url, body, content_type)
        return _decode(status, payload, what)

    # Two builtins are shadowed in this class namespace, and both are shadowed from
    # the `def` that names them onward: `json` above (so no annotation below it may say
    # `json`, which is why `json`'s own `body: bytes | None` annotation is written
    # before it and is still the builtin) and `bytes` here. An annotation is evaluated
    # in the class namespace at `def` time, so a later `-> bytes` would resolve to this
    # method instead of the type -- the exact failure `tests/test_module_imports.py`
    # exists to catch. Hence the order: `json` first, `bytes` last, nothing after it.
    # The names are worth it: `transport.json(...)` / `transport.bytes(...)` is what the
    # call site means, and `gmail/transport.py` already reads that way.
    def bytes(self, method: str, url: str, *, what: str, max_bytes: int | None = None) -> bytes:
        """The response body itself, undecoded: a media download is a PDF, not JSON.

        `max_bytes` caps what is read off the socket at `max_bytes + 1` bytes, so a caller
        that refuses anything over `max_bytes` can tell an oversized answer from one that
        fits without the whole of it ever being buffered; the caller's own length check
        is what refuses it.

        A failure is still reported through `_decode`, which raises before returning,
        so the success path here is only ever reached with real bytes in hand.
        """
        status, payload = self._send(method, url, None, None, max_bytes)
        if status >= 400:
            _decode(status, payload, what)
        return payload


def user_transport_for(
    account: GoogleDriveAccount,
    settings: Settings,
    *,
    http: HttpCall | None = None,
    tokens: GoogleTokenClient | None = None,
) -> DriveTransport:
    """A Drive transport driven by one stored account's own credential.

    Three steps, and the reason this is a function rather than three lines at each call
    site: unseal the refresh token from the row, wrap it in a `UserTokens` provider over
    the slice 5 token client, hand that to a `DriveTransport`. Both callers that need a
    user-credentialled Drive -- the document storage (9D) and the Drive reader (9C) --
    need exactly those three steps, and a second copy of them would be a second place
    the plaintext refresh token exists as a local variable. It exists here for the
    length of this call and reaches nothing but `UserTokens`, which does not print it.

    Nothing is validated about the account beyond its credential being decipherable:
    whether this Drive may be *used* -- status, scopes -- is
    `GoogleDriveAccountService.usable`'s question, asked before anything is composed,
    and repeating it here would put the same rule in two places with no rule for which
    one wins.

    `tokens` exists so the composition can be tested against a fake of Google's token
    endpoint. Left out, a fresh `GoogleTokenClient` is built the way the API's own
    `token_client` builds one; the caller keeps the transport for as long as its
    resolution is valid, so that client's per-account cache is what stops one upload
    from becoming one OAuth round-trip per Drive call.
    """
    refresh_token = open_refresh_token(
        account.refresh_token_ciphertext, account.refresh_token_nonce, settings
    )
    return DriveTransport(
        tokens=UserTokens(
            account_id=account.id,
            email_address=account.email_address,
            refresh_token=refresh_token,
            tokens=tokens
            or GoogleTokenClient(
                client_id=settings.google_client_id,
                client_secret=settings.google_client_secret,
                transport=GmailTransport(),
            ),
        ),
        http=http,
    )
