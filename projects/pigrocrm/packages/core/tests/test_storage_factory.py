"""Which credential writes the documents, and when that question gets answered.

Two ways to configure Drive after slice 9: the service account every earlier
installation set up in the environment, and the titolare's *own* connected account
writing into a folder they chose in Impostazioni → Drive. The first is fully known at
startup. The second is not known at startup at all -- the row it lives in may not exist
yet, and the API has to boot anyway, or nobody can reach the settings page that would
create it. So the second one resolves at the first operation instead, and that is the
property most of this file is about.

Nothing here touches the network. Drive is `fakes/fake_drive.py` and Google's token
endpoint is `fakes/fake_gmail.py`, so the composition under test -- unseal the stored
refresh token, exchange it through the slice 5 token client, put the resulting bearer
token on every Drive request -- runs for real, end to end, against a fake of the
network and of nothing else.
"""

import base64
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from fakes.fake_drive import FOLDER_MIME, FakeDrive
from fakes.fake_gmail import FakeGmail
from fakes.gmail_fixtures import TOKEN_KEY, gmail_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.drive.errors import DriveCredentialRevoked
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.drive.query import file_meta_url
from pigrocrm.core.drive.repository import DriveRepository
from pigrocrm.core.drive.schemas import DRIVE_SCOPE_FILE, DRIVE_SCOPE_READONLY
from pigrocrm.core.drive.transport import user_transport_for
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.crypto import seal
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.storage import lazy_drive
from pigrocrm.core.storage.errors import StorageNotConfigured
from pigrocrm.core.storage.factory import storage_from_settings
from pigrocrm.core.storage.gdrive import GDriveStorage
from pigrocrm.core.storage.lazy_drive import LazyUserDriveStorage
from pigrocrm.core.storage.local import LocalFileStorage

PDF = b"%PDF-1.7\nfinto\n"
KEY = "acme-01234567/0199abcd/v1.pdf"
OTHER_KEY = "beta-76543210/0199ffff/v1.pdf"
TTL = timedelta(minutes=5)
# Drive file ids of the shape `DriveRootsUpdate` holds a typed-in id to.
STORAGE_FOLDER = "1CartellaScritturaA"
OTHER_FOLDER = "1CartellaScritturaB"
REFRESH_TOKEN = "1//0gDriveStorageRefresh"
MAILBOX = "titolare@example.it"

# No real RSA key is needed: the service-account branch never gets as far as signing
# anything here (its root verification is stubbed), so no private key material exists
# in this repository at all.
SERVICE_ACCOUNT_JSON = (
    '{"type":"service_account","client_email":"pigro@example.iam.gserviceaccount.com",'
    '"private_key":"-----BEGIN PRIVATE KEY-----\\nFAKE\\n-----END PRIVATE KEY-----\\n",'
    '"token_uri":"https://oauth2.googleapis.com/token"}'
)


@dataclass
class RecordingHttp:
    """Records the headers of every request, then delegates to the real fake.

    `FakeDrive` records `(method, url)` and the parsed query string, deliberately not
    the headers. Wrapping it keeps every bit of its Drive semantics while making the
    one thing this file has to see -- *whose* bearer token reached the wire -- an
    assertion rather than a claim.
    """

    inner: FakeDrive
    headers: list[dict[str, str]] = field(default_factory=list)

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.headers.append(dict(headers))
        return self.inner(method, url, headers, body)


def _account(
    session: Session,
    *,
    status: str = "active",
    storage_folder_id: str | None = STORAGE_FOLDER,
    updated_at: datetime | None = None,
    scopes: tuple[str, ...] = (DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FILE),
    ruolo: str = "admin",
) -> GoogleDriveAccount:
    """A connected Drive with a write folder chosen -- the state the lazy storage has
    to find. Its owner is an admin unless `ruolo` says otherwise: only an admin's row
    may supply the space's storage (REB-457).

    The refresh token is really sealed, with the same key `gmail_settings()` publishes,
    so the unsealing under test runs for real rather than reading a plaintext column
    production never has.
    """
    user = User(
        email=f"drive-{uuid4().hex[:8]}@example.it",
        nome="Titolare",
        password_hash="x",
        ruolo=ruolo,
        attivo=True,
    )
    session.add(user)
    session.flush()
    ciphertext, nonce = seal(REFRESH_TOKEN, TOKEN_KEY)
    account = GoogleDriveAccount(
        user_id=user.id,
        google_sub=f"sub-{user.id}",
        email_address=MAILBOX,
        refresh_token_ciphertext=ciphertext,
        refresh_token_nonce=nonce,
        scopes_granted=list(scopes),
        status=status,
        root_folder_ids=[],
        storage_folder_id=storage_folder_id,
    )
    if updated_at is not None:
        account.updated_at = updated_at
    session.add(account)
    session.flush()
    return account


