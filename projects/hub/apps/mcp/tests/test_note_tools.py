"""The notes thread over MCP: signed by the token, marked `mcp`, edited and deleted."""

from uuid import UUID

from mcp import Client
from mcp_tool_support import IVAN, _payload, _seed_admin, _seed_company, _seed_freelancer, _wipe
from sqlalchemy.orm import Session, sessionmaker

from rebase_mcp.server import build_server


async def test_a_note_from_the_mcp_is_signed_by_the_token_and_marked_mcp(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        first = _payload(
            await client.call_tool(
                "add_freelancer_note",
                {"freelancer_id": freelancer_id, "text": "Sentito al telefono."},
            )
        )
        assert (first["author"], first["text"]) == ("Ivan Sala", "Sentito al telefono.")
        assert (first["author_id"], first["source"], first["editable"]) == (
            str(IVAN.id),
            "mcp",
            True,
        )
        # The author is the token's: the tool has no argument to name another.
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert "author" not in tools["add_freelancer_note"].input_schema["properties"]
        assert "author" not in tools["add_company_note"].input_schema["properties"]
        second = _payload(
            await client.call_tool(
                "add_freelancer_note",
                {"freelancer_id": freelancer_id, "text": "Portfolio ricevuto."},
            )
        )

        detail = _payload(
            await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id})
        )
        assert [c["id"] for c in detail["note"]] == [second["id"], first["id"]]
        assert "commenti" not in detail
        # The list does not carry the thread.
        listed = _payload(await client.call_tool("list_talents", {}))
        assert "note" not in listed["items"][0]
    _wipe(factory)


async def test_a_note_is_edited_and_deleted_over_mcp_and_the_trail_names_the_door(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        note = _payload(
            await client.call_tool(
                "add_freelancer_note", {"freelancer_id": freelancer_id, "text": "Prima."}
            )
        )
        edited = _payload(
            await client.call_tool("update_note", {"note_id": note["id"], "text": "Dopo."})
        )
        assert (edited["text"], edited["edited_by"]) == ("Dopo.", "Ivan Sala")
        gone = _payload(await client.call_tool("delete_note", {"note_id": note["id"]}))
        assert gone == {"note": note["id"], "deleted": True}
        detail = _payload(
            await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id})
        )
        assert detail["note"] == []
        again = await client.call_tool("delete_note", {"note_id": note["id"]})
        assert again.is_error and "non trovato" in again.content[0].text

        audit = await client.call_tool("get_freelancer_audit", {"freelancer_id": freelancer_id})
        assert audit.structured_content is not None
        entries = {entry["kind"]: entry["payload"] for entry in audit.structured_content["result"]}
        assert set(entries) == {"note_added", "note_edited", "note_deleted"}
        assert all(payload["source"] == "mcp" for payload in entries.values())
        assert entries["note_edited"]["before"] == "Prima."
    _wipe(factory)


async def test_the_persons_events_are_refused_by_update_and_delete(
    factory: sessionmaker[Session],
) -> None:
    from rebase_core.models import Note

    freelancer_id = _seed_freelancer(factory)
    session = factory()
    event = Note(
        entity_type="freelancer",
        entity_id=UUID(freelancer_id),
        text="CV caricato dalla persona",
        author="Ada Lovelace",
        source="person",
    )
    session.add(event)
    session.commit()
    event_id = str(event.id)
    session.close()
    async with Client(build_server(factory, lambda: IVAN)) as client:
        for tool, args in (
            ("update_note", {"note_id": event_id, "text": "Riscritta."}),
            ("delete_note", {"note_id": event_id}),
        ):
            refused = await client.call_tool(tool, args)
            assert refused.is_error and "non si modifica" in refused.content[0].text
        detail = _payload(
            await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id})
        )
        assert [n["text"] for n in detail["note"]] == ["CV caricato dalla persona"]
        assert detail["note"][0]["editable"] is False
    _wipe(factory)


async def test_a_company_note_lands_on_the_company_and_a_bad_one_is_a_sentence(
    factory: sessionmaker[Session],
) -> None:
    company_id = _seed_company(factory)
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        posted = _payload(
            await client.call_tool(
                "add_company_note", {"company_id": company_id, "text": "Budget confermato."}
            )
        )
        assert (posted["author"], posted["source"]) == ("Ivan Sala", "mcp")
        detail = _payload(await client.call_tool("get_company", {"company_id": company_id}))
        assert [c["text"] for c in detail["note"]] == ["Budget confermato."]

        # A freelancer id is not a company: not found, as a sentence.
        wrong = await client.call_tool(
            "add_company_note", {"company_id": freelancer_id, "text": "x"}
        )
        assert wrong.is_error and "non trovato" in wrong.content[0].text
        empty = await client.call_tool(
            "add_freelancer_note", {"freelancer_id": freelancer_id, "text": "   "}
        )
        assert empty.is_error and "text" in empty.content[0].text
        long = await client.call_tool(
            "add_freelancer_note", {"freelancer_id": freelancer_id, "text": "x" * 4001}
        )
        assert long.is_error and "4000" in long.content[0].text
        untouched = _payload(
            await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id})
        )
        assert untouched["note"] == []
    _wipe(factory)
