import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

# The in-memory Drive and the in-memory Google token endpoint live with the core tests,
# which are a separate pytest root with no package of their own -- reached by path
# exactly as `apps/mcp/tests/test_drive_privileged_tools.py` reaches them, rather than
# duplicated into a second pair of fakes that would be a second place for the two to
# disagree about what Google does.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.fake_drive import FOLDER_MIME, FakeDrive  # noqa: E402
from fakes.fake_gmail import FakeGmail  # noqa: E402
from fakes.gmail_fixtures import TOKEN_KEY, gmail_settings  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

import pigrocrm_api.deps as deps  # noqa: E402
from pigrocrm.core.auth.models import User  # noqa: E402
from pigrocrm.core.config import Settings, get_settings  # noqa: E402
from pigrocrm.core.drive.models import GoogleDriveAccount  # noqa: E402
from pigrocrm.core.drive.schemas import DRIVE_SCOPE_FILE, DRIVE_SCOPE_READONLY  # noqa: E402
from pigrocrm.core.gmail.crypto import seal  # noqa: E402
from pigrocrm.core.gmail.tokens import GoogleTokenClient  # noqa: E402
from pigrocrm.core.gmail.transport import GmailTransport  # noqa: E402
from pigrocrm.core.storage import lazy_drive  # noqa: E402
from pigrocrm.core.storage.lazy_drive import LazyUserDriveStorage  # noqa: E402
from pigrocrm.core.tenants import SpaceRegistry  # noqa: E402
from pigrocrm_api.deps import get_storage  # noqa: E402

PDF = b"%PDF-1.7\nfinto\n"
# A Drive file id of the shape the titolare types into Impostazioni → Drive.
STORAGE_FOLDER = "1CartellaScritturaAPI"
REFRESH_TOKEN = "1//0gApiDriveStorageRefresh"
DRIVE_MAILBOX = "titolare@example.it"


