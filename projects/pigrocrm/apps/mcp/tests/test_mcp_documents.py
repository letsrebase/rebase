"""Task 13's own brief sample called `server.call_tool_sync(...)`/
`server.list_tools_sync(...)` and depended on a `mcp_server` fixture -- neither
exists against the installed SDK (`mcp==2.0.0`; `MCPServer` has no `*_sync` methods
at all) or in this repo's `conftest.py` (the existing fixture is `server`, an
`MCPServer`, driven through `mcp.Client` exactly as every other MCP test in this
project already does -- see `test_mcp_tools.py`). Rewritten against that real
pattern rather than transcribed.
"""

import json
import shutil
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

# The in-memory Drive and the in-memory Google token endpoint live with the core tests,
# which are a separate pytest root with no package of their own -- reached by path
# exactly as `test_drive_privileged_tools.py` reaches them, rather than duplicated into
# a second pair of fakes that would be a second place for the two to disagree about
# what Google does.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.fake_drive import FOLDER_MIME, FakeDrive  # noqa: E402
from fakes.fake_gmail import FakeGmail  # noqa: E402
from fakes.gmail_fixtures import TOKEN_KEY, gmail_settings  # noqa: E402
from mcp import Client  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from test_text import minimal_pdf  # noqa: E402

from pigrocrm.core.actor import Actor  # noqa: E402
from pigrocrm.core.analytics import service as analytics_service  # noqa: E402
from pigrocrm.core.auth.models import User  # noqa: E402
from pigrocrm.core.drive.models import GoogleDriveAccount  # noqa: E402
from pigrocrm.core.drive.schemas import DRIVE_SCOPE_FILE, DRIVE_SCOPE_READONLY  # noqa: E402
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert  # noqa: E402
from pigrocrm.core.fiscal.service import FiscalProfileService  # noqa: E402
from pigrocrm.core.gmail.crypto import seal  # noqa: E402
from pigrocrm.core.gmail.tokens import GoogleTokenClient  # noqa: E402
from pigrocrm.core.gmail.transport import GmailTransport  # noqa: E402
from pigrocrm.core.storage import lazy_drive  # noqa: E402
from pigrocrm.core.timetracking.schemas import TimeEntryCreate  # noqa: E402
from pigrocrm.core.timetracking.service import TimeEntryService  # noqa: E402
from pigrocrm_mcp.server import build_server  # noqa: E402

# A Drive file id of the shape the titolare types into Impostazioni → Drive.
STORAGE_FOLDER = "1CartellaScritturaMCP"
REFRESH_TOKEN = "1//0gMcpDriveStorageRefresh"
DRIVE_MAILBOX = "titolare@example.it"

