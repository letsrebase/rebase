"""`GET /api/search`, and the three §8.6 states as far as the API can express them.

Two of the three are the API's to produce. "No results" is four empty groups with four
zero counts, and "truncated results with the real count" is `totale = 200` with
`totale_e_un_minimo = true` -- one response shape, not two, so the client never has to
guess which it is looking at. The third state, "search unavailable", is the API failing
loudly with a problem document; rendering that as an error rather than as an empty list is
the frontend's job and task A14 asserts it.

The contract details that matter are pinned on the **OpenAPI document** and not only on the
generated types: `openapi-typescript` erases `minLength` and `maxLength`, so a bound that
lives only in the generated client is a bound no test can see (the pattern task A5
established).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.search.schemas import COUNT_CEILING

# Five, and the order is fixed: a palette whose sections move between keystrokes
# cannot be driven with the keyboard. `invoice` is last, added by Task C12 with the
# branch that finally makes `SearchEntity`'s fifth member true.
_ENTITIES = ["customer", "person", "deal", "document", "invoice", "task"]


def _search(client: TestClient, **params: Any) -> Any:
    return client.get("/api/search", params=params)


def test_a_search_returns_all_four_groups(logged_in: TestClient) -> None:
    created = logged_in.post("/api/customers", json={"legal_name": "Vulcano Impianti Srl"})
    assert created.status_code == 201, created.text

    response = _search(logged_in, q="Vulcano")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["term"] == "Vulcano"
    assert [group["entity"] for group in body["groups"]] == _ENTITIES
    customer = body["groups"][0]
    assert customer["total"] == 1
    assert customer["total_is_minimum"] is False
    assert customer["hits"][0]["label"] == "Vulcano Impianti Srl"
    assert customer["hits"][0]["field"] == "legal_name"
    assert customer["hits"][0]["id"] == created.json()["id"]


def test_a_term_that_matches_nothing_is_four_empty_groups_and_not_a_404(
    logged_in: TestClient,
) -> None:
    """§8.6's first state. A missing group and an empty group render identically in a
    palette, so the client would have to guess which it was."""
    response = _search(logged_in, q="zzqqxxwwyy")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [group["entity"] for group in body["groups"]] == _ENTITIES
    assert all(group["hits"] == [] for group in body["groups"])
    assert all(group["total"] == 0 for group in body["groups"])
    assert all(group["total_is_minimum"] is False for group in body["groups"])


def test_more_matches_than_the_ceiling_are_declared_as_a_minimum(
    logged_in: TestClient, api_session: Session
) -> None:
    """§8.6's second state, and the whole reason `total_is_minimum` exists.

    Inserted straight onto the session rather than through 201 `POST`s: the endpoint under
    test is the `GET`, and two hundred round trips through the writing half of the API
    would measure that instead.
    """
    api_session.execute(
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
    api_session.flush()

    body = _search(logged_in, q="Moltissimi").json()

    customer = body["groups"][0]
    assert customer["total"] == COUNT_CEILING
    assert customer["total_is_minimum"] is True
    # Still five on screen, and five is what the palette shows -- the count being a
    # minimum must not change the page.
    assert len(customer["hits"]) == 5


def test_the_score_is_serialised_as_a_decimal_string_not_a_float(logged_in: TestClient) -> None:
    """A float score renders as 0.6000000238418579 and breaks criterion 4.

    The value is asserted as well as the type: `"0.8000"` is §8.5's prefix rung at the
    identifying weight, at the four-place scale `SearchHit.score` declares, and a
    `Decimal` that had lost its trailing zeros would still be a string here.
    """
    logged_in.post("/api/customers", json={"legal_name": "Vulcano Impianti Srl"})

    score = _search(logged_in, q="Vulcano").json()["groups"][0]["hits"][0]["score"]

    assert isinstance(score, str), type(score)
    assert score == "0.8000"


@pytest.mark.parametrize("term", ["", "a", "ab"])
def test_a_term_shorter_than_three_characters_is_a_422(logged_in: TestClient, term: str) -> None:
    assert _search(logged_in, q=term).status_code == 422


def test_a_missing_term_is_a_422_and_not_a_search_for_everything(logged_in: TestClient) -> None:
    assert logged_in.get("/api/search").status_code == 422


def test_a_term_longer_than_the_maximum_is_a_422(logged_in: TestClient) -> None:
    assert _search(logged_in, q="a" * 101).status_code == 422


def test_a_nul_byte_in_the_term_is_refused(logged_in: TestClient) -> None:
    """`SafeStr` on a query parameter, not only on a body field. Global Constraints puts
    every free-text query parameter behind it, and a `q` that reached `ILIKE` with a NUL
    byte in it would come back as a raw driver error with the session poisoned."""
    assert _search(logged_in, q="Vulc\x00ano").status_code == 422


def test_a_readonly_actor_may_search(readonly_client: TestClient) -> None:
    """No authorisation rule is added by this slice (spec §13): search reads the same rows
    the four list endpoints already return to every role."""
    assert _search(readonly_client, q="Vulcano").status_code == 200


def test_an_unauthenticated_request_is_a_401(client: TestClient) -> None:
    assert _search(client, q="Vulcano").status_code == 401


@pytest.mark.parametrize("limit", [0, -1, 21, 500])
def test_the_limit_is_bounded(logged_in: TestClient, limit: int) -> None:
    assert _search(logged_in, q="abc", limit=limit).status_code == 422


def test_the_limit_is_honoured(logged_in: TestClient, api_session: Session) -> None:
    """A bound nobody applies is a bound that does nothing. Twelve matching rows, a limit
    of three, three hits and a `total` of twelve -- the limit pages the hits and does not
    touch the count."""
    api_session.execute(
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
    api_session.flush()

    body = _search(logged_in, q="Limitabile", limit=3).json()

    assert len(body["groups"][0]["hits"]) == 3
    assert body["groups"][0]["total"] == 12


def test_the_endpoint_appears_in_the_openapi_document(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/search" in schema["paths"]
    assert "get" in schema["paths"]["/api/search"]


def test_the_openapi_document_pins_the_bounds_the_generated_types_erase(
    client: TestClient,
) -> None:
    """`openapi-typescript` drops `minLength`, `maxLength` and numeric bounds, so the
    generated client cannot carry them and `tsc` cannot break on them. They are asserted
    here, on the document itself, which is task A5's established pattern for exactly this.
    """
    operation = client.get("/openapi.json").json()["paths"]["/api/search"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    # `legal_entity_id` joined with REB-624: the sidebar's azienda narrows the palette too.
    assert set(parameters) == {"q", "limit", "legal_entity_id"}, parameters
    assert parameters["q"]["required"] is True
    assert parameters["q"]["schema"]["minLength"] == 3
    assert parameters["q"]["schema"]["maxLength"] == 100
    assert parameters["limit"]["required"] is False
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["limit"]["schema"]["maximum"] == 20
    assert parameters["limit"]["schema"]["default"] == 5