def _customer(client: TestClient) -> str:
    response = client.post("/api/customers", json={"ragione_sociale": "ACME S.r.l."})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_create_a_document_on_a_customer(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    response = logged_in.post(
        "/api/documents",
        json={"customer_id": customer_id, "tipo": "offerta", "titolo": "Offerta 1"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["stato"] == "bozza"


def test_a_document_with_neither_owner_is_a_422_problem_document(logged_in: TestClient) -> None:
    response = logged_in.post("/api/documents", json={"tipo": "documento", "titolo": "X"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "validation_failed"


def test_an_unknown_customer_is_a_404_problem_document(logged_in: TestClient) -> None:
    response = logged_in.post(
        "/api/documents",
        json={"customer_id": str(uuid4()), "tipo": "documento", "titolo": "X"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_list_filters_by_customer(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "tipo": "documento", "titolo": "A"}
    )
    response = logged_in.get("/api/documents", params={"customer_id": customer_id})
    assert response.status_code == 200
    assert [d["titolo"] for d in response.json()["items"]] == ["A"]


def test_a_limit_over_the_ceiling_is_refused(logged_in: TestClient) -> None:
    assert logged_in.get("/api/documents", params={"limit": 1000}).status_code == 422


def test_upload_a_version_and_download_it_back(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    document_id = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "tipo": "documento", "titolo": "Doc"}
    ).json()["id"]

    upload = logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("scansione.pdf", b"%PDF-1.7\nfinto\n", "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["numero"] == 1

    download = logged_in.get(f"/api/documents/{document_id}/download")
    assert download.status_code == 200
    assert download.content == b"%PDF-1.7\nfinto\n"
    assert download.headers["content-type"] == "application/pdf"
    assert "attachment" in download.headers["content-disposition"]


def test_an_unallowed_upload_type_is_refused(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    document_id = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "tipo": "documento", "titolo": "Doc"}
    ).json()["id"]
    response = logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("x.html", b"<script>alert(1)</script>", "text/html")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


def test_the_download_filename_cannot_inject_a_header(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    document_id = logged_in.post(
        "/api/documents",
        json={"customer_id": customer_id, "tipo": "documento", "titolo": 'a"\r\nX-Evil: 1'},
    ).json()["id"]
    logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("a.pdf", b"%PDF-1.7\n", "application/pdf")},
    )
    response = logged_in.get(f"/api/documents/{document_id}/download")
    assert response.status_code == 200
    assert "X-Evil" not in response.headers


def test_set_offer_state_and_refuse_an_undeclared_transition(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    document_id = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "tipo": "offerta", "titolo": "O"}
    ).json()["id"]
    assert (
        logged_in.post(f"/api/documents/{document_id}/status", json={"stato": "inviata"}).json()[
            "stato"
        ]
        == "inviata"
    )
    conflict = logged_in.post(f"/api/documents/{document_id}/status", json={"stato": "bozza"})
    assert conflict.status_code == 200  # inviata -> bozza is allowed
    # Now back in "bozza", whose only legal exit is "inviata": "accettata" is refused.
    forbidden = logged_in.post(f"/api/documents/{document_id}/status", json={"stato": "accettata"})
    assert forbidden.status_code == 409
    assert forbidden.json()["code"] == "conflict"


def test_a_readonly_actor_cannot_create_a_document(readonly_client: TestClient) -> None:
    response = readonly_client.post(
        "/api/documents", json={"customer_id": str(uuid4()), "tipo": "documento", "titolo": "X"}
    )
    assert response.status_code == 403


def test_templates_crud_and_describe(logged_in: TestClient) -> None:
    created = logged_in.post(
        "/api/templates",
        json={
            "nome": "Consulenza CTO",
            "tipo": "offerta",
            "corpo_markdown": "Spett.le {{cliente.ragione_sociale}} — {{oggetto}}",
            "variabili_dichiarate": [
                {"nome": "oggetto", "etichetta": "Oggetto", "tipo": "text", "obbligatoria": True}
            ],
        },
    )
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]

    described = logged_in.get(f"/api/templates/{template_id}/describe")
    assert described.status_code == 200
    assert [v["nome"] for v in described.json()["variabili"]] == ["oggetto"]

    preview = logged_in.post(
        f"/api/templates/{template_id}/preview",
        json={"variabili": {"cliente": {"ragione_sociale": "ACME"}, "oggetto": "Advisory"}},
    )
    assert preview.status_code == 200
    assert preview.json()["markdown"] == "Spett.le ACME — Advisory"


def test_a_template_body_that_does_not_parse_is_a_422(logged_in: TestClient) -> None:
    response = logged_in.post(
        "/api/templates", json={"nome": "Rotto", "tipo": "offerta", "corpo_markdown": "{{#if x}}"}
    )
    assert response.status_code == 422
    assert "riga 1" in response.json()["reason"]


def test_a_template_can_be_deactivated_and_reactivated(logged_in: TestClient) -> None:
    created = logged_in.post(
        "/api/templates",
        json={"nome": "Da disattivare", "tipo": "offerta", "corpo_markdown": "Ciao"},
    )
    template_id = created.json()["id"]
    deactivated = logged_in.delete(f"/api/templates/{template_id}")
    assert deactivated.status_code == 200
    assert deactivated.json()["attivo"] is False
    reactivated = logged_in.post(f"/api/templates/{template_id}/activate")
    assert reactivated.status_code == 200
    assert reactivated.json()["attivo"] is True


def test_template_list_hides_inactive_ones_by_default(logged_in: TestClient) -> None:
    created = logged_in.post(
        "/api/templates",
        json={"nome": "Nascosto", "tipo": "offerta", "corpo_markdown": "Ciao"},
    )
    template_id = created.json()["id"]
    logged_in.delete(f"/api/templates/{template_id}")
    hidden = logged_in.get("/api/templates")
    assert template_id not in [t["id"] for t in hidden.json()["items"]]
    shown = logged_in.get("/api/templates", params={"include_inactive": True})
    assert template_id in [t["id"] for t in shown.json()["items"]]


def test_emitter_profile_is_404_before_it_is_saved_then_readable(logged_in: TestClient) -> None:
    assert logged_in.get("/api/emitter").status_code == 404
    saved = logged_in.put(
        "/api/emitter",
        json={"ragione_sociale": "Studio Rossi", "partita_iva": "01234567890"},
    )
    assert saved.status_code == 200, saved.text
    assert logged_in.get("/api/emitter").json()["partita_iva"] == "01234567890"


def test_a_non_admin_cannot_write_the_emitter_profile(collaborator_client: TestClient) -> None:
    response = collaborator_client.put("/api/emitter", json={"ragione_sociale": "X"})
    assert response.status_code == 403


def test_read_the_text_of_an_uploaded_file(logged_in: TestClient) -> None:
    """The counterpart of the download: same file, same authorisation, but the answer
    is what the file *says* rather than the file itself -- for a reader (an agent, a
    preview panel) that cannot open a PDF."""
    customer_id = _customer(logged_in)
    document_id = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "tipo": "documento", "titolo": "Doc"}
    ).json()["id"]
    logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("note.md", b"# Codice destinatario\n\nABCDEFG\n", "text/markdown")},
    )

    response = logged_in.get(f"/api/documents/{document_id}/text")

    assert response.status_code == 200, response.text
    body = response.json()
    assert "ABCDEFG" in body["testo"]
    assert body["numero"] == 1
    assert body["troncato"] is False
    # The sentence that says whose words those are travels in the body, always.
    assert "istruzione" in body["provenienza"]