def _sessions(db_session: Session) -> Callable[[], Session]:
    """A `session_factory` the lazy storage may open *and close* freely.

    It cannot be `lambda: db_session`: the storage owns the lifecycle of every session
    it opens (production hands it a `sessionmaker`), and closing the suite's own
    session would end the transaction the `db_session` fixture rolls back at the end of
    the test. Binding a fresh `Session` to the same `Connection` with
    `create_savepoint` gives a genuinely independent session that still sees this
    test's uncommitted rows, and whose own commits -- `mark_revoked` does commit -- nest
    inside the fixture's transaction instead of escaping it.
    """
    bind = db_session.get_bind()
    return lambda: Session(bind=bind, join_transaction_mode="create_savepoint")


def _token_client(gmail: FakeGmail, settings: Settings) -> GoogleTokenClient:
    return GoogleTokenClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        transport=GmailTransport(http=gmail, sleep=lambda _: None),
    )


def _lazy(
    db_session: Session,
    *,
    drive: FakeDrive | RecordingHttp,
    gmail: FakeGmail,
    sessions: Callable[[], Session] | None = None,
) -> LazyUserDriveStorage:
    settings = gmail_settings(storage_backend="gdrive")
    return LazyUserDriveStorage(
        sessions or _sessions(db_session),
        settings,
        http=drive,
        tokens=_token_client(gmail, settings),
    )


def _fail_if_called() -> Session:
    raise AssertionError("nessuna sessione va aperta qui")


# --- storage_from_settings: which of the two Drive routes, decided once --------------


def test_a_configured_service_account_still_gets_the_service_account_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec 5.4: il service account resta supportato per chi lo aveva configurato.

    An installation with both variables set keeps getting exactly what it had -- and in
    particular must not be handed a storage that waits for somebody to connect a Drive
    account it has no need of."""
    monkeypatch.setattr(GDriveStorage, "verify_root_accessible", lambda self: None)
    settings = gmail_settings(
        storage_backend="gdrive",
        gdrive_service_account_json=SERVICE_ACCOUNT_JSON,
        gdrive_root_folder_id="1CartellaRadiceSAX",
    )

    storage = storage_from_settings(settings, session_factory=_fail_if_called)

    assert isinstance(storage, GDriveStorage)
    assert not isinstance(storage, LazyUserDriveStorage)


def test_gdrive_without_a_service_account_and_without_a_session_factory_is_refused() -> None:
    """The user-credential route needs a way to reach the database, because the folder
    it writes into lives in a row. An adapter that selects `gdrive` and hands over no
    session factory has configured neither route -- and the refusal is the one that
    names *both* of them, since that is the choice the reader has to make."""
    settings = gmail_settings(storage_backend="gdrive")

    with pytest.raises(ValidationFailed) as excinfo:
        storage_from_settings(settings)

    reason = excinfo.value.details["reason"]
    assert "PIGROCRM_GDRIVE_SERVICE_ACCOUNT_JSON" in reason
    assert "collega Drive da Impostazioni e scegli la cartella di scrittura" in reason
    # No slice number: an operator reading a startup failure has no map of this
    # project's slices.
    assert "slice" not in reason


def test_a_half_configured_service_account_is_still_a_startup_failure() -> None:
    """The trap this branch exists to avoid: somebody sets the JSON key, forgets the
    root folder id, and -- because a session factory happens to be available -- silently
    gets the *other* credential instead of being told what is missing. Naming either
    service-account variable is a statement of intent, so the other one is then
    required, and the failure is loud."""
    settings = gmail_settings(
        storage_backend="gdrive", gdrive_service_account_json=SERVICE_ACCOUNT_JSON
    )

    with pytest.raises(ValidationFailed):
        storage_from_settings(settings, session_factory=_fail_if_called)


def test_gdrive_with_a_session_factory_returns_a_lazy_storage_and_opens_no_session() -> None:
    """The whole reason this storage is lazy: the API must start before the titolare has
    connected Drive. Construction therefore reads nothing -- proven by a factory that
    fails the test if it is ever called."""
    storage = storage_from_settings(
        gmail_settings(storage_backend="gdrive"), session_factory=_fail_if_called
    )

    assert isinstance(storage, LazyUserDriveStorage)


def test_local_stays_the_default(tmp_path: Path) -> None:
    settings = gmail_settings(storage_local_root=str(tmp_path))
    assert isinstance(storage_from_settings(settings, session_factory=None), LocalFileStorage)


# --- DriveRepository.storage_account: the one row that may be written into -----------


def test_storage_account_finds_the_active_account_that_has_a_write_folder(
    db_session: Session,
) -> None:
    account = _account(db_session)
    assert DriveRepository(db_session).storage_account() is account


def test_storage_account_ignores_an_account_with_no_write_folder(db_session: Session) -> None:
    """Connected is not configured. A Drive whose `storage_folder_id` is still `None`
    can be read from, and writing into it would mean picking a folder somewhere in the
    titolare's personal Drive on their behalf."""
    _account(db_session, storage_folder_id=None)
    assert DriveRepository(db_session).storage_account() is None


