"""The MCP half. Same service, same figures, one call.

Spec §11.1 lists `search_everything` on both surfaces. The reason it is a tool rather than
four is the same reason it is one service method: an agent that had to call four searches
and merge them would be doing in a transcript what the service already does in one query.

**The docstring is tested, not just written.** A tool's docstring is its contract with the
agent -- it is the only documentation the model ever sees -- so the two promises it makes
are executable here: that a fragment in the middle of a word finds the row (the "34567
finds P.IVA 01234567890" example, which task A8's deviation from spec §8.5 is what makes
true), and that three characters are the minimum. A docstring that promised something the
code did not do would mislead every agent that read it and no test would notice.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from mcp import Client
from sqlalchemy import insert
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.search.schemas import COUNT_CEILING

# Five, and the order is fixed: a palette whose sections move between keystrokes
# cannot be driven with the keyboard. `invoice` is last, added by Task C12 with the
# branch that finally makes `SearchEntity`'s fifth member true.
_ENTITIES = ["customer", "person", "deal", "document", "invoice", "task"]

# The example the tool's own docstring gives an agent, and the one spec §17 names as 6A's
# reason to exist.
_DOCSTRING_VAT_NUMBER = "01234567890"
_DOCSTRING_FRAGMENT = "34567"


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def test_search_everything_is_registered(server: Any) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert "search_everything" in names


async def test_search_everything_returns_the_four_groups(
    server: Any, seeded_customer_id: str, mcp_session: Session
) -> None:
    customer = mcp_session.get(Customer, UUID(seeded_customer_id))
    assert customer is not None

    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"term": "ACME"})

    payload = _payload(result)
    assert [group["entity"] for group in payload["groups"]] == _ENTITIES
    assert payload["groups"][0]["hits"][0]["id"] == seeded_customer_id
    assert payload["groups"][0]["hits"][0]["label"] == customer.legal_name


async def test_the_score_survives_as_a_string_and_not_as_a_float(
    server: Any, seeded_customer_id: str
) -> None:
    """An agent reading `0.6000000238418579` would be reading a different number from the
    one a person sees on the same row.

    What this catches is `SearchHit.score` becoming a `float`: a float serialises as a
    JSON number and `isinstance(..., str)` fails. It does **not** catch `mode="json"` being
    dropped from the tool module -- measured, and recorded there: the SDK's own encoder
    renders a `Decimal` as a string regardless. The safeguard is the schema type.
    """
    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"term": "ACME"})

    score = _payload(result)["groups"][0]["hits"][0]["score"]
    assert isinstance(score, str), type(score)
    assert score == "0.8000"


async def test_the_docstring_example_actually_works(server: Any, mcp_session: Session) -> None:
    """The docstring's «34567» → P.IVA 01234567890 example, asserted rather than promised.

    Implemented literally, spec §8.5's third rung scores this pair at 0.1200 -- under the
    0.20 floor -- and the example would be false. Task A8 changed the measure on that rung
    to `word_similarity(termine, campo)` for exactly this reason and recorded the numbers;
    this is the test that keeps the docstring honest if anyone ever changes it back.
    """
    mcp_session.execute(
        insert(Customer),
        [
            {
                "id": uuid7(),
                "legal_name": "Frammento Srl",
                "vat_number": _DOCSTRING_VAT_NUMBER,
                "country": "IT",
                "custom_fields": {},
            }
        ],
    )
    mcp_session.flush()

    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"term": _DOCSTRING_FRAGMENT})

    customers = _payload(result)["groups"][0]
    assert [hit["label"] for hit in customers["hits"]] == ["Frammento Srl"]
    assert customers["hits"][0]["field"] == "vat_number"
    assert customers["hits"][0]["subtitle"] == _DOCSTRING_VAT_NUMBER


async def test_the_docstring_promises_the_example_it_is_tested_against(server: Any) -> None:
    """The other direction: the test above proves the behaviour, this one proves the agent
    is told about it. Either half alone can drift away from the other."""
    async with Client(server) as client:
        tool = next(
            tool for tool in (await client.list_tools()).tools if tool.name == "search_everything"
        )

    description = tool.description or ""
    assert _DOCSTRING_FRAGMENT in description and _DOCSTRING_VAT_NUMBER in description, description
    assert "At least 3 characters" in description, description
    assert "total_is_minimum" in description, description
    # The fifth branch, since Task C12. A docstring naming four entity classes when the
    # tool searches five is how an agent concludes an invoice does not exist rather than
    # that it did not look -- the same silent partial result the branch was added to close,
    # moved from the response into the documentation.
    assert "invoices" in description, description
    assert "2026/7" in description, description


async def test_a_two_character_term_is_a_domain_error_not_a_scan(server: Any) -> None:
    """`_guard` converts the pydantic failure into a message the agent can act on, instead
    of the SDK rejecting it with one we did not write.

    `term` is a bare `str` at the tool boundary precisely so this happens: typed with a
    length bound, the SDK's own pre-call `validate_arguments` would refuse it before the
    guard ran, and the agent would get a raw English pydantic dump with a link in it.
    """
    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"term": "ab"})

    assert result.is_error
    rendered = result.content[0].text
    # The offending field is named: `fieldErrorFrom` in the web client and an agent both
    # read it, and "something was wrong" is not an instruction.
    assert "term" in rendered, rendered
    assert "3" in rendered, rendered
    # Ours, not the SDK's: `errors.py`'s own remediation line for `validation_failed`.
    assert "Fix the indicated value and try again." in rendered, rendered
    assert "errors.pydantic.dev" not in rendered, rendered


async def test_a_truncated_count_is_declared_to_the_agent_as_a_minimum(
    server: Any, mcp_session: Session
) -> None:
    """The agent gets §8.6's second state in the same shape a person does. An agent told
    "200" when the answer is "at least 200" will report a number that is not true."""
    mcp_session.execute(
        insert(Customer),
        [
            {
                "id": uuid7(),
                "legal_name": f"Moltissimi Srl {index:03d}",
                "country": "IT",
                "custom_fields": {},
            }
            for index in range(COUNT_CEILING + 1)
        ],
    )
    mcp_session.flush()

    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"term": "Moltissimi"})

    customers = _payload(result)["groups"][0]
    assert customers["total"] == COUNT_CEILING
    assert customers["total_is_minimum"] is True
    assert len(customers["hits"]) == 5


async def test_the_limit_argument_is_honoured(server: Any, mcp_session: Session) -> None:
    mcp_session.execute(
        insert(Customer),
        [
            {
                "id": uuid7(),
                "legal_name": f"Limitabile Srl {index:02d}",
                "country": "IT",
                "custom_fields": {},
            }
            for index in range(12)
        ],
    )
    mcp_session.flush()

    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"term": "Limitabile", "limit": 3})

    customers = _payload(result)["groups"][0]
    assert len(customers["hits"]) == 3
    assert customers["total"] == 12


async def test_a_phone_typed_as_digits_finds_the_customer_and_the_hit_says_phone(
    server: Any, mcp_session: Session
) -> None:
    """REB-948: the palette and the tool find a number in any form, like `search_customers`,
    and `field` names the kind of the point that matched (it used to say `email`)."""
    from pigrocrm.core.contact_points.models import ContactPoint
    from pigrocrm.core.contact_points.schemas import ContactPointIn
    from pigrocrm.core.contact_points.service import prepare

    owned = [
        ContactPoint(
            kind=point.kind,
            value=point.value,
            normalized=point.normalized,
            label=point.label,
            note=point.note,
            is_primary=point.is_primary,
            position=position,
        )
        for position, point in enumerate(
            prepare(
                "customer",
                [
                    ContactPointIn(kind="phone", value="+39 02 1234567"),
                    ContactPointIn(kind="phone", value="+39 388 871 3657", label="other"),
                ],
            )
        )
    ]
    mcp_session.add(
        Customer(legal_name="Numero Srl", country="IT", custom_fields={}, contact_points=owned)
    )
    mcp_session.flush()

    async with Client(server) as client:
        for term in ("3888713657", "+393888713657", "388-871-3657", "388 871 3657"):
            customers = _payload(await client.call_tool("search_everything", {"term": term}))[
                "groups"
            ][0]
            assert [hit["label"] for hit in customers["hits"]] == ["Numero Srl"], term
            assert customers["hits"][0]["field"] == "phone", term
