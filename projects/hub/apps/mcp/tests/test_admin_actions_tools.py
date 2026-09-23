"""REB-347's MCP tools: an agent overrides, clears, deletes and restores a freelancer or
company record, and reads and reverses those actions from the same rows' own trail."""

from decimal import Decimal
from typing import Any

from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from test_tools import IVAN, PDF, _payload

from rebase_core.companies import CompanyService
from rebase_core.freelancers import FreelancerService
from rebase_core.models import User
from rebase_core.schemas import CompanyCreate, FreelancerCreate
from rebase_mcp.server import build_server


def _wipe(factory: sessionmaker[Session]) -> None:
    session = factory()
    for table in ("admin_actions", "comments", "freelancers", "companies", "users"):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()


def _seed_admin(factory: sessionmaker[Session]) -> None:
    """`IVAN` (`test_tools.py`) is a stand-in `AdminRead`, never a `users` row: every
    other tool only reads its `nome`. `record` writes `admin_id` for real, so this
    file alone needs the row the id names, with the same id."""
    session = factory()
    session.add(User(id=IVAN.id, email=IVAN.email, nome=IVAN.nome, cognome="", role="admin"))
    session.commit()
    session.close()


def _seed_freelancer(factory: sessionmaker[Session]) -> str:
    session = factory()
    try:
        row, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome="Ada",
                cognome="Lovelace",
                email="ada@studio.it",
                tariffa_giornaliera=Decimal("450"),
                posizione="Backend developer",
                remoto="remoto",
            ),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
        return str(row.id)
    finally:
        session.close()


def _seed_company(factory: sessionmaker[Session]) -> str:
    from datetime import date

    session = factory()
    try:
        row = CompanyService(session).request(
            CompanyCreate(
                nome_azienda="ACME Srl",
                referente_nome="Wile",
                referente_cognome="E.",
                email="wile@acme.it",
                progetto="Un backend developer per tre mesi.",
                periodo_da=date(2026, 10, 1),
                durata="3 mesi",
                budget_giornaliero=Decimal("500"),
            )
        )
        return str(row.id)
    finally:
        session.close()


async def test_override_freelancer_writes_the_delta_and_leaves_untouched_fields_alone(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        overridden = _payload(
            await client.call_tool(
                "override_freelancer",
                {"freelancer_id": freelancer_id, "posizione": "Tech lead"},
            )
        )
        assert overridden["posizione"] == "Tech lead"
        assert overridden["tariffa_giornaliera"] == "450.00"

        trail = _payload_list(
            await client.call_tool("get_freelancer_audit", {"freelancer_id": freelancer_id})
        )
        assert len(trail) == 1
        assert trail[0]["kind"] == "overridden"
        assert trail[0]["payload"]["before"] == {"posizione": "Backend developer"}
    _wipe(factory)


async def test_an_empty_string_clears_a_nullable_field_and_a_required_one_is_refused(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        cleared = _payload(
            await client.call_tool(
                "override_freelancer", {"freelancer_id": freelancer_id, "posizione": ""}
            )
        )
        assert cleared["posizione"] is None

        refused = await client.call_tool(
            "override_freelancer", {"freelancer_id": freelancer_id, "stato": ""}
        )
        assert refused.is_error
    _wipe(factory)


async def test_clear_freelancer_cv_drops_the_file_and_records_only_its_metadata(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        cleared = _payload(
            await client.call_tool("clear_freelancer_cv", {"freelancer_id": freelancer_id})
        )
        assert cleared["cv_filename"] is None
        missing_cv = await client.call_tool("read_freelancer_cv", {"freelancer_id": freelancer_id})
        assert missing_cv.is_error

        trail = _payload_list(
            await client.call_tool("get_freelancer_audit", {"freelancer_id": freelancer_id})
        )
        assert trail[0]["kind"] == "cleared"
        assert "cv_bytes" not in str(trail[0]["payload"])
    _wipe(factory)


async def test_delete_and_restore_a_freelancer_through_the_mcp(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        deleted = _payload(
            await client.call_tool("delete_freelancer", {"freelancer_id": freelancer_id})
        )
        assert deleted["deleted_at"] is not None
        gone = _payload(await client.call_tool("list_talenti", {}))
        assert gone["totale"] == 0

        restored = _payload(
            await client.call_tool("restore_freelancer", {"freelancer_id": freelancer_id})
        )
        assert restored["deleted_at"] is None
        back = _payload(await client.call_tool("list_talenti", {}))
        assert back["totale"] == 1
    _wipe(factory)


async def test_revert_freelancer_action_reads_the_before_value_off_the_trail(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        await client.call_tool(
            "override_freelancer", {"freelancer_id": freelancer_id, "posizione": "Tech lead"}
        )
        trail = _payload_list(
            await client.call_tool("get_freelancer_audit", {"freelancer_id": freelancer_id})
        )
        action_id = trail[0]["id"]

        reverted = _payload(
            await client.call_tool(
                "revert_freelancer_action",
                {"freelancer_id": freelancer_id, "action_id": action_id},
            )
        )
        assert reverted["posizione"] == "Backend developer"
    _wipe(factory)


async def test_override_company_touches_the_row_and_the_referente_identity(
    factory: sessionmaker[Session],
) -> None:
    company_id = _seed_company(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        overridden = _payload(
            await client.call_tool("override_company", {"company_id": company_id, "nome": "Coyote"})
        )
        assert overridden["referente"].startswith("Coyote")

        trail = _payload_list(
            await client.call_tool("get_company_audit", {"company_id": company_id})
        )
        assert len(trail) == 1 and trail[0]["kind"] == "overridden"
    _wipe(factory)


async def test_delete_and_restore_a_company_through_the_mcp(
    factory: sessionmaker[Session],
) -> None:
    company_id = _seed_company(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        deleted = _payload(await client.call_tool("delete_company", {"company_id": company_id}))
        assert deleted["deleted_at"] is not None
        gone = _payload(await client.call_tool("list_aziende", {}))
        assert gone["totale"] == 0

        restored = _payload(await client.call_tool("restore_company", {"company_id": company_id}))
        assert restored["deleted_at"] is None
    _wipe(factory)


async def test_reverting_a_deleted_or_restored_entry_is_refused(
    factory: sessionmaker[Session],
) -> None:
    company_id = _seed_company(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        await client.call_tool("delete_company", {"company_id": company_id})
        await client.call_tool("restore_company", {"company_id": company_id})
        trail = _payload_list(
            await client.call_tool("get_company_audit", {"company_id": company_id})
        )
        delete_entry = next(e for e in trail if e["kind"] == "deleted")

        refused = await client.call_tool(
            "revert_company_action",
            {"company_id": company_id, "action_id": delete_entry["id"]},
        )
        assert refused.is_error
    _wipe(factory)


def _payload_list(result: Any) -> list[dict[str, Any]]:
    return result.structured_content["result"] if result.structured_content else []