needs_binaries = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("typst") is None,
    reason="pandoc e typst vivono nell'immagine dell'API",
)


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_seven_spec_tools_are_registered(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {
        "list_documents",
        "get_document",
        "create_document_from_template",
        "list_templates",
        "describe_template",
        "set_offer_state",
        "get_document_versions",
    } <= names


async def test_no_tool_returns_document_bytes(server) -> None:
    # "Il download dei byte non passa da MCP: un tool che restituisce un PDF in
    # base64 dentro un contesto e' uno spreco e un rischio" (spec 7).
    async with Client(server) as client:
        tools = (await client.list_tools()).tools

    for tool in tools:
        assert "download" not in tool.name
        assert "base64" not in (tool.description or "").lower()


async def test_describe_template_reports_the_variables_before_anyone_is_asked(
    server, seeded_template_id: str
) -> None:
    async with Client(server) as client:
        result = await client.call_tool("describe_template", {"template_id": seeded_template_id})

    described = _payload(result)
    assert [v["nome"] for v in described["variabili"]] == ["oggetto"]
    assert described["nome"] == "Consulenza CTO"


@needs_binaries
async def test_create_document_from_template_returns_an_identifier_not_bytes(
    server, seeded_template_id: str, seeded_customer_id: str
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "create_document_from_template",
            {
                "template_id": seeded_template_id,
                "customer_id": seeded_customer_id,
                "titolo": "Offerta 2026-01",
                "variabili": {"oggetto": "Advisory"},
            },
        )

    assert not result.is_error, result.content[0].text
    created = _payload(result)
    assert "id" in created
    assert "pdf" not in json.dumps(created).lower()


async def test_list_documents_filters_by_customer(server, seeded_customer_id: str) -> None:
    async with Client(server) as client:
        result = await client.call_tool("list_documents", {"customer_id": seeded_customer_id})

    listed = _payload(result)
    assert "items" in listed and isinstance(listed["items"], list)


async def test_a_wrong_typed_limit_produces_guidance_not_a_pydantic_dump(
    server, seeded_customer_id: str
) -> None:
    # R2: the SDK validates some arguments before the guard runs. `BoundedLimit`'s
    # `int | str` runtime type is what keeps this inside the guard.
    async with Client(server) as client:
        result = await client.call_tool(
            "list_documents", {"customer_id": seeded_customer_id, "limit": "molti"}
        )

    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_set_offer_state_refuses_an_undeclared_transition_with_guidance(
    server, seeded_offer_id: str
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "set_offer_state", {"document_id": seeded_offer_id, "stato": "accettata"}
        )

    assert result.is_error
    message = result.content[0].text
    assert "bozza" in message
    assert "errors.pydantic.dev" not in message


async def test_get_document_versions_lists_them_newest_first(server, seeded_offer_id: str) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_document_versions", {"document_id": seeded_offer_id})

    assert "versions" in _payload(result)


async def test_the_four_document_tools_the_audit_added_are_registered(server) -> None:
    """Each was reachable over REST and excluded from MCP with a reason that only said
    nobody had written the tool yet -- `TemplateService.preview` "non ha ancora
    un'audience agentica", `DocumentService.regenerate` "e' una decisione della persona
    che l'ha vista", `soft_delete`/`restore` "l'MCP non archivia documenti". None of the
    four decides anything a person has not already decided, and the last two are the
    archive/restore pair this surface already exposes for customers, deals, people,
    costs and time entries."""
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {
        "preview_template",
        "regenerate_document_version",
        "archive_document",
        "restore_document",
    } <= names


async def test_preview_template_renders_without_creating_a_document(
    server, seeded_template_id: str, seeded_customer_id: str
) -> None:
    """`describe_template` says what a template wants; this says what the answers would
    produce. Nothing is written -- which is the whole reason it is safe to expose, and
    the reason it is useful: the agent checks the text before
    `create_document_from_template` freezes a version and a PDF."""
    async with Client(server) as client:
        result = await client.call_tool(
            "preview_template",
            {
                "template_id": seeded_template_id,
                # `cliente` is supplied by the caller here, unlike
                # `create_document_from_template`, which reads it off the record: a
                # preview is not filed under anything, so there is no record to read.
                "variabili": {"oggetto": "Advisory", "cliente": {"ragione_sociale": "ACME"}},
            },
        )
        assert not result.is_error, result.content[0].text
        assert _payload(result)["markdown"] == "Spett.le ACME — Advisory"

        listed = _payload(
            await client.call_tool("list_documents", {"customer_id": seeded_customer_id})
        )
    assert listed["items"] == []


async def test_preview_template_names_the_variable_it_is_missing(
    server, seeded_template_id: str
) -> None:
    """The failure mode that makes a preview worth having: it says which variable is
    missing, before the same omission costs a document and a rendered PDF."""
    async with Client(server) as client:
        result = await client.call_tool(
            "preview_template",
            {
                "template_id": seeded_template_id,
                "variabili": {"cliente": {"ragione_sociale": "ACME"}},
            },
        )

    assert result.is_error
    assert "oggetto" in result.content[0].text
    assert "errors.pydantic.dev" not in result.content[0].text


@needs_binaries
async def test_regenerate_reproduces_a_version_and_leaves_the_original_in_place(
    server, seeded_template_id: str, seeded_customer_id: str
) -> None:
    """Slice 2's reproducibility promise, exercised from the surface that now offers it:
    the new version is rendered from the old one's own frozen `template_id` and
    `variabili`, so its bytes hash the same, and the version it came from is still
    there. It adds; it does not overwrite."""
    async with Client(server) as client:
        created = _payload(
            await client.call_tool(
                "create_document_from_template",
                {
                    "template_id": seeded_template_id,
                    "customer_id": seeded_customer_id,
                    "titolo": "Offerta 2026-02",
                    "variabili": {"oggetto": "Advisory"},
                },
            )
        )
        regenerated = await client.call_tool(
            "regenerate_document_version", {"document_id": created["id"], "numero": 1}
        )
        assert not regenerated.is_error, regenerated.content[0].text
        assert _payload(regenerated)["numero"] == 2

        versions = _payload(
            await client.call_tool("get_document_versions", {"document_id": created["id"]})
        )["versions"]

    assert [v["numero"] for v in versions] == [2, 1]
    assert versions[0]["hash_sha256"] == versions[1]["hash_sha256"]


async def test_regenerate_refuses_a_version_that_was_never_generated_from_a_template(
    server, seeded_offer_id: str
) -> None:
    """The guard that makes this tool a reproduction rather than a creation: with no
    template and no variables stored on the source version there is nothing to
    reproduce, and the refusal says so instead of inventing a document."""
    async with Client(server) as client:
        result = await client.call_tool(
            "regenerate_document_version", {"document_id": seeded_offer_id, "numero": 1}
        )

    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_regenerate_rejects_a_wrong_typed_version_number_with_guidance(
    server, seeded_offer_id: str
) -> None:
    """`VersionNumber` is `int | str` at runtime for the reason every other scalar
    parameter here is: a bare `int` lets the SDK reject the argument before `_guard`
    runs, and the agent gets a raw English pydantic dump. `_NUMERO` validates it inside
    the guarded call instead."""
    async with Client(server) as client:
        result = await client.call_tool(
            "regenerate_document_version", {"document_id": seeded_offer_id, "numero": "prima"}
        )

    assert result.is_error
    message = result.content[0].text
    assert "numero intero" in message
    assert "errors.pydantic.dev" not in message


async def test_a_document_archived_by_an_agent_can_be_restored_by_one(
    server, seeded_offer_id: str
) -> None:
    """Both, or neither. An agent that could archive a document and not bring it back
    would leave its own mistake correctable only by a person with a browser -- the exact
    asymmetry the audit found on customers, deals and people. The stored bytes are never
    touched by either half, so the restored document still has its PDF."""
    async with Client(server) as client:
        archived = await client.call_tool("archive_document", {"document_id": seeded_offer_id})
        assert not archived.is_error, archived.content[0].text
        assert (await client.call_tool("get_document", {"document_id": seeded_offer_id})).is_error

        restored = _payload(
            await client.call_tool("restore_document", {"document_id": seeded_offer_id})
        )
        assert restored["id"] == seeded_offer_id
        assert not (
            await client.call_tool("get_document", {"document_id": seeded_offer_id})
        ).is_error


async def test_describe_azienda_reads_the_issuer_every_header_prints(
    server, seeded_template_id: str
) -> None:
    """`seeded_template_id` is the fixture that seeds the emitter profile, because
    rendering a document needs one -- which is the same reason
    `LegalEntityService.get` is un-role-gated at the service layer: the PDF header
    needs it for every role, so there is no role for which this read is privileged. The
    write on that row is `update_azienda`, admin-only (ORB-188)."""
    async with Client(server) as client:
        profile = _payload(await client.call_tool("describe_azienda", {}))

    assert profile["ragione_sociale"] == "Studio Rossi"
    assert profile["partita_iva"] == "01234567890"
    # Whether a logo is set, never its storage key (REB-627): the keys are the server's,
    # and an agent that reads this hands it back to `update_azienda` unchanged.
    assert profile["ha_logo"] is False
    assert "logo_key" not in profile


class _Provider:
    """The `server` fixture's provider, plus the one thing a Drive-backed storage needs.

    `__call__` answers the suite's own transactional session, so a tool call reads and
    writes inside the transaction `mcp_session` rolls back -- exactly what the shared
    `server` fixture does, and the reason it passes a bare `lambda: session`. What a
    bare lambda cannot offer is `new_session`: the lazily-resolved Drive storage opens a
    session per operation and closes it, and closing this one would end the transaction
    the tool call is still running in. So `new_session` binds a fresh `Session` to the
    same `Connection` with `create_savepoint` -- independent lifecycle, same uncommitted
    rows, and its own commits nested inside the fixture's transaction rather than
    escaping it (`packages/core/tests/test_storage_factory.py::_sessions` explains the
    same choice at length).

    Deliberately no `scope`: `build_server` reads that attribute to decide whether to
    open a session per logical call, and a provider without one keeps the single shared
    session this suite is built on.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._bind = session.get_bind()

    def __call__(self) -> Session:
        return self._session

    def new_session(self) -> Session:
        return Session(bind=self._bind, join_transaction_mode="create_savepoint")


def _connected_drive(session: Session) -> GoogleDriveAccount:
    """The titolare's Drive, connected, with a write folder chosen.

    Seeded here rather than shared from a fixture module, the way every other suite in
    this repository that needs this row seeds its own (`test_drive_tools.py`,
    `test_drive_privileged_tools.py`, `apps/api/tests/test_documents_api.py`). The
    refresh token is really sealed with the key `gmail_settings()` publishes, so the
    unsealing the storage does at resolution runs for real.
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


@needs_binaries
async def test_a_document_an_agent_generates_lands_in_the_titolares_own_drive(
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    seeded_template_id: str,
    seeded_customer_id: str,
) -> None:
    """`build_server(..., storage=None)` on an installation whose documents go to the
    titolare's own Drive: the tool renders a PDF and the bytes land in *their* Drive,
    under *the folder they chose*, carrying *their* access token.

    No `storage` is passed, on purpose -- that is the whole test. Every other MCP test
    hands `build_server` a `LocalFileStorage` under `tmp_path`, so nothing until now
    exercised the branch production actually uses, where the adapter has to give
    `storage_from_settings` a way to reach the database or the configuration is refused
    at every single tool call.

    The network is the only thing replaced: `user_transport_for` still unseals the row's
    refresh token for real, with Google's token endpoint and Drive itself answered by
    the two in-memory fakes. One token exchange for the whole tool call, because one
    storage per process holds one token client.
    """
    settings = gmail_settings(storage_backend="gdrive")
    drive = FakeDrive(root_id=STORAGE_FOLDER)
    gmail = FakeGmail()
    _connected_drive(mcp_session)

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

    server = build_server(
        _Provider(mcp_session),
        lambda: Actor(id=None, type="mcp", role="admin"),
        None,
        settings,
    )
    async with Client(server) as client:
        result = await client.call_tool(
            "create_document_from_template",
            {
                "template_id": seeded_template_id,
                "customer_id": seeded_customer_id,
                "titolo": "Offerta su Drive",
                "variabili": {"oggetto": "Advisory"},
            },
        )

    assert not result.is_error, result.content[0].text
    written = [f for f in drive.files.values() if f.mime != FOLDER_MIME]
    assert len(written) == 1, [f.name for f in written]
    assert written[0].data.startswith(b"%PDF")
    # One folder per customer, one per document, both beneath the folder the titolare
    # picked -- the arrangement the service-account backend produces (spec 5.4).
    folders = {f.id: f for f in drive.files.values() if f.mime == FOLDER_MIME}
    document_folder = folders[written[0].parent]
    assert folders[document_folder.parent].parent == STORAGE_FOLDER
    assert gmail.token_requests == 1


async def test_binding_hours_to_a_draft_writes_through_the_servers_own_storage(
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    seeded_customer_id: str,
    seeded_deal_id: Any,
    seeded_user_id: Any,
) -> None:
    """`bind_time_to_invoice` on the installation of the test above: `gdrive` chosen,
    no service account, documents on the titolare's own Drive.

    The tool reaches `InvoiceService`, whose constructor takes a backend because a
    *later* emission renders artefacts through it -- and the backend it must take is the
    one the adapter already built and holds on `McpContext.storage`. A tool that builds
    `AnalyticsService(context.session)` instead makes that service resolve a *second*
    storage from the process settings alone, with no session factory to reach the row
    the folder lives in -- and `storage_from_settings` then refuses that
    configuration by name. On this installation the refusal is total: every call fails
    with a `ValidationFailed` naming the service-account JSON setting, pointing an
    operator at a service account they deliberately do not have.

    `get_settings` is monkeypatched rather than left to the process, because the
    installation this asserts about is a property of the fixture and not of whoever is
    running pytest. Patched to *return* the settings, not to raise: the point is that a
    correct wiring never consults it, and a broken one meets exactly the refusal
    production meets.

    The setting is described rather than spelled: `test_no_network.py`'s guard against
    self-skipping suites reads every test file as text, and a PIGROCRM-prefixed
    variable name in a file that also carries a `skipif` -- `needs_binaries`, on pandoc
    and typst, which asks about installed binaries and not about a credential -- is
    indistinguishable from the pattern it exists to forbid.
    """
    settings = gmail_settings(storage_backend="gdrive", mcp_full_access=True)
    monkeypatch.setattr(analytics_service, "get_settings", lambda: settings)
    FiscalProfileService(mcp_session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), Actor(id=None, type="system", role="admin")
    )
    entry = TimeEntryService(mcp_session).create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            data=date(2026, 3, 2),
            ore=Decimal("4.00"),
            descrizione="Analisi",
            fatturabile=True,
            tariffa_applicata=Decimal("100.000000"),
        ),
        Actor(id=None, type="user", role="admin"),
    )
    server = build_server(
        _Provider(mcp_session),
        lambda: Actor(id=None, type="mcp", role="admin", full_access=True),
        None,
        settings,
    )

    async with Client(server) as client:
        result = await client.call_tool(
            "bind_time_to_invoice",
            {"deal_id": str(seeded_deal_id), "entry_ids": [str(entry.id)]},
        )

    assert not result.is_error, result.content[0].text
    draft = _payload(result)
    assert draft["stato"] == "bozza"
    assert draft["imponibile"] == "400.00"


