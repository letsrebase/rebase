"""A person's and a customer's contact points through the MCP (REB-941): created as a list,
read back with `email` equal to the primary, found by any address, replaced whole, and the old
flat arguments refused with the way to say it now."""

import json
from typing import Any

from mcp import Client

TWO_EMAILS_AND_A_PHONE = [
    {"kind": "email", "value": "ada@acme.it", "label": "work"},
    {"kind": "email", "value": "Ada.Private@gmail.com", "label": "personal"},
    {"kind": "phone", "value": "+39 02 1234567", "label": "billing", "note": "centralino"},
]


def _payload(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    return result.structured_content or json.loads(result.content[0].text)


async def test_a_person_with_two_emails_and_a_labelled_phone_is_read_back_and_found_by_either(
    server,
) -> None:
    async with Client(server) as client:
        created = _payload(
            await client.call_tool(
                "create_person",
                {
                    "first_name": "Ada",
                    "last_name": "Byron",
                    "contact_points": TWO_EMAILS_AND_A_PHONE,
                },
            )
        )
        read = _payload(await client.call_tool("get_person", {"person_id": created["id"]}))
        found = {
            term: _payload(await client.call_tool("search_people", {"search": term}))["items"]
            for term in ("ada.private@gmail", "ada@acme.it", "0212345")
        }
        resource = (await client.read_resource(f"person://{created['id']}")).contents[0].text

    assert read["email"] == "ada@acme.it"
    assert read["phone"] == "+39 02 1234567"
    assert [
        (p["kind"], p["value"], p["label"], p["note"], p["is_primary"])
        for p in read["contact_points"]
    ] == [
        ("email", "ada@acme.it", "work", None, True),
        ("email", "Ada.Private@gmail.com", "personal", None, False),
        ("phone", "+39 02 1234567", "billing", "centralino", True),
    ]
    for term, items in found.items():
        assert [p["id"] for p in items] == [created["id"]], term
    # After the person's own lines (customer included) and before anything else.
    assert resource.index("- Customer: not linked") < resource.index("## Contact points")
    assert resource.index("## Contact points") < resource.index("## Timeline")
    assert "- email: Ada.Private@gmail.com [personal]" in resource
    assert "- email: ada@acme.it [work] (primary)" in resource
    assert "- phone: +39 02 1234567 [billing] (primary) — centralino" in resource


async def test_update_person_replaces_the_list_and_reports_the_derived_email(server) -> None:
    async with Client(server) as client:
        person = _payload(
            await client.call_tool(
                "create_person", {"first_name": "Ada", "contact_points": TWO_EMAILS_AND_A_PHONE}
            )
        )
        updated = _payload(
            await client.call_tool(
                "update_person",
                {
                    "person_id": person["id"],
                    "changes": {
                        "contact_points": [
                            {"kind": "email", "value": "ada@acme.it"},
                            {"kind": "email", "value": "ada.new@acme.it", "is_primary": True},
                        ]
                    },
                },
            )
        )

    assert updated["email"] == "ada.new@acme.it"
    assert updated["phone"] is None
    assert [p["value"] for p in updated["contact_points"]] == ["ada@acme.it", "ada.new@acme.it"]
    assert {"contact_points", "email", "phone"} <= set(updated["changed"])


async def test_a_customer_takes_and_returns_contact_points(server) -> None:
    async with Client(server) as client:
        customer = _payload(
            await client.call_tool(
                "create_customer",
                {
                    "legal_name": "ACME Srl",
                    "certified_email": "acme@pec.it",
                    "contact_points": [
                        {"kind": "phone", "value": "+39 02 7654321"},
                        {"kind": "email", "value": "info@acme.it", "label": "billing"},
                    ],
                },
            )
        )
        read = _payload(await client.call_tool("get_customer", {"customer_id": customer["id"]}))
        found = _payload(await client.call_tool("search_customers", {"search": "02 76543"}))
        resource = (await client.read_resource(f"customer://{customer['id']}")).contents[0].text

    assert (read["email"], read["phone"], read["certified_email"]) == (
        "info@acme.it",
        "+39 02 7654321",
        "acme@pec.it",
    )
    assert [c["id"] for c in found["items"]] == [customer["id"]]
    assert "- email: info@acme.it [billing] (primary)" in resource


async def test_the_flat_arguments_are_refused_with_the_way_to_say_it_now(server) -> None:
    async with Client(server) as client:
        for tool, arguments in (
            ("create_person", {"first_name": "Ada", "email": "ada@acme.it"}),
            ("create_person", {"first_name": "Ada", "linkedin": "https://linkedin.com/in/ada"}),
            ("create_customer", {"legal_name": "ACME", "phone": "0212345"}),
        ):
            result = await client.call_tool(tool, arguments)
            assert result.is_error, (tool, arguments)
            assert "contact_points" in result.content[0].text


async def test_the_flat_fields_of_a_change_get_the_same_hint(server) -> None:
    async with Client(server) as client:
        person = _payload(await client.call_tool("create_person", {"first_name": "Ada"}))
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        for tool, key, entity_id, changes in (
            ("update_person", "person_id", person["id"], {"email": "ada@acme.it"}),
            ("update_person", "person_id", person["id"], {"linkedin": "ada"}),
            ("update_customer", "customer_id", customer["id"], {"phone": "0212345"}),
        ):
            result = await client.call_tool(tool, {key: entity_id, "changes": changes})
            assert result.is_error, (tool, changes)
            text = result.content[0].text
            assert "contact_points" in text and f"changes.{next(iter(changes))}" in text


async def test_a_refused_point_names_its_position(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "create_person",
            {
                "first_name": "Ada",
                "contact_points": [
                    {"kind": "email", "value": "a@acme.it"},
                    {"kind": "email", "value": "not-an-email"},
                ],
            },
        )

    assert result.is_error
    assert "contact_points[1]" in result.content[0].text
