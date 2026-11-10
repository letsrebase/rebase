"""REB-971: the inbox over MCP is reads only and carries no bytes. A file is uploaded over the
web or REST and never through a tool; a tool lists and reads the items (metadata, never the
file, never the storage key), and the access rule of the inbox holds for a token like it does
for a session."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.ai.gateway import ModelGateway
from pigrocrm.core.ai.transport import ModelAnswer, ModelRequest
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.inbox.classifier import InboxClassifier
from pigrocrm.core.inbox.uploads import InboxUploads
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.provenance import PROVENANCE_TEXT_FREE
from pigrocrm_mcp.server import build_server

INVOICE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "tests"
    / "fixtures"
    / "fatturapa"
    / "fpr-12-march-consulting.xml"
).read_bytes()
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _member(session: Session, key: str, role: str) -> User:
    user = User(email=f"{key}@example.test", password_hash="x", name=key.title(), role=role)
    session.add(user)
    session.flush()
    return user


def _server_of(session: Session, user: User, tmp_path: Path) -> Any:
    agent = Actor(id=user.id, type="mcp", role=user.role)  # type: ignore[arg-type]
    return build_server(
        lambda: session,
        lambda: agent,
        LocalFileStorage(tmp_path / "agent"),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def anna(mcp_session: Session) -> User:
    return _member(mcp_session, "anna", "admin")


@pytest.fixture
def marco(mcp_session: Session) -> User:
    return _member(mcp_session, "marco", "collaborator")


@pytest.fixture
def giulia(mcp_session: Session) -> User:
    return _member(mcp_session, "giulia", "collaborator")


@pytest.fixture
def uploaded(mcp_session: Session, marco: User, tmp_path: Path) -> list[dict[str, Any]]:
    """What Marco uploaded over REST: an invoice, its duplicate and a PDF."""
    inbox = InboxUploads(mcp_session, LocalFileStorage(tmp_path / "store"))
    items = inbox.upload(
        [("fattura.xml", INVOICE), ("copia.xml", INVOICE), ("estratto.pdf", PDF)],
        Actor(id=marco.id, type="user", role="collaborator"),
    ).items
    return [item.model_dump(mode="json") for item in items]


async def test_the_inbox_has_two_read_only_tools_and_no_upload(server: Any) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert {"list_inbox_items", "get_inbox_item"} <= set(tools)
    for name in ("list_inbox_items", "get_inbox_item"):
        assert tools[name].annotations is not None
        assert tools[name].annotations.read_only_hint is True
    assert not [name for name in tools if "upload" in name]
    # No tool of the server takes the bytes of a file: the inbox's reads have none to take.
    for name in ("list_inbox_items", "get_inbox_item"):
        properties = tools[name].input_schema.get("properties", {})
        assert not {"content", "data", "file", "bytes", "base64"} & set(properties)


async def test_the_list_answers_metadata_newest_first_and_never_the_file(
    mcp_session: Session, anna: User, uploaded: list[dict[str, Any]], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        result = await client.call_tool("list_inbox_items", {})
    assert not result.is_error, result.content[0].text
    payload = _payload(result)
    assert [i["original_name"] for i in payload["items"]] == [
        "estratto.pdf",
        "copia.xml",
        "fattura.xml",
    ]
    assert payload["next_cursor"] is None
    # A file name is written by whoever sent the file: the answer says so.
    assert payload["provenance"] == PROVENANCE_TEXT_FREE
    for item in payload["items"]:
        assert "storage_key" not in item
        assert not {"content", "data", "bytes"} & set(item)
    duplicate = payload["items"][1]
    assert (duplicate["state"], duplicate["duplicate_of_id"]) == ("duplicate", uploaded[0]["id"])


async def test_the_list_filters_and_pages(
    mcp_session: Session, anna: User, uploaded: list[dict[str, Any]], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        duplicates = _payload(await client.call_tool("list_inbox_items", {"state": "duplicate"}))
        first = _payload(await client.call_tool("list_inbox_items", {"limit": 2}))
        second = _payload(
            await client.call_tool("list_inbox_items", {"limit": 2, "cursor": first["next_cursor"]})
        )
    assert [i["original_name"] for i in duplicates["items"]] == ["copia.xml"]
    assert len(first["items"]) == 2 and first["next_cursor"] is not None
    assert [i["original_name"] for i in second["items"]] == ["fattura.xml"]


async def test_a_state_that_does_not_exist_is_refused_in_words(
    mcp_session: Session, anna: User, tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        result = await client.call_tool("list_inbox_items", {"state": "nonsense"})
    assert result.is_error
    assert "new" in result.content[0].text and "duplicate" in result.content[0].text


async def test_one_item_is_read_with_its_hash_and_where_it_came_from(
    mcp_session: Session, anna: User, uploaded: list[dict[str, Any]], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        result = await client.call_tool("get_inbox_item", {"item_id": uploaded[1]["id"]})
    assert not result.is_error, result.content[0].text
    item = _payload(result)
    assert item["sha256"] == uploaded[1]["sha256"]
    assert item["duplicate_of_id"] == uploaded[0]["id"]
    assert item["has_file"] is False
    assert "storage_key" not in item


async def test_an_item_that_does_not_exist_is_not_found(
    mcp_session: Session, anna: User, tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        result = await client.call_tool("get_inbox_item", {"item_id": str(uuid4())})
    assert result.is_error
    assert "non trovato" in result.content[0].text


async def test_a_token_sees_the_unclassified_items_of_its_owner_and_an_admins_see_all(
    mcp_session: Session,
    anna: User,
    marco: User,
    giulia: User,
    uploaded: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    async with Client(_server_of(mcp_session, marco, tmp_path)) as client:
        mine = _payload(await client.call_tool("list_inbox_items", {}))
    async with Client(_server_of(mcp_session, giulia, tmp_path)) as client:
        theirs = _payload(await client.call_tool("list_inbox_items", {}))
        refused = await client.call_tool("get_inbox_item", {"item_id": uploaded[0]["id"]})
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        everything = _payload(await client.call_tool("list_inbox_items", {}))
    assert len(mine["items"]) == 3
    assert theirs["items"] == []
    assert refused.is_error and "non trovato" in refused.content[0].text
    assert len(everything["items"]) == 3


def _pdf(line: str) -> bytes:
    """A one-page PDF whose text layer says `line`, with a real cross-reference table."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


