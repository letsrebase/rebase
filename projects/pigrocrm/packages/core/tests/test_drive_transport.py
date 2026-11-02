"""The Drive HTTP layer, and the two ways a token for it is obtained.

`storage/gdrive.py` used to be one class doing three jobs: signing a service-account
JWT, HTTP mechanics (retry, backoff, error decoding), and Drive placement/identity.
Slice 9 needs the middle job driven by a *user* OAuth token instead, so the first two
now live in `drive/transport.py` and this file covers them directly.

What is faked is still only the network -- `fakes/fake_drive.py` for Drive,
`fakes/fake_gmail.py` for Google's token endpoint -- so URL building, header
injection, the retry schedule and the error decoding all run for real. Where a test
has to see the *headers* of a request (the whole point of a bearer token), it wraps
the fake rather than changing it: the fake records `(method, url)` only, and the spec
is explicit that the fake does not change.
"""

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fakes.fake_drive import FakeDrive, _File
from fakes.fake_gmail import FakeGmail

from pigrocrm.core.drive.errors import DriveCredentialRevoked
from pigrocrm.core.drive.transport import DriveTransport, ServiceAccountTokens, UserTokens
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.http import MAX_ATTEMPTS
from pigrocrm.core.storage import GDriveStorage
from pigrocrm.core.storage.gdrive import APP_PROPERTY_KEY

PDF = b"%PDF-1.7\nfinto\n"
KEY = "acme-01234567/0199abcd/v1.pdf"
REFRESH = "1//0gRefreshTokenValue"
EMAIL = "titolare@example.it"

# No real RSA key is needed anywhere in this file: the signer is injected, and the
# fake answers the token endpoint without verifying the assertion -- so no private key
# material exists in this repository at all.
SERVICE_ACCOUNT_JSON = (
    '{"type":"service_account","client_email":"pigro@example.iam.gserviceaccount.com",'
    '"private_key":"-----BEGIN PRIVATE KEY-----\\nFAKE\\n-----END PRIVATE KEY-----\\n",'
    '"token_uri":"https://oauth2.googleapis.com/token"}'
)


@dataclass
class StubTokens:
    """A `TokenProvider` that hands out `values[0]` and moves to the next on `forget`.

    Two things the real providers make hard to observe: which token reached the wire,
    and whether `forget` was called at all. Both are exactly what `DriveTransport`'s
    401 handling is judged on.
    """

    values: list[str] = field(default_factory=lambda: ["tok"])
    forgotten: int = 0

    def access_token(self) -> str:
        return self.values[0]

    def forget(self) -> None:
        self.forgotten += 1
        if len(self.values) > 1:
            self.values.pop(0)


@dataclass
class RecordingHttp:
    """Records the headers of every request, then delegates to the real fake.

    `FakeDrive` records `(method, url)` and nothing else, and the spec says it does not
    change ("il fake non cambia: e' il trasporto che finge"). Wrapping it keeps every
    bit of its Drive semantics while making the one thing it does not record --
    the `Authorization` header -- assertable.
    """

    inner: FakeDrive
    headers: list[dict[str, str]] = field(default_factory=list)

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        self.headers.append(dict(headers))
        return self.inner(method, url, headers, body, max_bytes=max_bytes)


@dataclass
class ScriptedHttp:
    """Answers with `statuses` in order, then 200s. Records what each attempt carried."""

    statuses: list[int] = field(default_factory=list)
    seen: list[dict[str, str]] = field(default_factory=list)

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        self.seen.append(dict(headers))
        status = self.statuses.pop(0) if self.statuses else 200
        if status >= 400:
            return status, json.dumps({"error": {"message": "boom"}}).encode(), {}
        return 200, json.dumps({"id": "ok"}).encode(), {}


def _token_client(fake: FakeGmail) -> GoogleTokenClient:
    return GoogleTokenClient(
        client_id="cid.apps.googleusercontent.com",
        client_secret="the-client-secret",
        transport=GmailTransport(http=fake, sleep=lambda _: None),
    )


# --- The bearer token reaches every request ------------------------------------------