def test_reading_the_text_of_a_version_nobody_uploaded_is_a_404(logged_in: TestClient) -> None:
    customer_id = _customer(logged_in)
    document_id = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "tipo": "documento", "titolo": "Doc"}
    ).json()["id"]

    assert logged_in.get(f"/api/documents/{document_id}/text").status_code == 404


def test_the_openapi_document_declares_the_new_routes(logged_in: TestClient) -> None:
    paths = logged_in.get("/openapi.json").json()["paths"]
    for path in (
        "/api/documents",
        "/api/documents/from-template",
        "/api/documents/{document_id}/download",
        "/api/documents/{document_id}/text",
        "/api/templates/{template_id}/describe",
        "/api/emitter",
    ):
        assert path in paths, path


def test_an_upload_before_drive_is_connected_is_a_409_that_says_what_to_do(
    logged_in: TestClient, api_session: Session
) -> None:
    """`PIGROCRM_STORAGE_BACKEND=gdrive` with no service account means the documents go
    to the titolare's own Drive, and until they have connected it and chosen a folder
    there is nowhere to put them. The API still starts -- that is the whole point of the
    storage resolving late -- so the refusal happens here, at the upload, and it has to
    arrive as a problem document naming the screen that fixes it rather than as a 500.

    `get_storage` is overridden rather than `PIGROCRM_STORAGE_BACKEND` set, because the
    factory's own choice is core's test (`test_storage_factory.py`); what this asserts
    is the *rendering* of what that choice produced.
    """
    bind = api_session.get_bind()
    logged_in.app.dependency_overrides[get_storage] = lambda: LazyUserDriveStorage(
        lambda: Session(bind=bind, join_transaction_mode="create_savepoint"),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )
    customer_id = _customer(logged_in)
    document_id = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "tipo": "documento", "titolo": "Doc"}
    ).json()["id"]

    response = logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("scansione.pdf", b"%PDF-1.7\nfinto\n", "application/pdf")},
    )

    assert response.status_code == 409, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "conflict"
    assert "Impostazioni → Drive" in body["detail"]