@pytest.mark.parametrize("status", ["revoked", "expired", "disconnected"])
def test_storage_account_ignores_an_account_that_is_not_active(
    db_session: Session, status: str
) -> None:
    _account(db_session, status=status)
    assert DriveRepository(db_session).storage_account() is None


def test_storage_account_prefers_the_most_recently_updated_when_more_than_one_qualifies(
    db_session: Session,
) -> None:
    """The installation is single-tenant and the column is unique per *user*, so two
    qualifying rows is not the expected state -- but "not expected" is not
    "impossible": two users can each connect a Drive and each choose a folder. A query
    that raised there would take the whole documents feature down, so the most recently
    configured row wins, because it is the one somebody just chose.
    """
    older = _account(db_session, updated_at=datetime(2026, 1, 1, tzinfo=UTC))
    newer = _account(
        db_session, storage_folder_id=OTHER_FOLDER, updated_at=datetime(2026, 6, 1, tzinfo=UTC)
    )

    found = DriveRepository(db_session).storage_account()

    assert found is newer and found is not older


@pytest.mark.parametrize("ruolo", ["collaboratore", "readonly"])
def test_storage_account_never_answers_with_a_non_admin_s_row(
    db_session: Session, ruolo: str
) -> None:
    """REB-457. The write folder is the space's, not a person's: every document the
    space generates lands there, an automation's included. So a non-admin's row never
    supplies it, however recently it was touched -- a roots-only save bumps
    `updated_at`, and that must not be enough to redirect the space's documents into
    a collaboratore's Drive. The folder on their row (set over REST before the service
    refused it, or left from a time they were admin) is simply not a candidate.
    """
    admin = _account(db_session, updated_at=datetime(2026, 1, 1, tzinfo=UTC))
    _account(
        db_session,
        ruolo=ruolo,
        storage_folder_id=OTHER_FOLDER,
        updated_at=datetime(2026, 6, 1, tzinfo=UTC),
    )

    assert DriveRepository(db_session).storage_account() is admin


def test_storage_account_with_only_a_non_admin_s_folder_is_not_configured(
    db_session: Session,
) -> None:
    """The behaviour change REB-457 accepts: an installation whose only write folder
    sits on a non-admin's row reads as not configured until an admin chooses one."""
    _account(db_session, ruolo="collaboratore")
    assert DriveRepository(db_session).storage_account() is None


def test_storage_account_stops_answering_with_an_admin_who_is_demoted(
    db_session: Session,
) -> None:
    """The role is read at each resolution, from `users.ruolo` as it is now, not
    remembered from the moment the folder was chosen: a demoted admin stops supplying
    the space's storage at the next operation."""
    account = _account(db_session)
    assert DriveRepository(db_session).storage_account() is account

    owner = db_session.get(User, account.user_id)
    assert owner is not None
    owner.ruolo = "collaboratore"
    db_session.flush()

    assert DriveRepository(db_session).storage_account() is None


def test_the_lazy_storage_stops_writing_into_a_demoted_admin_s_folder(
    db_session: Session,
) -> None:
    """The same demotion, seen from the storage: the cached resolution is not a way
    around it, because every operation re-reads the row it would write into."""
    account = _account(db_session)
    storage = _lazy(db_session, drive=FakeDrive(root_id=STORAGE_FOLDER), gmail=FakeGmail())
    storage.put(KEY, PDF, "application/pdf")

    owner = db_session.get(User, account.user_id)
    assert owner is not None
    owner.ruolo = "collaboratore"
    db_session.flush()

    with pytest.raises(StorageNotConfigured):
        storage.put(OTHER_KEY, PDF, "application/pdf")


