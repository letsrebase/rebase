"""`list_fee_occurrences` and `bill_contract_fee` over the MCP client (REB-1032): the read is every
token's, the draft is a `full_access` administrator's, and it never issues."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TODAY = today_local()
SETUP = Actor(id=None, type="system", role="admin")


def _server(session: Session, tmp_path: Path, *, full_access: bool = True) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=full_access),  # type: ignore[arg-type]
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _world(session: Session) -> tuple[Contract, RateCard]:
    """The regime and letterhead an invoice needs, a customer complete enough to be invoiced, an
    active contract and a monthly fee card that started a month ago."""
    FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), SETUP)
    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            email="mario@example.com",
        ),
        SETUP,
    )
    customer = Organization(
        legal_name=f"ACME {uuid4()}",
        vat_number=str(uuid4().int)[:11],
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        postal_code="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    session.add(customer)
    session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=TODAY - dt.timedelta(days=60),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
        status="active",
    )
    session.add(contract)
    session.flush()
    card = RateCard(
        contract_id=contract.id,
        valid_from=TODAY - dt.timedelta(days=30),
        type="fixed_recurring",
        amount=Decimal("1000.00"),
        unit="month",
        delivery_period="monthly",
    )
    session.add(card)
    session.flush()
    return contract, card


async def test_the_occurrences_are_every_tokens_and_the_draft_is_a_full_access_tokens(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }

    assert {"list_fee_occurrences", "bill_contract_fee"} <= full
    assert "list_fee_occurrences" in plain
    assert "bill_contract_fee" not in plain


async def test_it_reads_the_occurrences_bills_one_and_answers_the_draft_the_second_time(
    mcp_session: Session, tmp_path: Path
) -> None:
    contract, card = _world(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        listed = await client.call_tool("list_fee_occurrences", {"contract_id": str(contract.id)})
        assert not listed.is_error, listed.content
        rows = _payload(listed)["occurrences"]
        assert rows[0]["period_start"] == card.valid_from.isoformat()
        assert {row["state"] for row in rows} == {"to_bill"}
        arguments = {
            "contract_id": str(contract.id),
            "rate_card_id": str(card.id),
            "period_start": rows[0]["period_start"],
        }

        made = await client.call_tool("bill_contract_fee", arguments)
        assert not made.is_error, made.content
        body = _payload(made)
        assert body["created"] is True
        # A draft only: no number, nothing issued.
        assert (body["invoice"]["status"], body["invoice"]["number"]) == ("draft", None)
        assert body["invoice"]["taxable_amount"] == "1000.00"

        again = await client.call_tool("bill_contract_fee", arguments)
        assert not again.is_error, again.content
        assert _payload(again)["created"] is False
        assert _payload(again)["invoice"]["id"] == body["invoice"]["id"]

        after = await client.call_tool(
            "list_fee_occurrences",
            {
                "contract_id": str(contract.id),
                "date_from": rows[0]["period_start"],
                "date_to": rows[0]["period_start"],
            },
        )
        (row,) = _payload(after)["occurrences"]
        assert (row["state"], row["invoice"]["id"]) == ("drafted", body["invoice"]["id"])


async def test_a_date_that_is_not_an_occurrence_is_refused_with_the_ones_that_are(
    mcp_session: Session, tmp_path: Path
) -> None:
    contract, card = _world(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        refused = await client.call_tool(
            "bill_contract_fee",
            {
                "contract_id": str(contract.id),
                "rate_card_id": str(card.id),
                "period_start": (card.valid_from + dt.timedelta(days=1)).isoformat(),
            },
        )

        assert refused.is_error
        assert card.valid_from.isoformat() in str(refused.content)
        malformed = await client.call_tool(
            "bill_contract_fee",
            {
                "contract_id": str(contract.id),
                "rate_card_id": str(card.id),
                "period_start": "primo novembre",
            },
        )
        assert malformed.is_error


async def test_a_token_without_full_access_cannot_call_the_draft_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    contract, card = _world(mcp_session)
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool(
            "bill_contract_fee",
            {
                "contract_id": str(contract.id),
                "rate_card_id": str(card.id),
                "period_start": card.valid_from.isoformat(),
            },
        )

        assert result.is_error
        assert "Unknown tool" in str(result.content)