# --- read_document_text ----------------------------------------------------------------


@pytest.fixture
def documento_con_pdf(mcp_session: Session, seeded_customer_id: str, tmp_path: Path) -> str:
    """A document holding one real PDF, seeded through the same storage root the
    `server` fixture hands the context -- so the tool reads the very bytes written
    here, rather than a second copy in a second directory."""
    from uuid import UUID

    from pigrocrm.core.documents.schemas import DocumentCreate
    from pigrocrm.core.documents.service import DocumentService
    from pigrocrm.core.storage import LocalFileStorage

    service = DocumentService(mcp_session, LocalFileStorage(tmp_path))
    document = service.create(
        DocumentCreate(
            customer_id=UUID(seeded_customer_id), tipo="documento", titolo="Modulo d'ordine firmato"
        ),
        Actor(id=None, type="system", role="admin"),
    )
    service.add_version(
        document.id,
        minimal_pdf(["Codice destinatario: ABCDEFG", "PEC: beta@pec.it"]),
        "application/pdf",
        Actor(id=None, type="system", role="admin"),
    )
    return str(document.id)


async def test_read_document_text_returns_what_is_written_inside_the_pdf(
    server, documento_con_pdf: str
) -> None:
    """The reason the tool exists: a codice destinatario that lives only on a signed
    order form was, until this, recoverable solely by downloading the file and reading
    it by eye -- which an agent cannot do."""
    async with Client(server) as client:
        result = await client.call_tool("read_document_text", {"document_id": documento_con_pdf})

    assert not result.is_error, result.content[0].text
    letto = _payload(result)
    assert "ABCDEFG" in letto["testo"]
    assert letto["numero"] == 1
    assert letto["troncato"] is False


async def test_read_document_text_carries_its_provenance(server, documento_con_pdf: str) -> None:
    """Text extracted from somebody else's file reaches a model that reads its own
    instructions as text. The warning travels in the payload, not only in the tool
    description the model saw once."""
    from pigrocrm.core.text import PROVENIENZA

    async with Client(server) as client:
        result = await client.call_tool("read_document_text", {"document_id": documento_con_pdf})

    assert _payload(result)["provenienza"] == PROVENIENZA


async def test_read_document_text_returns_no_bytes(server, documento_con_pdf: str) -> None:
    """The rule this tool is an exception to still binds it: text, never the file. No
    base64 payload and no storage key -- the download stays on the REST API."""
    async with Client(server) as client:
        result = await client.call_tool("read_document_text", {"document_id": documento_con_pdf})

    letto = _payload(result)
    assert set(letto) == {
        "document_id",
        "numero",
        "titolo",
        "testo",
        "mime",
        "troncato",
        "provenienza",
    }


async def test_read_document_text_on_an_unknown_document_is_guidance_not_a_dump(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("read_document_text", {"document_id": str(uuid4())})

    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text
