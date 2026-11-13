"""REB-800's MCP tools: an agent reads the duplicate pairs without an address and merges one
card into another, and a refusal is the sentence, not a stack trace."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from fakes_crm_door import FakeCrmDoor
from fakes_pigro import PIGRO, TOKEN
from mcp import Client
from mcp_tool_support import IVAN, PDF, _payload
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.config import Settings
from rebase_core.duplicates import UNIQUE_INDEXES
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.models import Freelancer, User
from rebase_mcp.server import build_server

CRM = FakeCrmDoor()
CRM_SETTINGS = Settings(
    _env_file=None,  # type: ignore[call-arg]
    pigro_api_url=PIGRO,
    pigro_engagements_token=TOKEN,
)

TABLES = (
    "admin_actions",
    "notes",
    "contract_documents",
    "freelancer_cards",
    "freelancers",
    "users",
    "signups",
)
ADA = "https://www.linkedin.com/in/ada-lovelace"


def _wipe(factory: sessionmaker[Session]) -> None:
    session = factory()
    for table in TABLES:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()


def _seed(factory: sessionmaker[Session], *emails: str) -> list[UUID]:
    session = factory()
    try:
        session.add(
            User(
                id=IVAN.id, email=IVAN.email, first_name=IVAN.first_name, last_name="", role="admin"
            )
        )
        session.commit()
        ids = []
        for email in emails:
            read, _ = FreelancerService(session).apply(
                FreelancerCreate(
                    first_name="Ada",
                    last_name="Lovelace",
                    email=email,
                    daily_rate=Decimal("450"),
                    job_title="Backend developer",
                    remote="remote",
                ),
                PDF,
                "cv.pdf",
                "application/pdf",
            )
            ids.append(read.id)
        return ids
    finally:
        session.close()


async def test_merge_freelancers_moves_the_card_and_answers_counts_and_the_action(
    factory: sessionmaker[Session],
) -> None:
    keep, other = _seed(factory, "uno@studio.it", "due@studio.it")
    async with Client(
        build_server(factory, lambda: IVAN, settings=CRM_SETTINGS, http=CRM)
    ) as client:
        done = _payload(
            await client.call_tool(
                "merge_freelancers", {"freelancer_id": str(keep), "da_id": str(other)}
            )
        )
    assert done["survivor_freelancer_id"] == str(keep)
    assert done["absorbed_freelancer_id"] == str(other)
    assert done["cv"] in ("kept", "absorbed") and "action_id" in done
    session = factory()
    absorbed = session.scalar(select(Freelancer).where(Freelancer.id == other))
    assert absorbed is not None and absorbed.deleted_at is not None
    session.close()
    _wipe(factory)


async def test_a_refused_merge_says_why(factory: sessionmaker[Session]) -> None:
    keep, other = _seed(factory, "uno@studio.it", "due@studio.it")
    conflict = {"framework_regimes": ["ordinary"], "shared_clients": 0, "tax_code_conflict": False}
    crm = FakeCrmDoor(merge={(keep, other): conflict, (other, keep): conflict})
    async with Client(
        build_server(factory, lambda: IVAN, settings=CRM_SETTINGS, http=crm)
    ) as client:
        refused = await client.call_tool(
            "merge_freelancers", {"freelancer_id": str(keep), "da_id": str(other)}
        )
    assert refused.is_error and "contratto quadro in corso o firmato" in refused.content[0].text
    _wipe(factory)


async def test_a_merge_with_no_crm_to_ask_is_refused(factory: sessionmaker[Session]) -> None:
    keep, other = _seed(factory, "uno@studio.it", "due@studio.it")
    async with Client(build_server(factory, lambda: IVAN)) as client:
        refused = await client.call_tool(
            "merge_freelancers", {"freelancer_id": str(keep), "da_id": str(other)}
        )
    assert refused.is_error and "senza il suo controllo non unisco" in refused.content[0].text
    _wipe(factory)


async def test_list_duplicates_answers_the_pairs_without_an_address(
    factory: sessionmaker[Session],
) -> None:
    older, newer = _seed(factory, "vecchia@studio.it", "nuova@studio.it")
    session = factory()
    for name, _ in UNIQUE_INDEXES:
        session.execute(text(f"DROP INDEX IF EXISTS {name}"))
    session.execute(text("UPDATE users SET linkedin_url = :u WHERE role = 'member'"), {"u": ADA})
    session.commit()
    try:
        async with Client(
            build_server(factory, lambda: IVAN, settings=CRM_SETTINGS, http=CRM)
        ) as client:
            listed: dict[str, Any] = _payload(await client.call_tool("list_duplicates", {}))
        assert len(listed["mergeable"]) == 1 and listed["to_review"] == []
        pair = listed["mergeable"][0]
        assert {pair["survivor"]["freelancer_id"], pair["absorbed"]["freelancer_id"]} == {
            str(older),
            str(newer),
        }
        assert "email" not in pair["survivor"] and "user_id" not in pair["absorbed"]
        assert "studio.it" not in str(listed)
    finally:
        session.close()
        _wipe(factory)
        from rebase_core.duplicates import ensure_unique_indexes

        restore = factory()
        ensure_unique_indexes(restore)
        restore.close()