def _connected_drive(session: Session) -> GoogleDriveAccount:
    """The titolare's Drive, connected, with a write folder chosen.

    The refresh token is really sealed with the key `gmail_settings()` publishes, so the
    unsealing the storage does at resolution runs for real -- the same seeding
    `packages/core/tests/test_storage_factory.py` does, kept local for the reason every
    other suite in this repository keeps its own: `conftest` is an ambiguous module name
    across three test roots, and a fixture module for one row would be a third place to
    look.
    """
    user = User(
        email=f"titolare-{uuid4().hex[:8]}@example.it",
        nome="Titolare",
        password_hash="x",
        ruolo="admin",
        attivo=True,
    )
    session.add(user)
    session.flush()
    ciphertext, nonce = seal(REFRESH_TOKEN, TOKEN_KEY)
    account = GoogleDriveAccount(
        user_id=user.id,
        google_sub=f"sub-{user.id}",
        email_address=DRIVE_MAILBOX,
        refresh_token_ciphertext=ciphertext,
        refresh_token_nonce=nonce,
        scopes_granted=[DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FILE],
        status="active",
        root_folder_ids=[],
        storage_folder_id=STORAGE_FOLDER,
    )
    session.add(account)
    session.flush()
    return account


@pytest.fixture
def fresh_storage_cache() -> Iterator[None]:
    """`get_storage` caches one backend for the whole process, so a test that changes
    which backend the settings name has to clear it -- before, so it does not inherit
    the one an earlier test built, and after, so it does not leave a Drive-backed
    storage behind for the next one.

    Through `deps.reset_storage_cache()` rather than by assigning `deps._storage`: the
    cache is that module's business, and a test that reaches into its private state is
    one rename away from silently resetting nothing.
    """
    deps.reset_storage_cache()
    yield
    deps.reset_storage_cache()


def _drive_installation(
    session: Session, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive, gmail: FakeGmail
) -> Settings:
    """An installation whose documents go to the titolare's own Drive, wired to fakes.

    Three things, and each of them replaces exactly one piece of the outside world:

    * `PIGROCRM_STORAGE_BACKEND=gdrive` with no service account -- the settings the
      dependency reads to choose the second Drive route;
    * `deps._registry`'s root sessionmaker -- the API's own, pointed at this test's
      connection, because `get_storage` hands the storage a factory that opens *fresh*
      sessions and the real one would build an engine against a database that is not
      the testcontainer. `join_transaction_mode="create_savepoint"` is what lets those
      sessions see this test's uncommitted rows and nest their own work inside the
      transaction the `api_session` fixture rolls back. `SpaceRegistry.session_factory`
      returns whatever is already in `_root` without opening anything of its own, so a
      registry built for this purpose and never asked for a space's engine is exactly
      the API's own registry with one field pinned;
    * `lazy_drive.user_transport_for` -- the network. The real helper still runs (it is
      what unseals the refresh token), only with the Drive HTTP call and Google's token
      endpoint pointed at the two in-memory fakes. One `GoogleTokenClient`, built here
      and reused by every resolution, because a client per call would cache nothing and
      the token count below is the whole point.

    `jwt_secret` is the one the `logged_in` cookie was signed with (`conftest.py`
    overrides `get_settings` with a bare `Settings`), so replacing the settings mid-test
    changes the storage backend and nothing else about who the caller is.
    """
    settings = gmail_settings(
        storage_backend="gdrive",
        jwt_secret=Settings(_env_file=None).jwt_secret,  # type: ignore[call-arg]
    )
    registry = SpaceRegistry(settings)
    registry._root = sessionmaker(
        bind=session.get_bind(),
        expire_on_commit=False,
        future=True,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(deps, "_registry", registry)
    tokens = GoogleTokenClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        transport=GmailTransport(http=gmail, sleep=lambda _: None),
    )
    real_user_transport_for = lazy_drive.user_transport_for
    monkeypatch.setattr(
        lazy_drive,
        "user_transport_for",
        lambda account, account_settings, **_: real_user_transport_for(
            account, account_settings, http=drive, tokens=tokens
        ),
    )
    return settings


