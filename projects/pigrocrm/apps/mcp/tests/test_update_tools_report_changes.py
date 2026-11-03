"""REB-807: an update tool refuses a call that names nothing and, when it applies one,
says which fields it changed.

`update_deal(changes={})` used to answer with the deal as it was, and a client that drops
the `null`s of a nested object turned «clear this field» into exactly that call. These are
real MCP round trips, so what is asserted is what an agent reads.
"""

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp import Client
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.timetracking.models import CostCategory
from pigrocrm_mcp.tools.changes import report

ADMIN = Actor(id=None, type="mcp", role="admin")
SETUP = Actor(id=None, type="system", role="admin")  # seeding a pipeline is not an agent's

REFUSAL = "nessun campo da modificare"


def _ids(*names: str) -> dict[str, str]:
    return {name: str(uuid4()) for name in names}


# Every update tool that takes a `changes` object, with the identifiers it needs. The ids are
# made up: the refusal comes before anything is read, which is the point of it.
CHANGES_TOOLS: list[tuple[str, dict[str, str]]] = [
    ("update_customer", _ids("customer_id")),
    ("update_person", _ids("person_id")),
    ("update_deal", _ids("deal_id")),
    ("update_contract", _ids("contract_id")),
    ("update_rate_card", _ids("contract_id", "rate_card_id")),
    ("update_clause_note", _ids("contract_id", "note_id")),
    ("update_contract_expense", _ids("contract_id", "expense_id")),
    ("update_time_entry", _ids("entry_id")),
    ("update_cost", _ids("cost_id")),
    ("update_task", _ids("task_id")),
]


@pytest.mark.parametrize(("tool", "identifiers"), CHANGES_TOOLS, ids=[t for t, _ in CHANGES_TOOLS])
async def test_an_update_that_names_no_field_is_refused_in_italian(
    server, tool: str, identifiers: dict[str, str]
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(tool, {**identifiers, "changes": {}})

    assert result.is_error
    message = result.content[0].text
    assert REFUSAL in message
    assert "changes" in message
    assert "errors.pydantic.dev" not in message


@pytest.mark.parametrize("tool", ["update_proforma", "update_timer"])
async def test_a_tool_whose_fields_are_parameters_refuses_a_call_that_gives_none(
    server, tool: str
) -> None:
    arguments = {"invoice_id": str(uuid4())} if tool == "update_proforma" else {}
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments)

    assert result.is_error
    assert REFUSAL in result.content[0].text


class _Row(BaseModel):
    id: str
    name: str
    note: str | None
    value: str
    updated_at: str
    custom_fields: dict[str, Any]


class _Detail(_Row):
    only_in_detail: int = 1


def test_report_lists_what_differs_and_what_was_cleared() -> None:
    before = _Detail(
        id="1",
        name="Vecchio",
        note="da togliere",
        value="10.00",
        updated_at="ieri",
        custom_fields={"sede": "Roma", "level": "A"},
    )
    after = _Row(
        id="1",
        name="Nuovo",
        note=None,
        value="10.00",
        updated_at="today",
        custom_fields={"sede": None, "level": "A", "channel": "web"},
    )

    result = report(before, after)

    # `only_in_detail` is on one side only and `updated_at` is bookkeeping: neither is
    # a change. A custom field that appeared or emptied is.
    assert result["changed"] == ["custom_fields.channel", "custom_fields.sede", "name", "note"]
    assert result["cleared"] == {"custom_fields.sede": "Roma", "note": "da togliere"}
    assert result["name"] == "Nuovo"