RECEIPT_PDF = _pdf("Scontrino 42 totale 48,00")


def _no_model(request: ModelRequest) -> ModelAnswer:
    raise AssertionError("the model is off: nothing may be sent to it")


@pytest.fixture
def unread(mcp_session: Session, marco: User, tmp_path: Path) -> dict[str, Any]:
    """A PDF Marco uploaded while PigroCRM's own model is off: read, not classified."""
    storage = LocalFileStorage(tmp_path / "store")
    actor = Actor(id=marco.id, type="user", role="collaborator")
    uploaded = InboxUploads(mcp_session, storage).upload([("scontrino.pdf", RECEIPT_PDF)], actor)
    classifier = InboxClassifier(
        mcp_session,
        storage,
        ModelGateway(mcp_session, Settings(_env_file=None), _no_model),  # type: ignore[call-arg]
    )
    (item,) = classifier.classify_uploaded(uploaded, actor, model_budget=5).items
    return item.model_dump(mode="json")


async def test_the_agent_finds_what_nobody_has_read_reads_its_text_and_proposes(
    mcp_session: Session, marco: User, unread: dict[str, Any], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, marco, tmp_path)) as client:
        waiting = _payload(await client.call_tool("list_inbox_items", {"classified": False}))
        read = await client.call_tool("get_inbox_item", {"item_id": unread["id"]})
        proposed = await client.call_tool(
            "propose_inbox_item",
            {
                "item_id": unread["id"],
                "kind": "receipt",
                "excerpt": "Scontrino 42 totale 48,00",
                "confidence": 0.8,
                "fields": {"issue_date": "2026-11-03", "total": 48},
            },
        )
        after = _payload(await client.call_tool("get_inbox_item", {"item_id": unread["id"]}))
        nothing_left = _payload(await client.call_tool("list_inbox_items", {"classified": False}))
    assert [i["id"] for i in waiting["items"]] == [unread["id"]]
    item = _payload(read)
    assert "Scontrino 42" in item["extracted_text"]
    assert PROVENANCE_TEXT_FREE in json.dumps(read.structured_content or read.content[0].text)
    assert not proposed.is_error, proposed.content[0].text
    proposal = _payload(proposed)
    assert (proposal["status"], proposal["target_type"]) == ("pending", "receipt")
    assert proposal["inbox_item_id"] == unread["id"] and proposal["document_id"] is None
    assert (after["state"], after["kind"], after["proposal_id"]) == (
        "proposed",
        "receipt",
        proposal["id"],
    )
    assert nothing_left["items"] == []


async def test_a_proposal_from_the_inbox_is_refused_in_words_when_it_does_not_hold(
    mcp_session: Session,
    marco: User,
    giulia: User,
    unread: dict[str, Any],
    tmp_path: Path,
) -> None:
    fields = {"issue_date": "2026-11-03", "total": 48}
    base = {"item_id": unread["id"], "excerpt": "x", "confidence": 0.5, "fields": fields}
    async with Client(_server_of(mcp_session, marco, tmp_path)) as client:
        wrong_kind = await client.call_tool("propose_inbox_item", {**base, "kind": "contract"})
        no_total = await client.call_tool(
            "propose_inbox_item",
            {**base, "kind": "receipt", "fields": {"issue_date": "2026-11-03"}},
        )
    async with Client(_server_of(mcp_session, giulia, tmp_path)) as client:
        not_hers = await client.call_tool("propose_inbox_item", {**base, "kind": "receipt"})
    assert wrong_kind.is_error and "supplier_invoice" in wrong_kind.content[0].text
    assert no_total.is_error and "total" in no_total.content[0].text
    assert not_hers.is_error and "non trovato" in not_hers.content[0].text