def test_an_upload_goes_to_the_titolares_own_drive_and_downloads_back_identical(
    logged_in: TestClient,
    api_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fresh_storage_cache: None,
) -> None:
    """The slice, through the API: `PIGROCRM_STORAGE_BACKEND=gdrive` with no service
    account, a Drive the titolare has connected and chosen a folder in, and the bytes of
    an upload land in *their* Drive under *that* folder -- then come back byte-identical
    from the download route, which is the only way documents are ever read (spec 5).

    `get_storage` is the real dependency here, not an override: what this test exists to
    prove is that the dependency itself passes a session factory to
    `storage_from_settings`, which is the one thing standing between this configuration
    and a 500 at every upload.

    `token_requests == 1` is the assertion about *scope*, and it is why this test makes
    two requests instead of one. A storage built per request would build a second
    transport with a second `GoogleTokenClient` for the download, and pay a second OAuth
    round-trip to read back what it had just written. One exchange for both requests is
    what "one storage per process" looks like from outside.
    """
    drive = FakeDrive(root_id=STORAGE_FOLDER)
    gmail = FakeGmail()
    settings = _drive_installation(api_session, monkeypatch, drive, gmail)
    _connected_drive(api_session)
    logged_in.app.dependency_overrides[get_settings] = lambda: settings
    del logged_in.app.dependency_overrides[get_storage]

    customer_id = _customer(logged_in)
    document_id = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "tipo": "documento", "titolo": "Doc"}
    ).json()["id"]

    uploaded = logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("scansione.pdf", PDF, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text

    downloaded = logged_in.get(f"/api/documents/{document_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == PDF

    # The bytes really are on the titolare's Drive, beneath the folder they picked --
    # one folder per customer and one per document, exactly the arrangement the service
    # account produces (spec 5.4).
    written = [f for f in drive.files.values() if f.mime != FOLDER_MIME]
    assert [f.data for f in written] == [PDF]
    folders = {f.id: f for f in drive.files.values() if f.mime == FOLDER_MIME}
    document_folder = folders[written[0].parent]
    assert folders[document_folder.parent].parent == STORAGE_FOLDER
    assert gmail.token_requests == 1


def test_the_api_builds_one_storage_for_the_whole_process_and_opens_nothing_to_do_it(
    monkeypatch: pytest.MonkeyPatch, fresh_storage_cache: None
) -> None:
    """Two properties of the dependency itself, neither visible over HTTP.

    *One instance.* Every request that touches a document asks for `get_storage`, and
    each one that built its own `LazyUserDriveStorage` would throw away the access token
    the previous request obtained -- so the instance is cached in the module, behind a
    lock, the way the registry already is (`_storage_lock`; `test_deps.py` drives the
    registry's equivalent cold start with eight threads).

    *Nothing is opened to build it.* The session factory is passed as a callable, not
    called, so a process configured for Drive can construct its storage without an
    engine, without a connection and without the `google_drive_accounts` row that may
    not exist yet -- which is the entire reason this storage resolves late. The registry
    staying unbuilt is that, asserted: `_fresh_session` would have to run to reach
    `_get_session_factory`, and nothing here ever calls it.

    The storage cache is cleared through the module's own `reset_storage_cache`
    (`fresh_storage_cache`, above); `_registry` is still monkeypatched, which `pytest`
    undoes at the end of the test.
    """
    monkeypatch.setattr(deps, "_registry", None)
    settings = gmail_settings(storage_backend="gdrive")
    # A root request: a space would get its own on-disk storage instead (deps.get_storage).
    request = Request({"type": "http", "path": "/api/documents", "headers": [], "state": {}})

    first = deps.get_storage(request, settings)
    second = deps.get_storage(request, settings)

    assert isinstance(first, LazyUserDriveStorage)
    assert first is second
    assert deps._registry is None