async def _create(client: Client, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def _customer(client: Client) -> dict[str, Any]:
    return await _create(
        client,
        "create_customer",
        {
            "legal_name": "ACME Srl",
            "certified_email": "acme@pec.example.test",
            "phone": "0212345678",
            "email": "info@acme.example.test",
        },
    )


async def _deal(client: Client, customer_id: str) -> dict[str, Any]:
    return await _create(
        client,
        "create_deal",
        {
            "name": "Progetto",
            "customer_id": customer_id,
            "expected_value": "1000.00",
            "expected_close_date": "2026-12-31",
            "budgeted_hours": "10.00",
            "budgeted_value": "900.00",
        },
    )


@pytest.mark.parametrize("field", ["certified_email", "phone", "email"])
async def test_clearing_a_customer_field_is_reported_with_the_value_it_held(
    server, field: str
) -> None:
    async with Client(server) as client:
        customer = await _customer(client)
        was = customer[field]
        result = await _create(
            client,
            "update_customer",
            {"customer_id": customer["id"], "changes": {field: None}},
        )

    assert result[field] is None
    assert result["changed"] == [field]
    assert result["cleared"] == {field: was}


@pytest.mark.parametrize(
    "field",
    [
        "expected_value",
        "expected_close_date",
        "budgeted_hours",
        "budgeted_value",
    ],
)
async def test_clearing_a_deal_field_is_reported_with_the_value_it_held(
    server, mcp_session: Session, field: str
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = await _customer(client)
        deal = await _deal(client, customer["id"])
        was = deal[field]
        assert was is not None
        result = await _create(
            client, "update_deal", {"deal_id": deal["id"], "changes": {field: None}}
        )

    assert result[field] is None
    assert result["changed"] == [field]
    assert result["cleared"] == {field: was}


async def test_a_value_that_is_not_new_is_not_reported_as_changed(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = await _customer(client)
        deal = await _deal(client, customer["id"])
        result = await _create(
            client,
            "update_deal",
            {
                "deal_id": deal["id"],
                "changes": {"name": "Rinominato", "expected_value": deal["expected_value"]},
            },
        )

    assert result["changed"] == ["name"]
    assert result["cleared"] == {}


async def test_clearing_a_person_field_is_reported(server) -> None:
    async with Client(server) as client:
        person = await _create(
            client,
            "create_person",
            {"first_name": "Mario", "last_name": "Rossi", "role": "CTO"},
        )
        result = await _create(
            client, "update_person", {"person_id": person["id"], "changes": {"role": None}}
        )

    assert result["changed"] == ["role"]
    assert result["cleared"] == {"role": "CTO"}


async def test_detaching_a_person_from_a_customer_is_reported_as_cleared(server) -> None:
    async with Client(server) as client:
        customer = await _customer(client)
        person = await _create(
            client,
            "create_person",
            {"first_name": "Mario", "last_name": "Rossi", "customer_id": customer["id"]},
        )
        result = await _create(
            client, "update_person", {"person_id": person["id"], "changes": {"detach": True}}
        )

    # The customer's name travels with the person, so it goes with the id.
    assert result["changed"] == ["customer_id", "customer_legal_name"]
    assert result["cleared"] == {
        "customer_id": customer["id"],
        "customer_legal_name": "ACME Srl",
    }


@pytest.mark.parametrize("field", ["end", "note"])
async def test_clearing_a_contract_field_is_reported(
    server, mcp_session: Session, field: str
) -> None:
    customer = Customer(legal_name="ACME Srl")
    mcp_session.add(customer)
    mcp_session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        note="rinnovo da discutere",
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
    )
    mcp_session.add(contract)
    mcp_session.flush()
    mcp_session.commit()

    async with Client(server) as client:
        result = await _create(
            client,
            "update_contract",
            {"contract_id": str(contract.id), "changes": {field: None}},
        )

    assert result[field] is None
    assert result["changed"] == [field]
    assert list(result["cleared"]) == [field]


async def test_clearing_an_task_date_by_flag_is_reported_as_cleared(server) -> None:
    async with Client(server) as client:
        attivita = await _create(
            client, "create_task", {"title": "Richiamare", "due_date": "2026-11-30"}
        )
        result = await _create(
            client,
            "update_task",
            {"task_id": attivita["id"], "changes": {"due_date_to_remove": True}},
        )

    assert result["due_date"] is None
    assert result["changed"] == ["due_date"]
    assert result["cleared"] == {"due_date": "2026-11-30"}


async def test_update_task_advertises_its_fields(server) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    changes = tools["update_task"].input_schema["properties"]["changes"]
    assert {"title", "due_date", "assigned_to", "due_date_to_remove"} <= set(changes["properties"])


async def test_clearing_a_cost_and_a_time_entry_field_is_reported(
    server, mcp_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    category = CostCategory(name="Software", position=0)
    mcp_session.add(category)
    mcp_session.flush()
    async with Client(server) as client:
        entry = await _create(
            client,
            "log_time",
            {
                "deal_id": str(seeded_deal_id),
                "user_id": str(seeded_user_id),
                "date": "2026-03-10",
                "hours": "2.00",
                "description": "Sviluppo",
                "internal_notes": "da rivedere",
            },
        )
        cleared_entry = await _create(
            client,
            "update_time_entry",
            {"entry_id": entry["id"], "changes": {"internal_notes": None}},
        )
        cost = await _create(
            client,
            "create_cost",
            {
                "category_id": str(category.id),
                "date": "2026-03-10",
                "amount": "50.00",
                "description": "Licenza",
                "supplier": "Acme",
            },
        )
        cleared_cost = await _create(
            client, "update_cost", {"cost_id": cost["id"], "changes": {"supplier": None}}
        )

    assert cleared_entry["changed"] == ["internal_notes"]
    assert cleared_entry["cleared"] == {"internal_notes": "da rivedere"}
    assert cleared_cost["changed"] == ["supplier"]
    assert cleared_cost["cleared"] == {"supplier": "Acme"}


async def test_the_same_amount_written_with_another_scale_is_not_a_change(
    server, mcp_session: Session
) -> None:
    """The database answers `1000.00` and the update just set `1000`: one amount, and the
    agent that resends it must not be told it changed something."""
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = await _customer(client)
        deal = await _deal(client, customer["id"])
        result = await _create(
            client,
            "update_deal",
            {"deal_id": deal["id"], "changes": {"expected_value": 1000, "probability": 50}},
        )

    assert result["changed"] == ["probability"]


async def test_custom_fields_emptied_by_a_client_that_drops_nulls_is_refused(server) -> None:
    """`{"custom_fields": {"sede": null}}` arrives as `{"custom_fields": {}}` when a client
    drops the nulls of a nested object: it names nothing, and is told so."""
    async with Client(server) as client:
        result = await client.call_tool(
            "update_customer", {"customer_id": str(uuid4()), "changes": {"custom_fields": {}}}
        )

    assert result.is_error
    assert REFUSAL in result.content[0].text


async def test_update_user_rates_writes_only_the_rate_it_is_given_and_says_so(
    mcp_session: Session, tmp_path, seeded_user_id: UUID
) -> None:
    """It used to pass both rates on every call, so `default_hourly_rate=50` alone also
    emptied `default_hourly_cost`; and it answered a bare «tariffe aggiornate»."""
    from decimal import Decimal

    from pigrocrm.core.auth.models import User
    from pigrocrm.core.config import Settings
    from pigrocrm.core.storage import LocalFileStorage
    from pigrocrm_mcp.server import build_server

    user = mcp_session.get(User, seeded_user_id)
    assert user is not None
    user.default_hourly_cost = Decimal("30.00")
    mcp_session.flush()
    privileged = build_server(
        lambda: mcp_session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=True),
        LocalFileStorage(tmp_path),
        settings=Settings(_env_file=None, mcp_full_access=True),  # type: ignore[call-arg]
    )

    async with Client(privileged) as client:
        written = await _create(
            client,
            "update_user_rates",
            {"user_id": str(seeded_user_id), "default_hourly_rate": "50.00"},
        )
        nothing = await client.call_tool("update_user_rates", {"user_id": str(seeded_user_id)})

    assert written["changed"] == ["default_hourly_rate"]
    mcp_session.refresh(user)
    assert user.default_hourly_rate == Decimal("50.00")
    assert user.default_hourly_cost == Decimal("30.00")
    assert nothing.is_error
    assert REFUSAL in nothing.content[0].text