def test_every_request_carries_the_providers_bearer_token() -> None:
    """The one thing a transport with a pluggable token provider must not get wrong:
    the token has to be on *every* call, not only the first. Asserted on the headers
    that actually reached the wire, not on the provider having been asked."""
    drive = FakeDrive()
    http = RecordingHttp(drive)
    storage = GDriveStorage(
        transport=DriveTransport(tokens=StubTokens(), http=http),
        root_folder_id=drive.root_id,
    )

    storage.put(KEY, PDF, "application/pdf")
    storage.get(KEY)
    storage.delete(KEY)

    assert http.headers  # a put/get/delete really did make requests
    assert all(h.get("Authorization") == "Bearer tok" for h in http.headers), http.headers


def test_a_download_returns_raw_bytes_rather_than_a_parsed_body() -> None:
    """`bytes()` exists because a media download is not JSON: parsing it would corrupt
    a PDF, and `json()` would raise on the first byte."""
    drive = FakeDrive()
    transport = DriveTransport(tokens=StubTokens(), http=drive)
    storage = GDriveStorage(transport=transport, root_folder_id=drive.root_id)
    storage.put(KEY, PDF, "application/pdf")
    file_id = next(f.id for f in drive.files.values() if f.name == "v1.pdf")

    payload = transport.bytes(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media",
        what="download da Google Drive",
    )

    assert payload == PDF


def test_a_download_reads_at_most_one_byte_past_the_callers_ceiling() -> None:
    """The caller that refuses anything over `max_bytes` needs to tell "too big" from
    "fits", and nothing more: the seam is asked for `max_bytes + 1` bytes so that the
    rest of a huge file is never buffered, and the caller's own length check is what
    refuses it."""
    drive = FakeDrive()
    file_id = drive.add_file("grande.bin", parent=drive.root_id, content=b"x" * 1000)
    transport = DriveTransport(tokens=StubTokens(), http=drive)
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

    capped = transport.bytes("GET", url, what="download", max_bytes=100)
    whole = transport.bytes("GET", url, what="download")

    assert len(capped) == 101
    assert len(whole) == 1000


# --- Retry, unchanged from the class this was extracted from --------------------------


def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    sleeps: list[float] = []
    drive = FakeDrive()
    drive.fail_with = [429]
    transport = DriveTransport(tokens=StubTokens(), http=drive, sleep=sleeps.append)

    parsed = transport.json(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{drive.root_id}?supportsAllDrives=true",
        what="richiesta a Google Drive",
    )

    assert parsed["id"] == drive.root_id
    assert len(sleeps) == 1


def test_a_non_transient_client_error_is_not_retried() -> None:
    def _fail_if_called(seconds: float) -> None:
        raise AssertionError(f"should not have slept {seconds}s for a non-transient error")

    drive = FakeDrive()
    drive.fail_with = [403]
    transport = DriveTransport(tokens=StubTokens(), http=drive, sleep=_fail_if_called)

    with pytest.raises(Conflict) as excinfo:
        transport.json("GET", "https://www.googleapis.com/drive/v3/files", what="richiesta")

    assert excinfo.value.details["status"] == 403


# --- 401: the one failure a fresh token fixes ----------------------------------------


def test_a_401_forgets_the_cached_token_and_retries_with_the_next_one() -> None:
    """A 401 from Drive means the token is stale -- revoked, or minted before a
    re-consent -- and a cached one is the only reason a request that was authorised a
    minute ago is not now. Retrying with the *same* token would be pointless, so the
    provider is told to drop it first; that is why `TokenProvider` has `forget` at all.
    """
    tokens = StubTokens(values=["stale", "fresh"])
    http = ScriptedHttp(statuses=[401])
    transport = DriveTransport(tokens=tokens, http=http, sleep=lambda _: None)

    parsed = transport.json("GET", "https://www.googleapis.com/drive/v3/files", what="richiesta")

    assert parsed["id"] == "ok"
    assert tokens.forgotten == 1
    assert [h["Authorization"] for h in http.seen] == ["Bearer stale", "Bearer fresh"]