async def test_the_three_tools_that_say_something_about_an_item_are_annotated_and_take_no_bytes(
    server: Any,
) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert tools["set_inbox_item_kind"].annotations.destructive_hint is False  # type: ignore[union-attr]
    for name in ("discard_inbox_item", "mark_inbox_item_duplicate"):
        assert tools[name].annotations.destructive_hint is True  # type: ignore[union-attr]
    for name in ("set_inbox_item_kind", "discard_inbox_item", "mark_inbox_item_duplicate"):
        assert not {"content", "data", "file", "bytes", "base64"} & set(
            tools[name].input_schema.get("properties", {})
        )


async def test_an_agent_says_what_an_item_is_discards_one_and_marks_a_copy(
    mcp_session: Session, marco: User, uploaded: list[dict[str, Any]], tmp_path: Path
) -> None:
    xml, copy, pdf = uploaded
    async with Client(_server_of(mcp_session, marco, tmp_path)) as client:
        kind = await client.call_tool(
            "set_inbox_item_kind", {"item_id": pdf["id"], "kind": "bank_statement"}
        )
        discarded = await client.call_tool(
            "discard_inbox_item", {"item_id": pdf["id"], "reason": "Estratto di un altro conto"}
        )
        again = await client.call_tool(
            "discard_inbox_item", {"item_id": pdf["id"], "reason": "Estratto di un altro conto"}
        )
        no_reason = await client.call_tool(
            "discard_inbox_item", {"item_id": xml["id"], "reason": "  "}
        )
        wrong_kind = await client.call_tool(
            "set_inbox_item_kind", {"item_id": xml["id"], "kind": "archive"}
        )
        already_copy = await client.call_tool(
            "mark_inbox_item_duplicate", {"item_id": copy["id"], "original_id": xml["id"]}
        )
        other = await client.call_tool(
            "mark_inbox_item_duplicate", {"item_id": copy["id"], "original_id": pdf["id"]}
        )
    assert _payload(kind)["kind"] == "bank_statement"
    assert _payload(kind)["reason"] == "Tipo indicato dal tuo agente"
    assert (_payload(discarded)["state"], _payload(discarded)["reason"]) == (
        "discarded",
        "Estratto di un altro conto",
    )
    assert _payload(again)["state"] == "discarded"
    assert no_reason.is_error
    assert wrong_kind.is_error and "supplier_invoice" in wrong_kind.content[0].text
    assert not already_copy.is_error  # idempotent for the same original
    assert other.is_error  # already the copy of another file


async def test_an_agent_leaves_an_item_with_a_proposal_to_a_person(
    mcp_session: Session, marco: User, unread: dict[str, Any], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, marco, tmp_path)) as client:
        proposed = await client.call_tool(
            "propose_inbox_item",
            {
                "item_id": unread["id"],
                "kind": "receipt",
                "excerpt": "Scontrino 42 totale 48,00",
                "confidence": 0.8,
                "fields": {"issue_date": "2026-11-03", "total": 48},
            },
        )
        assert not proposed.is_error, proposed.content[0].text
        refused = [
            await client.call_tool("discard_inbox_item", {"item_id": unread["id"], "reason": "No"}),
            await client.call_tool(
                "set_inbox_item_kind", {"item_id": unread["id"], "kind": "supplier_invoice"}
            ),
        ]
        after = _payload(await client.call_tool("get_inbox_item", {"item_id": unread["id"]}))
    for result in refused:
        assert result.is_error and "proposta in attesa" in result.content[0].text
    assert (after["state"], after["kind"]) == ("proposed", "receipt")


async def test_another_member_cannot_say_anything_about_an_item_they_do_not_see(
    mcp_session: Session, giulia: User, uploaded: list[dict[str, Any]], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, giulia, tmp_path)) as client:
        results = [
            await client.call_tool(
                "discard_inbox_item", {"item_id": uploaded[2]["id"], "reason": "x"}
            ),
            await client.call_tool(
                "set_inbox_item_kind", {"item_id": uploaded[2]["id"], "kind": "receipt"}
            ),
            await client.call_tool(
                "mark_inbox_item_duplicate",
                {"item_id": uploaded[2]["id"], "original_id": uploaded[0]["id"]},
            ),
        ]
    for result in results:
        assert result.is_error and "non trovato" in result.content[0].text
