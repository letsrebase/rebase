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
_ENTITIES = ["customer", "person", "deal", "document", "invoice", "attivita"]

# The example the tool's own docstring gives an agent, and the one spec §17 names as 6A's
# reason to exist.
_DOCSTRING_PIVA = "01234567890"
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
        result = await client.call_tool("search_everything", {"termine": "ACME"})

    payload = _payload(result)
    assert [group["entity"] for group in payload["gruppi"]] == _ENTITIES
    assert payload["gruppi"][0]["hits"][0]["id"] == seeded_customer_id
    assert payload["gruppi"][0]["hits"][0]["etichetta"] == customer.ragione_sociale


async def test_the_score_survives_as_a_string_and_not_as_a_float(
    server: Any, seeded_customer_id: str
) -> None:
    """An agent reading `0.6000000238418579` would be reading a different number from the
    one a person sees on the same row.

    What this catches is `SearchHit.punteggio` becoming a `float`: a float serialises as a
    JSON number and `isinstance(..., str)` fails. It does **not** catch `mode="json"` being
    dropped from the tool module -- measured, and recorded there: the SDK's own encoder
    renders a `Decimal` as a string regardless. The safeguard is the schema type.
    """
    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"termine": "ACME"})

    punteggio = _payload(result)["gruppi"][0]["hits"][0]["punteggio"]
    assert isinstance(punteggio, str), type(punteggio)
    assert punteggio == "0.8000"


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
                "ragione_sociale": "Frammento Srl",
                "partita_iva": _DOCSTRING_PIVA,
                "nazione": "IT",
                "custom_fields": {},
            }
        ],
    )
    mcp_session.flush()

    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"termine": _DOCSTRING_FRAGMENT})

    customers = _payload(result)["gruppi"][0]
    assert [hit["etichetta"] for hit in customers["hits"]] == ["Frammento Srl"]
    assert customers["hits"][0]["campo"] == "partita_iva"
    assert customers["hits"][0]["sottotitolo"] == _DOCSTRING_PIVA


async def test_the_docstring_promises_the_example_it_is_tested_against(server: Any) -> None:
    """The other direction: the test above proves the behaviour, this one proves the agent
    is told about it. Either half alone can drift away from the other."""
    async with Client(server) as client:
        tool = next(
            tool for tool in (await client.list_tools()).tools if tool.name == "search_everything"
        )

    description = tool.description or ""
    assert _DOCSTRING_FRAGMENT in description and _DOCSTRING_PIVA in description, description
    assert "3 caratteri" in description, description
    assert "totale_e_un_minimo" in description, description
    # The fifth branch, since Task C12. A docstring naming four entity classes when the
    # tool searches five is how an agent concludes an invoice does not exist rather than
    # that it did not look -- the same silent partial result the branch was added to close,
    # moved from the response into the documentation.
    assert "fatture" in description, description
    assert "2026/7" in description, description


async def test_a_two_character_term_is_a_domain_error_not_a_scan(server: Any) -> None:
    """`_guard` converts the pydantic failure into a message the agent can act on, instead
    of the SDK rejecting it with one we did not write.

    `termine` is a bare `str` at the tool boundary precisely so this happens: typed with a
    length bound, the SDK's own pre-call `validate_arguments` would refuse it before the
    guard ran, and the agent would get a raw English pydantic dump with a link in it.
    """
    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"termine": "ab"})

    assert result.is_error
    rendered = result.content[0].text
    # The offending field is named: `fieldErrorFrom` in the web client and an agent both
    # read it, and "something was wrong" is not an instruction.
    assert "termine" in rendered, rendered
    assert "3" in rendered, rendered
    # Ours, not the SDK's: `errors.py`'s own remediation line for `validation_failed`.
    assert "Correggi il valore indicato e riprova." in rendered, rendered
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
                "ragione_sociale": f"Moltissimi Srl {index:03d}",
                "nazione": "IT",
                "custom_fields": {},
            }
            for index in range(COUNT_CEILING + 1)
        ],
    )
    mcp_session.flush()

    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"termine": "Moltissimi"})

    customers = _payload(result)["gruppi"][0]
    assert customers["totale"] == COUNT_CEILING
    assert customers["totale_e_un_minimo"] is True
    assert len(customers["hits"]) == 5


async def test_the_limite_argument_is_honoured(server: Any, mcp_session: Session) -> None:
    mcp_session.execute(
        insert(Customer),
        [
            {
                "id": uuid7(),
                "ragione_sociale": f"Limitabile Srl {index:02d}",
                "nazione": "IT",
                "custom_fields": {},
            }
            for index in range(12)
        ],
    )
    mcp_session.flush()

    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"termine": "Limitabile", "limite": 3})

    customers = _payload(result)["gruppi"][0]
    assert len(customers["hits"]) == 3
    assert customers["totale"] == 12