def test_a_401_that_survives_a_fresh_token_is_reported_rather_than_retried_forever() -> None:
    """Boundedness, the half that matters: a genuinely revoked grant answers 401 to
    every attempt, and a transport that kept forgetting and retrying would spin
    against Google instead of telling the caller. Exactly one extra attempt."""
    tokens = StubTokens(values=["stale", "also-stale"])
    http = ScriptedHttp(statuses=[401, 401])
    transport = DriveTransport(tokens=tokens, http=http, sleep=lambda _: None)

    with pytest.raises(Conflict) as excinfo:
        transport.json("GET", "https://www.googleapis.com/drive/v3/files", what="richiesta")

    assert excinfo.value.details["status"] == 401
    assert tokens.forgotten == 1
    assert len(http.seen) == 2


# --- UserTokens: the slice 9 provider, over the slice 5 token client ------------------


def test_user_tokens_refresh_through_the_token_client_and_reuse_the_result() -> None:
    """`UserTokens` owns no cache of its own: `GoogleTokenClient` already caches per
    account on Google's own `expires_in`, and a second cache in front of it would be a
    second place a token sits and a second clock to get wrong."""
    fake = FakeGmail()
    account_id = uuid4()
    provider = UserTokens(
        account_id=account_id,
        email_address=EMAIL,
        refresh_token=REFRESH,
        tokens=_token_client(fake),
    )

    assert provider.access_token() == fake.access_token
    assert provider.access_token() == fake.access_token
    assert fake.token_requests == 1


def test_user_tokens_forget_makes_the_next_call_refresh_again() -> None:
    fake = FakeGmail()
    provider = UserTokens(
        account_id=uuid4(),
        email_address=EMAIL,
        refresh_token=REFRESH,
        tokens=_token_client(fake),
    )
    provider.access_token()

    provider.forget()
    provider.access_token()

    assert fake.token_requests == 2


def test_user_tokens_never_print_the_refresh_token() -> None:
    """The refresh token is the long-lived half of the credential, and a generated
    `repr` prints itself into every traceback, every `logger.debug("%s", provider)` and
    every pytest failure dump -- the same rule `gmail/tokens.py` states for its own
    dataclasses, applied to the one that now travels into the storage layer."""
    provider = UserTokens(
        account_id=uuid4(),
        email_address=EMAIL,
        refresh_token=REFRESH,
        tokens=_token_client(FakeGmail()),
    )

    assert REFRESH not in repr(provider)


def test_a_revoked_drive_grant_names_drive_and_not_the_mailbox() -> None:
    """`GoogleTokenClient` is shared with the Gmail side and its `CredentialRevoked`
    says "ricollega la casella da Impostazioni → Gmail" under the entity
    `google_account`. That sentence reaches the problem document and the MCP message
    verbatim, so left alone it would tell somebody whose *Drive* consent was revoked to
    reconnect their mailbox -- an instruction that fixes nothing and hides what actually
    broke. Driven by a real refusal from `FakeGmail`, not a hand-rolled one."""
    provider = UserTokens(
        account_id=uuid4(),
        email_address=EMAIL,
        refresh_token=REFRESH,
        tokens=_token_client(FakeGmail(revoked=True)),
    )

    with pytest.raises(DriveCredentialRevoked) as excinfo:
        provider.access_token()

    details = excinfo.value.details
    assert details["entity"] == "google_drive_account"
    assert details["reason"] == (
        f"il consenso Google Drive per {EMAIL} è stato revocato: "
        "ricollega Drive da Impostazioni → Drive"
    )
    assert "Gmail" not in details["reason"] and "casella" not in details["reason"]
    assert REFRESH not in str(details) and REFRESH not in str(excinfo.value)


