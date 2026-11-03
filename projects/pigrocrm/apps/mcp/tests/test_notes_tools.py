"""REB-879: the notes thread of a deal, a customer and a person over MCP.

A note is signed and dated by the CRM from the token and the clock, so no tool takes an
author or a date; an agent edits and deletes only what its token owner may (their own notes,
or any for an admin, and never one the system or rebase wrote); `get_deal`, `get_customer`
and `get_person` carry the latest 50, newest first; the `deal://`, `customer://` and
`person://` resources render the whole thread; a `create_*` with `note` writes the record's
first note; an `update_*` no longer takes one; the timeline names the member.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.notes.models import Note
from pigrocrm.core.notes.schemas import NoteCreate
from pigrocrm.core.notes.service import NoteService
from pigrocrm.core.people.schemas import PersonCreate
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.provenance import PROVENANCE_TEXT_FREE
from pigrocrm_mcp.server import build_server
from pigrocrm_mcp.tools.notes import THREAD_LIMIT

SYSTEM = Actor.system()
ADD_KEYS = {"deal": "deal_id", "customer": "customer_id", "person": "person_id"}
GET_TOOLS = {
    "deal": ("get_deal", "deal_id"),
    "customer": ("get_customer", "customer_id"),
    "person": ("get_person", "person_id"),
}


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _member(session: Session, name: str, key: str, role: str) -> User:
    user = User(email=f"{key}@example.test", password_hash="x", name=name, role=role)
    session.add(user)
    session.flush()
    return user


def _server_of(session: Session, user: User, tmp_path: Path) -> Any:
    """The server as `PatService.resolve` hands it its actor: `mcp`, the owner's id and role."""
    agent = Actor(id=user.id, type="mcp", role=user.role)  # type: ignore[arg-type]
    return build_server(
        lambda: session,
        lambda: agent,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def marco(mcp_session: Session) -> User:
    return _member(mcp_session, "Marco Verdi", "marco", "collaborator")


@pytest.fixture
def giulia(mcp_session: Session) -> User:
    return _member(mcp_session, "Giulia Bianchi", "giulia", "collaborator")


@pytest.fixture
def admin(mcp_session: Session) -> User:
    return _member(mcp_session, "Anna Neri", "anna", "admin")


@pytest.fixture
def marco_server(mcp_session: Session, marco: User, tmp_path: Path) -> Any:
    return _server_of(mcp_session, marco, tmp_path)


@pytest.fixture
def giulia_server(mcp_session: Session, giulia: User, tmp_path: Path) -> Any:
    return _server_of(mcp_session, giulia, tmp_path)


@pytest.fixture
def admin_server(mcp_session: Session, admin: User, tmp_path: Path) -> Any:
    return _server_of(mcp_session, admin, tmp_path)


@pytest.fixture
def records(mcp_session: Session, seeded_customer_id: str) -> dict[str, UUID]:
    """One deal, one customer and one person, written by the system so no member owns them."""
    PipelineService(mcp_session).seed_defaults(SYSTEM)
    customer = UUID(seeded_customer_id)
    deal = DealService(mcp_session).create(
        DealCreate(name="Progetto", customer_id=customer), SYSTEM
    )
    person = PersonService(mcp_session).create(PersonCreate(first_name="Anna"), SYSTEM)
    return {"deal": deal.id, "customer": customer, "person": person.id}


async def _note(client: Client, entity_type: str, entity_id: UUID, text: str) -> dict[str, Any]:
    result = await client.call_tool(
        "add_note", {ADD_KEYS[entity_type]: str(entity_id), "text": text}
    )
    assert not result.is_error, result.content[0].text
    return _payload(result)


async def _thread(client: Client, entity_type: str, entity_id: UUID) -> dict[str, Any]:
    tool, key = GET_TOOLS[entity_type]
    result = await client.call_tool(tool, {key: str(entity_id)})
    assert not result.is_error, result.content[0].text
    return _payload(result)


# --- the three add tools ------------------------------------------------------------


@pytest.mark.parametrize("entity_type", ["deal", "customer", "person"])
async def test_a_note_is_signed_with_the_token_owner_and_the_mcp_channel(
    entity_type: str, marco_server: Any, marco: User, records: dict[str, UUID]
) -> None:
    async with Client(marco_server) as client:
        added = await _note(client, entity_type, records[entity_type], "Richiamare a giugno")
        thread = await _thread(client, entity_type, records[entity_type])

    assert added["text"] == "Richiamare a giugno"
    assert added["author"] == "Marco Verdi"
    assert added["author_id"] == str(marco.id)
    assert added["source"] == "mcp"
    assert added["entity_type"] == entity_type
    assert added["created_at"]
    assert added["edited_at"] is None
    assert added["editable"] is True
    assert "provenance" in added
    assert thread["note"] == [{k: v for k, v in added.items() if k != "provenance"}]
    assert thread["note_total"] == 1


async def test_no_note_tool_takes_an_author_or_a_date(marco_server: Any) -> None:
    async with Client(marco_server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    properties = {
        name: set(tools[name].input_schema["properties"])
        for name in ("add_note", "update_note", "delete_note")
    }
    assert properties == {
        "add_note": {"text", "deal_id", "customer_id", "person_id"},
        "update_note": {"note_id", "text"},
        "delete_note": {"note_id"},
    }
    # The sentence is wrapped in the docstring: compare the words, not the line breaks.
    said = " ".join(tools["add_note"].description.split())
    assert "The CRM signs and dates it itself" in said
    assert "must not start with a date or say who is writing" in said
    assert "No date or author in the text" in tools["update_note"].description


async def test_an_author_or_a_date_in_the_call_is_refused(
    marco_server: Any, records: dict[str, UUID]
) -> None:
    async with Client(marco_server) as client:
        for extra in ({"author": "Giulia"}, {"created_at": "2020-01-01"}):
            result = await client.call_tool(
                "add_note", {"deal_id": str(records["deal"]), "text": "x", **extra}
            )
            assert result.is_error
        assert (await _thread(client, "deal", records["deal"]))["note"] == []


async def test_a_blank_note_and_an_unknown_record_are_refused(
    marco_server: Any, records: dict[str, UUID]
) -> None:
    async with Client(marco_server) as client:
        blank = await client.call_tool("add_note", {"deal_id": str(records["deal"]), "text": "   "})
        missing = await client.call_tool(
            "add_note",
            {"customer_id": "00000000-0000-4000-8000-000000000000", "text": "x"},
        )

    assert blank.is_error
    assert missing.is_error


async def test_add_note_takes_exactly_one_record(
    marco_server: Any, records: dict[str, UUID]
) -> None:
    async with Client(marco_server) as client:
        none = await client.call_tool("add_note", {"text": "x"})
        two = await client.call_tool(
            "add_note",
            {"text": "x", "deal_id": str(records["deal"]), "person_id": str(records["person"])},
        )
        assert none.is_error and two.is_error
        assert "exactly one of" in none.content[0].text
        assert "alternatives" in two.content[0].text
        for entity_type in ("deal", "person"):
            assert (await _thread(client, entity_type, records[entity_type]))["note"] == []


# --- who may edit and delete --------------------------------------------------------


async def test_the_author_edits_their_own_note_and_keeps_the_signature(
    marco_server: Any, marco: User, records: dict[str, UUID]
) -> None:
    async with Client(marco_server) as client:
        added = await _note(client, "deal", records["deal"], "Prima stesura")
        result = await client.call_tool(
            "update_note", {"note_id": added["id"], "text": "Seconda stesura"}
        )
        assert not result.is_error, result.content[0].text
        edited = _payload(result)

    assert edited["text"] == "Seconda stesura"
    assert edited["author"] == "Marco Verdi"
    assert edited["created_at"] == added["created_at"]
    assert edited["edited_at"] is not None
    assert edited["edited_by"] == "Marco Verdi"


async def test_a_member_cannot_edit_or_delete_a_note_of_another_member(
    marco_server: Any,
    giulia_server: Any,
    mcp_session: Session,
    records: dict[str, UUID],
) -> None:
    async with Client(marco_server) as client:
        added = await _note(client, "customer", records["customer"], "Di Marco")

    async with Client(giulia_server) as client:
        seen = (await _thread(client, "customer", records["customer"]))["note"]
        edit = await client.call_tool("update_note", {"note_id": added["id"], "text": "Di Giulia"})
        delete = await client.call_tool("delete_note", {"note_id": added["id"]})

    assert seen[0]["editable"] is False
    for refused in (edit, delete):
        assert refused.is_error
        assert "chi l'ha scritta o un amministratore" in refused.content[0].text
    note = mcp_session.get(Note, UUID(added["id"]))
    assert note is not None
    assert (note.text, note.deleted_at) == ("Di Marco", None)


async def test_an_admin_edits_and_deletes_a_note_of_somebody_else(
    marco_server: Any, admin_server: Any, admin: User, records: dict[str, UUID]
) -> None:
    async with Client(marco_server) as client:
        first = await _note(client, "person", records["person"], "Di Marco")
        second = await _note(client, "person", records["person"], "Da togliere")

    async with Client(admin_server) as client:
        edited = _payload(
            await client.call_tool("update_note", {"note_id": first["id"], "text": "Corretta"})
        )
        deleted = _payload(await client.call_tool("delete_note", {"note_id": second["id"]}))
        thread = await _thread(client, "person", records["person"])

    assert edited["author"] == "Marco Verdi"
    assert edited["edited_by"] == "Anna Neri"
    assert deleted == {"status": "eliminata", "note_id": second["id"]}
    assert [note["text"] for note in thread["note"]] == ["Corretta"]
    assert thread["note_total"] == 1


async def test_a_note_the_system_or_rebase_wrote_is_refused_even_to_an_admin(
    admin_server: Any, mcp_session: Session, records: dict[str, UUID]
) -> None:
    notes = NoteService(mcp_session)
    by_system = notes.add("deal", records["deal"], NoteCreate(text="Dal sistema"), SYSTEM)
    by_rebase = notes.add("deal", records["deal"], NoteCreate(text="Da rebase"), Actor.rebase())
    assert (by_system.source, by_rebase.source) == ("system", "rebase")

    async with Client(admin_server) as client:
        results = []
        for note in (by_system, by_rebase):
            results.append(
                await client.call_tool(
                    "update_note", {"note_id": str(note.id), "text": "Riscritta"}
                )
            )
            results.append(await client.call_tool("delete_note", {"note_id": str(note.id)}))
        thread = (await _thread(client, "deal", records["deal"]))["note"]

    for result in results:
        assert result.is_error
        assert "il sistema o rebase" in result.content[0].text
    assert sorted(note["text"] for note in thread) == ["Da rebase", "Dal sistema"]
    assert {note["editable"] for note in thread} == {False}


async def test_a_note_that_is_not_there_is_refused(marco_server: Any) -> None:
    async with Client(marco_server) as client:
        edit = await client.call_tool(
            "update_note", {"note_id": "00000000-0000-4000-8000-000000000000", "text": "x"}
        )
        delete = await client.call_tool(
            "delete_note", {"note_id": "00000000-0000-4000-8000-000000000000"}
        )

    assert edit.is_error and delete.is_error


# --- the thread in get_* and in the resources ---------------------------------------


def _write_thread(session: Session, entity_type: str, entity_id: UUID, count: int) -> None:
    """`count` notes one minute apart, the first the oldest, with the dates stated: every
    row written inside one test transaction would otherwise share `now()`."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(count):
        session.add(
            Note(
                entity_type=entity_type,
                entity_id=entity_id,
                text=f"nota {index:03d}",
                source="web",
                created_at=start + timedelta(minutes=index),
            )
        )
    session.flush()


@pytest.mark.parametrize("entity_type", ["deal", "customer", "person"])
async def test_get_carries_the_latest_notes_newest_first_and_says_how_many_there_are(
    entity_type: str, marco_server: Any, mcp_session: Session, records: dict[str, UUID]
) -> None:
    _write_thread(mcp_session, entity_type, records[entity_type], THREAD_LIMIT + 5)

    async with Client(marco_server) as client:
        read = await _thread(client, entity_type, records[entity_type])
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    texts = [note["text"] for note in read["note"]]
    assert THREAD_LIMIT == 50
    assert len(texts) == 50
    assert texts[0] == "nota 054"
    assert texts[-1] == "nota 005"
    assert read["note_total"] == 55
    assert read["provenance"] == PROVENANCE_TEXT_FREE
    description = tools[GET_TOOLS[entity_type][0]].description
    assert "last 50 notes" in description or "latest 50 notes" in description
    assert "note_total" in description


@pytest.mark.parametrize("entity_type", ["deal", "customer", "person"])
async def test_the_resource_renders_the_whole_thread_newest_first_with_its_provenance(
    entity_type: str,
    marco_server: Any,
    giulia_server: Any,
    mcp_session: Session,
    records: dict[str, UUID],
) -> None:
    _write_thread(mcp_session, entity_type, records[entity_type], THREAD_LIMIT + 5)
    async with Client(marco_server) as client:
        mine = await _note(client, entity_type, records[entity_type], "La più recente")
        await client.call_tool("update_note", {"note_id": mine["id"], "text": "La più recente."})
    async with Client(giulia_server) as client:
        text = (
            (await client.read_resource(f"{entity_type}://{records[entity_type]}")).contents[0].text
        )

    section = text[text.index("## Note") :]
    assert PROVENANCE_TEXT_FREE in text.splitlines()[0]
    assert section.count("\n**") == THREAD_LIMIT + 6
    assert section.index("La più recente.") < section.index("nota 054") < section.index("nota 000")
    assert "Marco Verdi" in section
    assert "source: mcp" in section
    assert "edited on" in section
    assert "by Marco Verdi" in section


async def test_a_note_from_before_the_thread_is_shown_as_such(
    marco_server: Any, mcp_session: Session, records: dict[str, UUID]
) -> None:
    mcp_session.add(
        Note(entity_type="customer", entity_id=records["customer"], text="Vecchia", source=None)
    )
    mcp_session.flush()

    async with Client(marco_server) as client:
        text = (await client.read_resource(f"customer://{records['customer']}")).contents[0].text
        thread = (await _thread(client, "customer", records["customer"]))["note"]

    assert "Earlier note" in text
    assert (thread[0]["source"], thread[0]["author"]) == (None, None)


async def test_a_record_without_notes_has_no_note_section_and_is_still_marked(
    marco_server: Any, records: dict[str, UUID]
) -> None:
    async with Client(marco_server) as client:
        text = (await client.read_resource(f"deal://{records['deal']}")).contents[0].text
        read = await _thread(client, "deal", records["deal"])

    assert "## Note" not in text
    assert read["note"] == []
    # The name is free text too, so the answer is marked with or without notes.
    assert read["provenance"] == PROVENANCE_TEXT_FREE


# --- create_* and update_* ----------------------------------------------------------


async def test_create_with_a_note_makes_it_the_first_note_signed_by_the_token_owner(
    marco_server: Any, marco: User, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SYSTEM)
    async with Client(marco_server) as client:
        customer = _payload(
            await client.call_tool(
                "create_customer", {"legal_name": "ACME", "note": "Cliente storico"}
            )
        )
        deal = _payload(
            await client.call_tool(
                "create_deal",
                {"name": "Progetto", "customer_id": customer["id"], "note": "In trattativa"},
            )
        )
        person = _payload(
            await client.call_tool(
                "create_person", {"first_name": "Anna", "note": "Referente tecnico"}
            )
        )
        threads = {
            "customer": await _thread(client, "customer", UUID(customer["id"])),
            "deal": await _thread(client, "deal", UUID(deal["id"])),
            "person": await _thread(client, "person", UUID(person["id"])),
        }

    assert "note" not in customer and "note" not in deal and "note" not in person
    expected = {
        "customer": "Cliente storico",
        "deal": "In trattativa",
        "person": "Referente tecnico",
    }
    for entity_type, text in expected.items():
        [note] = threads[entity_type]["note"]
        assert (note["text"], note["author"], note["source"]) == (text, "Marco Verdi", "mcp")
        assert note["author_id"] == str(marco.id)


async def test_create_without_a_note_writes_none(marco_server: Any) -> None:
    async with Client(marco_server) as client:
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        thread = await _thread(client, "customer", UUID(customer["id"]))

    assert thread["note"] == [] and thread["note_total"] == 0


@pytest.mark.parametrize("entity_type", ["deal", "customer", "person"])
async def test_update_no_longer_takes_a_note_and_says_where_it_goes(
    entity_type: str, marco_server: Any, records: dict[str, UUID]
) -> None:
    tool = f"update_{entity_type}"
    async with Client(marco_server) as client:
        result = await client.call_tool(
            tool, {f"{entity_type}_id": str(records[entity_type]), "changes": {"note": "x"}}
        )
        schema = next(t for t in (await client.list_tools()).tools if t.name == tool)
        thread = await _thread(client, entity_type, records[entity_type])

    assert result.is_error
    assert "`add_note`" in result.content[0].text
    assert "note" not in json.dumps(schema.input_schema["properties"]["changes"])
    assert thread["note"] == []


async def test_the_creating_tools_still_advertise_a_note(marco_server: Any) -> None:
    async with Client(marco_server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    for name in ("create_deal", "create_customer", "create_person"):
        assert "note" in tools[name].input_schema["properties"], name
        assert "first note" in tools[name].description, name


@pytest.mark.parametrize("entity_type", ["deal", "customer", "person"])
async def test_describe_schema_no_longer_lists_note_as_a_native_field(
    entity_type: str, marco_server: Any
) -> None:
    async with Client(marco_server) as client:
        body = _payload(await client.call_tool("describe_schema", {"entity_type": entity_type}))

    assert "note" not in body["native_fields"]


# --- the timeline -------------------------------------------------------------------


async def test_the_timeline_names_the_member_for_an_added_edited_and_deleted_note(
    marco_server: Any, admin_server: Any, records: dict[str, UUID]
) -> None:
    async with Client(marco_server) as client:
        added = await _note(client, "deal", records["deal"], "Prima stesura")
        await client.call_tool("update_note", {"note_id": added["id"], "text": "Seconda"})
    async with Client(admin_server) as client:
        await client.call_tool("delete_note", {"note_id": added["id"]})
        entries = _payload(
            await client.call_tool(
                "get_timeline", {"entity_type": "deal", "entity_id": str(records["deal"])}
            )
        )["entries"]

    by_kind = {entry["kind"]: entry for entry in entries if entry["kind"].startswith("note_")}
    assert set(by_kind) == {"note_added", "note_edited", "note_deleted"}
    for kind, name in (
        ("note_added", "Marco Verdi"),
        ("note_edited", "Marco Verdi"),
        ("note_deleted", "Anna Neri"),
    ):
        assert (by_kind[kind]["actor_name"], by_kind[kind]["actor_type"]) == (name, "mcp"), kind
    assert "before" not in by_kind["note_edited"]["payload"]