# --- Not configured: the first operation says so, and says what to do ----------------


def test_the_first_put_without_a_connected_drive_says_how_to_configure_it(
    db_session: Session,
) -> None:
    storage = _lazy(db_session, drive=FakeDrive(), gmail=FakeGmail())

    with pytest.raises(StorageNotConfigured) as excinfo:
        storage.put(KEY, PDF, "application/pdf")

    assert (
        "collega Drive e scegli la cartella di scrittura in Impostazioni → Drive"
        in excinfo.value.details["reason"]
    )


def test_storage_not_configured_is_rendered_as_a_conflict() -> None:
    """`STATUS_BY_CODE` in `apps/api/errors.py` maps `conflict` to 409, and the MCP
    adapter renders the same `details`. This exception therefore needs no new mapping
    anywhere, which is the point of it being a `Conflict`: a state of the installation
    somebody can go and fix, not a malformed request."""
    assert issubclass(StorageNotConfigured, Conflict)
    assert StorageNotConfigured().code == "conflict"


def test_get_without_a_connected_drive_does_not_report_a_missing_document(
    db_session: Session,
) -> None:
    """A `get` that fell through to `NotFound` would tell somebody their document is
    gone when what is missing is the configuration -- and they would go looking for the
    document."""
    storage = _lazy(db_session, drive=FakeDrive(), gmail=FakeGmail())

    with pytest.raises(StorageNotConfigured):
        storage.get(KEY)


def test_delete_without_a_connected_drive_does_not_report_success(db_session: Session) -> None:
    """`delete` is silent for a key that is not there, deliberately (see
    `DocumentStorage`). It must not be silent for a backend that was never configured:
    that would report a deletion nobody performed."""
    storage = _lazy(db_session, drive=FakeDrive(), gmail=FakeGmail())

    with pytest.raises(StorageNotConfigured):
        storage.delete(KEY)


def test_signed_url_without_a_connected_drive_refuses_too(db_session: Session) -> None:
    """`GDriveStorage.signed_url` answers `None` on purpose and never calls Drive, so
    this is the one operation that could plausibly have been allowed through. It is not:
    `None` from a backend that does not exist is indistinguishable from `None` from one
    that does, and the caller would learn nothing."""
    storage = _lazy(db_session, drive=FakeDrive(), gmail=FakeGmail())

    with pytest.raises(StorageNotConfigured):
        storage.signed_url(KEY, TTL)


# --- Configured: the titolare's own credential, the titolare's own folder -------------


def test_put_writes_under_the_chosen_folder_with_the_owners_own_bearer_token(
    db_session: Session,
) -> None:
    """The slice in one test: the bytes land under the folder the titolare picked -- one
    folder per customer beneath it, exactly the arrangement the service account
    produces -- and every request that put them there carried *their* access token,
    obtained by refreshing the sealed refresh token on their row."""
    _account(db_session)
    drive = FakeDrive(root_id=STORAGE_FOLDER)
    gmail = FakeGmail()
    http = RecordingHttp(drive)
    storage = _lazy(db_session, drive=http, gmail=gmail)

    storage.put(KEY, PDF, "application/pdf")

    folders = {f.name: f for f in drive.files.values() if f.mime == FOLDER_MIME}
    assert folders["acme-01234567"].parent == STORAGE_FOLDER
    assert folders["0199abcd"].parent == folders["acme-01234567"].id
    written = [f for f in drive.files.values() if f.name == "v1.pdf"]
    assert [f.data for f in written] == [PDF]
    assert written[0].parent == folders["0199abcd"].id
    assert http.headers  # the put really did make requests
    assert {h["Authorization"] for h in http.headers} == {f"Bearer {gmail.access_token}"}
    # And the token came from Google's token endpoint rather than from the Drive fake's
    # own shortcut: `FakeDrive` answers the token URL too, so a composition that had
    # accidentally kept the service-account provider would still have worked here.
    assert gmail.token_requests == 1
    assert drive.token_requests == 0


def test_get_reads_back_what_put_wrote_and_delete_removes_it(db_session: Session) -> None:
    """Read-your-own-writes, the `DocumentStorage` minimum, through the lazy wrapper --
    which is where it could plausibly break, since every call resolves the account
    again."""
    _account(db_session)
    storage = _lazy(db_session, drive=FakeDrive(root_id=STORAGE_FOLDER), gmail=FakeGmail())

    storage.put(KEY, PDF, "application/pdf")
    assert storage.get(KEY) == PDF

    storage.delete(KEY)
    with pytest.raises(NotFound):
        storage.get(KEY)