def test_the_typed_revocation_escapes_the_transport_unchanged_and_without_the_token() -> None:
    """`UserTokens.access_token` is called from inside `DriveTransport._attempt`, before
    any request is built, and `DriveTransport` has its own error path (`_decode`) that
    turns Google's *response* bodies into a `Conflict`. A revoked grant never reaches a
    response, so the typed refusal must come out of `transport.json(...)` as the very
    class the provider raised -- not flattened into a bare `Conflict`, which is what a
    caller wanting to react to a revocation (9C's `mark_revoked` call site) has to
    match on. `_send` retries once on a 401 by dropping the token and asking again, so
    the second refusal is the one that escapes; both must be the same class."""
    provider = UserTokens(
        account_id=uuid4(),
        email_address=EMAIL,
        refresh_token=REFRESH,
        tokens=_token_client(FakeGmail(revoked=True)),
    )
    transport = DriveTransport(tokens=provider, http=FakeDrive(), sleep=lambda _: None)

    with pytest.raises(DriveCredentialRevoked) as excinfo:
        transport.json("GET", "https://www.googleapis.com/drive/v3/files", what="l'elenco")

    assert REFRESH not in str(excinfo.value) and REFRESH not in str(excinfo.value.details)


def test_a_drive_outage_is_not_reported_as_a_gmail_outage() -> None:
    """Same reason, the transient half: `GmailUnavailable` names Gmail, and waiting for
    Gmail to recover is not what fixes Drive. The distinction the failure carries --
    terminal and cured by re-consenting, versus transient and cured by waiting -- is
    preserved exactly; only the credential it names changes."""
    fake = FakeGmail(fail_with=[(503, b"{}", {})] * MAX_ATTEMPTS)
    provider = UserTokens(
        account_id=uuid4(),
        email_address=EMAIL,
        refresh_token=REFRESH,
        tokens=_token_client(fake),
    )

    with pytest.raises(Conflict) as excinfo:
        provider.access_token()

    details = excinfo.value.details
    assert details["entity"] == "google_drive"
    assert details["reason"] == (
        "Google Drive non ha risposto correttamente (codice 503). Riprova più tardi"
    )
    assert details["status"] == 503
    assert "Gmail" not in details["reason"]
    assert REFRESH not in str(details) and REFRESH not in str(excinfo.value)


def test_a_drive_call_with_a_user_token_carries_that_users_bearer_token() -> None:
    """End to end over both fakes: the Drive transport driven by a real
    `GoogleTokenClient` refresh, which is the whole point of the split."""
    gmail = FakeGmail(access_token="ya29.user-token")
    drive = FakeDrive()
    http = RecordingHttp(drive)
    storage = GDriveStorage(
        transport=DriveTransport(
            tokens=UserTokens(
                account_id=uuid4(),
                email_address=EMAIL,
                refresh_token=REFRESH,
                tokens=_token_client(gmail),
            ),
            http=http,
        ),
        root_folder_id=drive.root_id,
    )

    storage.put(KEY, PDF, "application/pdf")

    assert storage.get(KEY) == PDF
    assert all(h.get("Authorization") == "Bearer ya29.user-token" for h in http.headers)


# --- ServiceAccountTokens: today's provider, unchanged behaviour ----------------------


def test_service_account_tokens_ask_for_a_token_once_and_reuse_it() -> None:
    drive = FakeDrive()
    provider = ServiceAccountTokens(
        SERVICE_ACCOUNT_JSON, http=drive, sign_assertion=lambda claims: "assertion"
    )

    assert provider.access_token() == "at-1"
    assert provider.access_token() == "at-1"
    assert drive.token_requests == 1


def test_service_account_tokens_request_the_full_drive_scope() -> None:
    """`drive.file` cannot see a folder an admin shared beforehand (decision 4's second
    sanctioned setup), and a service account cannot drive a picker to be granted one.
    The scope is therefore part of this provider's contract, not an implementation
    detail -- asserted on the claims that were actually signed."""
    signed: list[dict[str, Any]] = []

    def _sign(claims: dict[str, Any]) -> str:
        signed.append(claims)
        return "assertion"

    ServiceAccountTokens(
        SERVICE_ACCOUNT_JSON, http=FakeDrive(), sign_assertion=_sign
    ).access_token()

    assert len(signed) == 1
    assert signed[0]["scope"] == "https://www.googleapis.com/auth/drive"
    assert signed[0]["iss"] == "pigro@example.iam.gserviceaccount.com"


