"""The HTTP half of residuo R9.

`dir` is spelled `dir` and not `direction` because the spec fixes it (§8.4) and because
`dir` is what the frontend query key carries; it shadows no Python builtin at module
scope here, since it is only ever a parameter name.

Two notes on what the assertions are and are not.

**The fixture is `logged_in`, not the brief's `client, admin_cookie`.** There is no
`admin_cookie` fixture in this suite; `logged_in` is a `TestClient` that has already
posted to `/api/auth/login` and is carrying the cookie jar (conftest.py).

**An unknown `sort` is a problem document; an unknown `dir` is not.** `sort` is a plain
string validated against the whitelist by the repository, which raises this project's
own `ValidationFailed`, so the response carries `field`. `dir` is a `Literal` at the
FastAPI boundary, so it never reaches the service and comes back in FastAPI's own
`HTTPValidationError` shape. Both are 422; only one has a `field` key, and asserting
otherwise would be asserting a shape the code does not produce.
"""

import pytest
from fastapi.testclient import TestClient

LIST_PATHS = ["/api/organizations", "/api/people", "/api/deals", "/api/documents"]


def _create_organizations(client: TestClient, *names: str) -> None:
    for name in names:
        created = client.post("/api/organizations", json={"legal_name": name})
        assert created.status_code == 201, created.text


def test_sorting_by_the_identifying_column(logged_in: TestClient) -> None:
    _create_organizations(logged_in, "Gamma Srl", "Alfa Srl", "Beta Srl")

    response = logged_in.get(
        "/api/organizations", params={"sort": "legal_name", "dir": "asc", "limit": 10}
    )
    assert response.status_code == 200, response.text
    assert [c["legal_name"] for c in response.json()["items"]] == [
        "Alfa Srl",
        "Beta Srl",
        "Gamma Srl",
    ]

    descending = logged_in.get(
        "/api/organizations", params={"sort": "legal_name", "dir": "desc", "limit": 10}
    )
    assert [c["legal_name"] for c in descending.json()["items"]] == [
        "Gamma Srl",
        "Beta Srl",
        "Alfa Srl",
    ]


def test_next_cursor_is_a_string_the_client_can_echo_back(logged_in: TestClient) -> None:
    """The round trip, over real HTTP and through real query-string encoding: a cursor
    is base64url, which contains `-` and `_` and nothing that needs escaping, but this
    is the test that would fail if it ever grew a `+` or a `=`."""
    _create_organizations(logged_in, "Alfa Srl", "Beta Srl", "Gamma Srl")

    first = logged_in.get("/api/organizations", params={"sort": "legal_name", "limit": 2})
    body = first.json()
    assert isinstance(body["next_cursor"], str)

    second = logged_in.get(
        "/api/organizations",
        params={"sort": "legal_name", "limit": 2, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    assert [c["legal_name"] for c in second.json()["items"]] == ["Gamma Srl"]
    assert second.json()["next_cursor"] is None


@pytest.mark.parametrize("path", LIST_PATHS)
def test_an_unknown_sort_key_is_a_422_naming_the_field(logged_in: TestClient, path: str) -> None:
    response = logged_in.get(path, params={"sort": "note"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["field"] == "sort", body
    assert "created_at" in body["expected"], body


@pytest.mark.parametrize("path", LIST_PATHS)
def test_an_unknown_direction_is_a_422(logged_in: TestClient, path: str) -> None:
    """`dir` is a Literal, so FastAPI rejects it before the service is reached -- which
    is why this assertion is on the status code and not on a `field` key: a FastAPI
    validation error is the `application/json` HTTPValidationError shape, not the
    problem+json shape."""
    response = logged_in.get(path, params={"dir": "sideways"})
    assert response.status_code == 422


@pytest.mark.parametrize("path", LIST_PATHS)
def test_the_default_direction_is_ascending_and_needs_no_parameter(
    logged_in: TestClient, path: str
) -> None:
    """`dir` has a default, so omitting it must not be a 422 -- the failure mode of
    declaring a `Literal` query parameter without one."""
    assert logged_in.get(path, params={"limit": 1}).status_code == 200


def test_a_garbage_cursor_is_a_422_and_not_a_500(logged_in: TestClient) -> None:
    response = logged_in.get("/api/organizations", params={"cursor": "not-a-cursor"})
    assert response.status_code == 422, response.text
    assert response.json()["field"] == "cursor"


def test_an_over_long_cursor_is_refused_before_it_is_decoded(logged_in: TestClient) -> None:
    """FastAPI's own `max_length` answers first, so nothing hands 5 000 characters to a
    base64 decoder. That is a bound on work done, not merely on values accepted."""
    response = logged_in.get("/api/organizations", params={"cursor": "x" * 5000})
    assert response.status_code == 422


def test_a_cursor_produced_for_one_sort_is_refused_by_another(logged_in: TestClient) -> None:
    """Changing `sort` while replaying `next_cursor` would otherwise compare a company
    name against a timestamp and return an arbitrary page."""
    _create_organizations(logged_in, "Alfa Srl", "Beta Srl", "Gamma Srl")
    first = logged_in.get("/api/organizations", params={"sort": "legal_name", "limit": 2})
    cursor = first.json()["next_cursor"]

    response = logged_in.get(
        "/api/organizations", params={"sort": "created_at", "limit": 2, "cursor": cursor}
    )
    assert response.status_code == 422, response.text
    assert response.json()["field"] == "cursor"


def test_documents_accept_a_search_term_and_it_actually_filters(logged_in: TestClient) -> None:
    customer_id = logged_in.post("/api/organizations", json={"legal_name": "ACME"}).json()["id"]
    for title in ("Offerta impianti", "Verbale riunione"):
        created = logged_in.post(
            "/api/documents",
            json={"customer_id": customer_id, "type": "document", "title": title},
        )
        assert created.status_code == 201, created.text

    response = logged_in.get("/api/documents", params={"search": "offer"})
    assert response.status_code == 200, response.text
    assert [d["title"] for d in response.json()["items"]] == ["Offerta impianti"]


@pytest.mark.parametrize("path", LIST_PATHS)
def test_the_openapi_document_types_cursor_as_a_bounded_string(
    client: TestClient, path: str
) -> None:
    """The mechanism slice 1 §10.2 put in place for exactly this change: the generated
    TypeScript client is regenerated from this document, so the contract moves here or
    it does not move at all.

    `maxLength` is asserted as well as the type. `openapi-typescript` erases both to
    `string`, so this is the only place the bound is visible to a reader of the
    contract -- and it is the difference between a cursor parameter and an unbounded
    one reaching a decoder.
    """
    schema = client.get("/openapi.json").json()
    parameters = schema["paths"][path]["get"]["parameters"]
    cursor = next(p for p in parameters if p["name"] == "cursor")
    rendered = str(cursor["schema"])
    assert "string" in rendered, cursor
    assert "uuid" not in rendered.lower(), cursor
    assert "maxLength" in rendered, cursor


@pytest.mark.parametrize("path", LIST_PATHS)
def test_the_openapi_document_declares_sort_and_dir(client: TestClient, path: str) -> None:
    schema = client.get("/openapi.json").json()
    names = {p["name"] for p in schema["paths"][path]["get"]["parameters"]}
    assert {"sort", "dir"} <= names, sorted(names)