def test_the_transport_is_built_once_and_reused_across_operations(db_session: Session) -> None:
    """The resolution is cached, and the cache earns its keep for a reason beyond the
    query it saves: rebuilding the transport would rebuild the `GoogleTokenClient`,
    whose in-memory cache is the only thing between one upload and one OAuth round-trip
    per Drive call. One token request for three operations."""
    _account(db_session)
    gmail = FakeGmail()
    storage = _lazy(db_session, drive=FakeDrive(root_id=STORAGE_FOLDER), gmail=gmail)

    storage.put(KEY, PDF, "application/pdf")
    storage.get(KEY)
    storage.delete(KEY)

    assert gmail.token_requests == 1


def test_changing_the_write_folder_takes_effect_without_a_restart(db_session: Session) -> None:
    """`updated_at` is the seam. The titolare changes the folder in Impostazioni →
    Drive; the next upload has to land in the new one. A storage that resolved once and
    never looked again would keep writing into the old folder until somebody restarted
    the API, with nothing on any screen to say so."""
    account = _account(db_session)
    drive = FakeDrive(root_id=STORAGE_FOLDER)
    storage = _lazy(db_session, drive=drive, gmail=FakeGmail())
    storage.put(KEY, PDF, "application/pdf")

    account.storage_folder_id = OTHER_FOLDER
    account.updated_at = datetime.now(UTC)
    db_session.flush()

    storage.put(OTHER_KEY, b"nuovo", "application/pdf")

    folders = {f.name: f for f in drive.files.values() if f.mime == FOLDER_MIME}
    assert folders["acme-01234567"].parent == STORAGE_FOLDER
    assert folders["beta-76543210"].parent == OTHER_FOLDER


def test_disconnecting_drive_stops_the_writes_at_the_next_operation(
    db_session: Session,
) -> None:
    """The other half of re-resolution: a storage that cached the account forever would
    keep writing into somebody's Drive after they unhooked it."""
    account = _account(db_session)
    storage = _lazy(db_session, drive=FakeDrive(root_id=STORAGE_FOLDER), gmail=FakeGmail())
    storage.put(KEY, PDF, "application/pdf")

    account.status = "disconnected"
    db_session.flush()

    with pytest.raises(StorageNotConfigured):
        storage.put(KEY, PDF, "application/pdf")


# --- A revoked grant: recorded once, where the user can read it ----------------------


def test_a_revoked_grant_is_recorded_on_the_row_and_re_raised(db_session: Session) -> None:
    """A refresh answering `invalid_grant` is something only this code path can learn,
    and the fact has to outlive the request that learned it: the upload is about to fail
    and its transaction to roll back, so `mark_revoked` commits on its own behalf (the
    same contract `gmail/sync.py` relies on). The exception continues afterwards,
    unflattened, so the caller stops rather than carrying on against a dead credential.
    """
    account_id = _account(db_session).id
    storage = _lazy(
        db_session, drive=FakeDrive(root_id=STORAGE_FOLDER), gmail=FakeGmail(revoked=True)
    )

    with pytest.raises(DriveCredentialRevoked):
        storage.put(KEY, PDF, "application/pdf")

    db_session.expire_all()
    stored = db_session.get(GoogleDriveAccount, account_id)
    assert stored is not None
    assert stored.status == "revoked"
    assert stored.last_error is not None
    assert MAILBOX in stored.last_error
    assert REFRESH_TOKEN not in stored.last_error
    kinds = db_session.execute(select(Activity.kind)).scalars().all()
    assert "drive.credenziale_revocata" in kinds


def test_after_a_revocation_the_next_operation_asks_for_configuration_again(
    db_session: Session,
) -> None:
    """`mark_revoked` moves the row off `active`, so it stops being the account that may
    be written into -- and the next attempt says what to do about it rather than raising
    the same revocation forever out of a cached transport."""
    _account(db_session)
    storage = _lazy(
        db_session, drive=FakeDrive(root_id=STORAGE_FOLDER), gmail=FakeGmail(revoked=True)
    )
    with pytest.raises(DriveCredentialRevoked):
        storage.put(KEY, PDF, "application/pdf")

    with pytest.raises(StorageNotConfigured):
        storage.put(KEY, PDF, "application/pdf")