def test_service_account_tokens_forget_makes_the_next_call_re_sign() -> None:
    drive = FakeDrive()
    provider = ServiceAccountTokens(
        SERVICE_ACCOUNT_JSON, http=drive, sign_assertion=lambda claims: "assertion"
    )
    provider.access_token()

    provider.forget()
    provider.access_token()

    assert drive.token_requests == 2


def test_service_account_tokens_never_print_the_credentials() -> None:
    provider = ServiceAccountTokens(
        SERVICE_ACCOUNT_JSON, http=FakeDrive(), sign_assertion=lambda claims: "assertion"
    )
    assert "PRIVATE KEY" not in repr(provider)


# --- from_service_account: the constructor the factory keeps using -------------------


def test_from_service_account_round_trips_a_document() -> None:
    """The same put/get/delete/signed_url contract the conformance suite runs, asserted
    once here too: `from_service_account` is what `storage_from_settings` calls, so it
    has to build a storage that actually works and not merely one that constructs."""
    drive = FakeDrive()
    storage = GDriveStorage.from_service_account(
        service_account_json=SERVICE_ACCOUNT_JSON,
        root_folder_id=drive.root_id,
        http=drive,
        sign_assertion=lambda claims: "assertion",
    )

    storage.put(KEY, PDF, "application/pdf")
    assert storage.get(KEY) == PDF
    assert storage.signed_url(KEY, timedelta(minutes=5)) is None
    storage.verify_root_accessible()  # does not raise

    storage.delete(KEY)
    with pytest.raises(NotFound):
        storage.get(KEY)


# --- Attributing a failure before interpreting it -------------------------------------
# Obtaining the token is itself a call through the same transport, so `GDriveStorage`
# has to know *which* call failed before it turns a status into a domain answer. Both
# tests below caught a real regression in the refactor that split this module out: the
# storage caught a `Conflict` and read only its status.


@dataclass
class TokensRefusedMidOperation:
    """A provider whose token serves the lookup and is refused for the download.

    Not hypothetical: `get` acquires a token twice -- once to find the file by its
    `appProperties`, once to download it -- and a cached token can reach its expiry
    between the two, so the refresh in the middle is a refusal waiting to happen.
    """

    acquisitions: int = 0

    def access_token(self) -> str:
        self.acquisitions += 1
        if self.acquisitions >= 2:
            raise Conflict(
                "document_blob",
                "autenticazione Google fallita (404)",
                status=404,
                what="autenticazione Google",
            )
        return "tok"

    def forget(self) -> None:
        pass


def test_a_404_from_the_token_endpoint_is_not_reported_as_a_missing_document() -> None:
    """A refused credential is not an absent document. Reporting it as `NotFound` would
    tell the caller the file is gone -- and a user that their document was deleted --
    when nothing was ever asked about the file at all."""
    drive = FakeDrive()
    drive.files["id-a"] = _File(
        "id-a", "v1.pdf", drive.root_id, "application/pdf", PDF, {APP_PROPERTY_KEY: KEY}
    )
    storage = GDriveStorage(
        transport=DriveTransport(tokens=TokensRefusedMidOperation(), http=drive),
        root_folder_id=drive.root_id,
    )

    with pytest.raises(Conflict) as excinfo:
        storage.get(KEY)

    assert "autenticazione Google" in excinfo.value.details["reason"]


def test_a_refused_credential_during_the_root_check_is_not_reported_as_an_unshared_folder() -> None:
    """`verify_root_accessible` exists to say what to fix at startup. Sending an
    operator to re-share a folder because the private key was rejected is exactly the
    wrong-thing-to-fix it is supposed to prevent."""
    drive = FakeDrive()
    drive.fail_with = [403]
    storage = GDriveStorage.from_service_account(
        service_account_json=SERVICE_ACCOUNT_JSON,
        root_folder_id=drive.root_id,
        http=drive,
        sign_assertion=lambda claims: "assertion",
        sleep=lambda _: None,
    )

    with pytest.raises(Conflict) as excinfo:
        storage.verify_root_accessible()

    assert "autenticazione Google" in excinfo.value.details["reason"]