def test_a_failed_revocation_record_never_hides_the_revocation_itself(
    db_session: Session,
) -> None:
    """The bookkeeping is best effort; the failure it describes is not.

    Recording the revocation needs a second session, and everything about that second
    session can fail on its own -- a pool that has no connection left, a row somebody
    else is holding, a `commit` that loses a race. Left unguarded, any of those replaces
    `DriveCredentialRevoked` with a SQLAlchemy error: the caller then sees an internal
    failure instead of "il consenso è stato revocato", the API renders a 500 rather than
    a 409, and the row it was trying to mark is still `active` anyway -- so the reader
    loses both the sentence and the record.

    The accepted consequence is asserted too: the row *stays* `active`. That is honest
    about what a swallowed exception costs. The next refresh will meet the same
    `invalid_grant` and try to record it again, which is exactly the retry a database
    that was momentarily unavailable needs -- and it is a far better failure mode than a
    revocation reported as an outage.
    """
    account_id = _account(db_session).id
    working = _sessions(db_session)
    opened = 0

    def sessions() -> Session:
        nonlocal opened
        opened += 1
        if opened == 2:  # 1 is the resolution, 2 is the revocation record
            raise RuntimeError("nessuna connessione disponibile nel pool")
        return working()

    storage = _lazy(
        db_session,
        drive=FakeDrive(root_id=STORAGE_FOLDER),
        gmail=FakeGmail(revoked=True),
        sessions=sessions,
    )

    with pytest.raises(DriveCredentialRevoked):
        storage.put(KEY, PDF, "application/pdf")

    assert opened == 2  # the record really was attempted, and really did fail
    db_session.expire_all()
    stored = db_session.get(GoogleDriveAccount, account_id)
    assert stored is not None and stored.status == "active"


# --- user_transport_for: the token composition, written once --------------------------


def test_user_transport_for_unseals_the_stored_token_and_refreshes_it_as_the_user(
    db_session: Session,
) -> None:
    """One helper composes a Drive transport from a stored account, and these are the
    three steps it has to get right: the ciphertext columns are unsealed with the
    installation's key, the plaintext refresh token is what gets posted to Google's
    token endpoint, and the access token that comes back is what every Drive request
    carries.

    A module-level helper because the Drive *reader* needs the identical three steps,
    and a second copy of them would be a second place a refresh token is handled.
    """
    account = _account(db_session)
    http = RecordingHttp(FakeDrive(root_id=STORAGE_FOLDER))
    gmail = FakeGmail()
    settings = gmail_settings()

    transport = user_transport_for(
        account, settings, http=http, tokens=_token_client(gmail, settings)
    )
    transport.json("GET", file_meta_url(STORAGE_FOLDER, fields="id"), what="la cartella")

    assert [h["Authorization"] for h in http.headers] == [f"Bearer {gmail.access_token}"]
    posted = parse_qs((gmail.requests[-1].body or b"").decode())
    assert posted["refresh_token"] == [REFRESH_TOKEN]
    assert posted["grant_type"] == ["refresh_token"]


def test_user_transport_for_reports_a_wrong_key_as_a_credential_problem(
    db_session: Session,
) -> None:
    """`unseal` refuses before anything is composed, and names the environment variable
    rather than the ciphertext. Asserted here because the storage path is the one most
    likely to meet a rotated `PIGROCRM_GOOGLE_TOKEN_KEY` first."""
    account = _account(db_session)
    wrong_key = gmail_settings(google_token_key=base64.b64encode(b"z" * 32).decode())

    with pytest.raises(Conflict) as excinfo:
        user_transport_for(account, wrong_key)

    assert "PIGROCRM_GOOGLE_TOKEN_KEY" in excinfo.value.details["reason"]


# --- One resolution, however many threads ask for it at once -------------------------


def test_two_threads_meeting_an_unresolved_storage_resolve_it_once(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The instance that reaches production is built once per process and shared by
    every request, and FastAPI runs sync endpoints in a thread pool -- so two uploads
    can meet a cold (or newly invalidated) cache at the same instant. Unguarded, each
    would resolve, each would build its own transport, and one of the two
    `GoogleTokenClient`s would be thrown away along with the access token it had just
    paid an OAuth round-trip for. The upload that lost the race then re-authenticates
    for no reason, and a revoked grant can be recorded twice.

    So resolution is serialised, and this is the test of it: one transport built and one
    token exchanged, for two concurrent `put`s. Both assertions matter -- the build count
    is what the lock guarantees structurally, and the token count is the cost the second
    build would have imposed.

    The delay in the session factory is the same device `apps/api/tests/test_deps.py`
    uses on the engine's own cold start: it widens a window that already exists so the
    race is exercised on purpose rather than when a machine happens to be slow. It sits
    where the resolution reads the row, which is inside the guarded region, so the
    thread that arrives second is genuinely made to wait for the first.
    """
    _account(db_session)
    gmail = FakeGmail()
    drive = FakeDrive(root_id=STORAGE_FOLDER)

    builds = 0
    builds_lock = threading.Lock()
    real_user_transport_for = lazy_drive.user_transport_for

    def counting_user_transport_for(*args: object, **kwargs: object) -> object:
        nonlocal builds
        with builds_lock:
            builds += 1
        return real_user_transport_for(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(lazy_drive, "user_transport_for", counting_user_transport_for)

    working = _sessions(db_session)

    def slow_sessions() -> Session:
        time.sleep(0.05)
        return working()

    storage = _lazy(db_session, drive=drive, gmail=gmail, sessions=slow_sessions)

    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def upload(key: str) -> None:
        start.wait()
        try:
            storage.put(key, PDF, "application/pdf")
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=upload, args=(key,)) for key in (KEY, OTHER_KEY)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert builds == 1, f"expected one transport to be built, got {builds}"
    assert gmail.token_requests == 1


def test_a_revocation_is_never_recorded_against_a_resolution_that_moved(
    db_session: Session,
) -> None:
    """The account marked `revoked` must be the one whose credential failed -- and the
    only way to know that is to carry the resolution the failure came from.

    `_record_revocation` used to read `self._resolution`, which is "whichever account is
    cached *now*". This object is shared by every request in the process and re-resolves
    whenever the row changes, so "now" is not "then": a settings change, a disconnect
    and reconnect, or a concurrent operation that met a newer row can all replace the
    resolution between the failed refresh and the bookkeeping. What was then written was
    a revocation stamped on a credential that had never been asked for anything --
    a healthy Drive shown as revoked on the Drive settings page, and the failing one left
    `active`.

    So `_run` hands `_record_revocation` the resolution it actually used, and the record
    happens only if that is still the current one (identity, not equality: two
    resolutions of the same row are still two different attempts). The accepted
    consequence is asserted too -- when the resolution moved, nothing is recorded about
    either account: the next operation resolves again and meets the same
    `invalid_grant`, which is the retry this bookkeeping is best-effort for, and that is
    a far better outcome than a fact recorded about the wrong account.

    The swap is made from inside the token exchange that is about to answer
    `invalid_grant`, which is the one moment production's own race has: the failure
    exists, and the resolution it belongs to is no longer the cached one.
    """
    now = datetime.now(UTC)
    # The account that gets resolved: `storage_account()` answers with the most recently
    # updated row, so the stamps are explicit rather than left to two flushes racing.
    failing = _account(db_session, updated_at=now)
    other = _account(db_session, updated_at=now - timedelta(hours=1))
    settings = gmail_settings(storage_backend="gdrive")
    gmail = FakeGmail(revoked=True)
    drive = FakeDrive(root_id=STORAGE_FOLDER)
    swaps: list[str] = []

    def swap_then_refuse(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        swaps.append(url)
        storage._resolution = lazy_drive._Resolution(
            account_id=other.id,
            email_address=other.email_address,
            updated_at=other.updated_at,
            storage=GDriveStorage(
                transport=user_transport_for(other, settings, http=drive),
                root_folder_id=STORAGE_FOLDER,
            ),
        )
        return gmail(method, url, headers, body)

    storage = LazyUserDriveStorage(
        _sessions(db_session),
        settings,
        http=drive,
        tokens=GoogleTokenClient(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            transport=GmailTransport(http=swap_then_refuse, sleep=lambda _: None),
        ),
    )

    with pytest.raises(DriveCredentialRevoked) as caught:
        storage.put(KEY, PDF, "application/pdf")

    assert swaps  # the resolution really was replaced before the record ran
    assert caught.value.details["email_address"] == MAILBOX
    db_session.expire_all()
    # The account that was swapped in is untouched: it was never asked for a token.
    swapped_in = db_session.get(GoogleDriveAccount, other.id)
    assert swapped_in is not None
    assert swapped_in.status == "active"
    assert swapped_in.last_error is None
    # And no revocation was recorded against *either* account, which is the accepted
    # cost of not guessing. Asserted per account rather than as an empty `Activity`
    # table: this session is shared with whatever else the run has already written, so
    # "nothing anywhere" is a claim about the suite and not about this behaviour.
    still_failing = db_session.get(GoogleDriveAccount, failing.id)
    assert still_failing is not None and still_failing.status == "active"
    revocations = (
        db_session.execute(
            select(Activity.entity_id).where(
                Activity.entity_type == "google_drive_account",
                Activity.kind == "drive.credenziale_revocata",
                Activity.entity_id.in_([failing.id, other.id]),
            )
        )
        .scalars()
        .all()
    )
    assert revocations == []


def test_a_grant_without_the_write_scope_refuses_the_upload_with_guidance(
    db_session: Session,
) -> None:
    """`drive.file` is the scope that lets this credential create a file at all, and
    until now nothing checked it before a write.

    A titolare can arrive here honestly: the consent screen lets a person tick fewer
    scopes than were asked for, and a Google client whose scope list changes between
    two authorisations leaves older grants short of one. What happened then was a
    `files.create` refused by Google as a 403 inside `GDriveStorage`, surfacing as
    «caricamento su Drive fallito (403)» -- an outage-shaped sentence for a
    configuration problem, with no mention of the authorisation that is missing or of
    the screen that grants it.

    So resolution refuses first, with the very `Conflict`
    `GoogleDriveAccountService.usable` raises for a missing scope, and no Drive request
    is made to get there: one rule, one sentence, wherever a missing scope is met.
    Deliberately not `StorageNotConfigured`: nothing here is unconfigured -- the account
    is connected and the folder is chosen -- and the action to take is not the one that
    refusal names.
    """
    _account(db_session, scopes=(DRIVE_SCOPE_READONLY,))
    drive = FakeDrive(root_id=STORAGE_FOLDER)
    gmail = FakeGmail()
    storage = _lazy(db_session, drive=drive, gmail=gmail)

    with pytest.raises(Conflict) as caught:
        storage.put(KEY, PDF, "application/pdf")

    assert caught.value.details["scope"] == DRIVE_SCOPE_FILE
    assert "Impostazioni → Drive" in caught.value.message
    # Neither Drive nor Google's token endpoint was called: the refusal is about the
    # grant, and asking anyway would have spent a round-trip to be told the same thing
    # in worse words.
    assert drive.requests == []
    assert gmail.token_requests == 0


def test_a_read_refused_over_the_missing_scope_is_not_worded_as_a_write(
    db_session: Session,
) -> None:
    """The same gate, met by `get` and by `signed_url`, must not name writing.

    `drive.file` is the scope this backend needs for every operation -- it is what
    scopes the credential to the files the CRM itself created, so without it a download
    is refused for the same reason an upload is. But the refusal is read by a person on
    the settings page, and "la scrittura dei documenti su Drive non è disponibile" in
    answer to *opening* a document names an operation nobody asked for: it invites the
    reader to conclude that reading is fine and something else is broken, which is the
    opposite of true.

    So the sentence names the archive rather than the direction of travel, and the
    guidance -- reconnect from Impostazioni → Drive -- is unchanged, because the fix is.
    `put` and `delete` keep the write wording: those really are writes, and naming them
    is more precise than naming the archive.
    """
    _account(db_session, scopes=(DRIVE_SCOPE_READONLY,))
    storage = _lazy(db_session, drive=FakeDrive(root_id=STORAGE_FOLDER), gmail=FakeGmail())

    for operation in (
        lambda: storage.get(KEY),
        lambda: storage.signed_url(KEY, TTL),
    ):
        with pytest.raises(Conflict) as caught:
            operation()
        assert caught.value.details["scope"] == DRIVE_SCOPE_FILE
        assert "Impostazioni → Drive" in caught.value.message
        assert "scrittura" not in caught.value.message, caught.value.message
        assert "l'archivio documenti su Drive" in caught.value.message

    with pytest.raises(Conflict) as writing:
        storage.delete(KEY)
    assert "la scrittura dei documenti su Drive" in writing.value.message


def test_the_write_scope_is_re_read_when_the_row_changes(db_session: Session) -> None:
    """The scope lives on the row the resolution already re-reads, so a
    re-authorisation that finally grants `drive.file` takes effect at the next
    operation -- no restart, exactly as a changed folder does."""
    account = _account(db_session, scopes=(DRIVE_SCOPE_READONLY,))
    storage = _lazy(db_session, drive=FakeDrive(root_id=STORAGE_FOLDER), gmail=FakeGmail())
    with pytest.raises(Conflict):
        storage.put(KEY, PDF, "application/pdf")

    account.scopes_granted = [DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FILE]
    account.updated_at = datetime.now(UTC)
    db_session.flush()

    storage.put(KEY, PDF, "application/pdf")  # no refusal: the grant is complete now
