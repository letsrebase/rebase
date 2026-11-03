"""The HTTP surface, thin by design: validate the body, resolve the Actor, call the
service, serialise. Every assertion here is about the wire -- statuses, problem
documents, and Decimal-as-string -- never about business rules, which are proven in
packages/core.
"""

from typing import Any

from fastapi.testclient import TestClient


def _second_actor(admin_client: TestClient, role: str) -> TestClient:
    """A genuinely independent session, not `admin_client`'s own cookie jar. Copied
    from `test_invoices_api.py`'s helper of the same name and purpose -- see its
    docstring for why `logged_in` and `collaborator_client` cannot both be requested
    in the same test."""
    email = f"{role}-{id(admin_client)}@pigro.it"
    created = admin_client.post(
        "/api/users",
        json={"email": email, "password": "supersegreta1", "name": "Test", "role": role},
    )
    assert created.status_code == 201, created.text
    client = TestClient(admin_client.app, base_url="https://testserver")
    response = client.post("/api/auth/login", json={"email": email, "password": "supersegreta1"})
    assert response.status_code == 200, response.text
    return client


def _seed_pipeline(logged_in: TestClient) -> None:
    assert logged_in.post("/api/pipeline-stages/seed").status_code == 200


def _seed_deal_and_user(logged_in: TestClient) -> tuple[str, str]:
    _seed_pipeline(logged_in)
    customer = logged_in.post("/api/customers", json={"legal_name": "ACME Srl"})
    assert customer.status_code == 201, customer.text
    deal = logged_in.post(
        "/api/deals", json={"name": "Progetto", "customer_id": customer.json()["id"]}
    )
    assert deal.status_code == 201, deal.text
    user = logged_in.post(
        "/api/users",
        json={
            "email": "worker@pigro.it",
            "password": "supersegreta1",
            "name": "Worker",
            "role": "collaborator",
        },
    )
    assert user.status_code == 201, user.text
    return deal.json()["id"], user.json()["id"]


def test_post_creates_and_returns_decimals_as_strings(logged_in: TestClient) -> None:
    """`Decimal` serialises to a JSON string, never a float -- which is exactly what
    lets the frontend read the digits instead of routing them through a binary float.
    Asserted on the raw text, because `response.json()` would already have parsed it."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    response = logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": user_id,
            "date": "2026-03-10",
            "hours": "3.50",
            "description": "Sviluppo",
            "applied_rate": "80.000000",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["hours"] == "3.50"
    assert body["applied_rate"] == "80.000000"
    assert body["line_value"] == "280.00"
    assert '"ore":"3.50"' in response.text.replace(" ", "")


def test_a_get_of_the_time_report_never_archives_a_document(logged_in: TestClient) -> None:
    """The PDF is rendered and archived by a POST (a GET must not create anything: a
    cross-site navigation sends the session cookie). The GET is the XLSX, whatever
    `format` a stale client still sends."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": user_id,
            "date": "2026-03-10",
            "hours": "2.00",
            "description": "Sviluppo",
            "applied_rate": "80.000000",
        },
    )

    for params in ({"month": "2026-03"}, {"month": "2026-03", "format": "pdf"}):
        response = logged_in.get(f"/api/deals/{deal_id}/time-report", params=params)
        assert response.status_code == 200, response.text
        assert "spreadsheetml" in response.headers["content-type"]
    assert logged_in.get("/api/documents", params={"deal_id": deal_id}).json()["items"] == []

    assert logged_in.get(f"/api/deals/{deal_id}/time-report/pdf").status_code == 405
    unknown = logged_in.post(
        "/api/deals/00000000-0000-0000-0000-000000000000/time-report/pdf",
        params={"month": "2026-03"},
    )
    assert unknown.status_code == 404


def test_a_closed_period_is_a_409_problem_document_naming_the_month(
    logged_in: TestClient,
) -> None:
    deal_id, user_id = _seed_deal_and_user(logged_in)
    assert logged_in.post("/api/period-locks", json={"year": 2026, "month": 3}).status_code == 201
    response = logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": user_id,
            "date": "2026-03-10",
            "hours": "1.00",
            "description": "x",
        },
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = response.json()
    assert problem["code"] == "conflict"
    assert problem["month"] == 3 and problem["year"] == 2026
    assert "marzo 2026" in problem["detail"]


def test_a_future_date_is_a_422_naming_the_field(logged_in: TestClient) -> None:
    deal_id, user_id = _seed_deal_and_user(logged_in)
    response = logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": user_id,
            "date": "2099-01-01",
            "hours": "1.00",
            "description": "x",
        },
    )
    assert response.status_code == 422
    assert response.json()["field"] == "date"


def test_over_range_hours_are_refused_before_the_database(logged_in: TestClient) -> None:
    """`ore = 80` is the comma slip that means 8,0. Bounded on the schema so it comes
    back as a clean 422 rather than reaching the CHECK as a raw IntegrityError."""
    deal_id, user_id = _seed_deal_and_user(logged_in)
    response = logged_in.post(
        "/api/time-entries",
        json={
            "deal_id": deal_id,
            "user_id": user_id,
            "date": "2026-03-10",
            "hours": "80.00",
            "description": "x",
        },
    )
    assert response.status_code == 422


def test_the_list_is_bounded_and_paginated(logged_in: TestClient) -> None:
    assert logged_in.get("/api/time-entries", params={"limit": 500}).status_code == 422
    ok = logged_in.get("/api/time-entries", params={"limit": 200})
    assert ok.status_code == 200
    assert set(ok.json()) == {"items", "next_cursor"}


def test_recalculate_requires_admin(logged_in: TestClient) -> None:
    deal_id, _ = _seed_deal_and_user(logged_in)
    collaborator = _second_actor(logged_in, "collaborator")
    response = collaborator.post(
        f"/api/deals/{deal_id}/rates/recalculate",
        json={"date_from": "2026-03-01", "date_to": "2026-03-31"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_the_openapi_document_describes_the_new_routes(logged_in: TestClient) -> None:
    """The frontend client is generated from this document, so a route missing here is
    a route the UI cannot call with types."""
    paths: dict[str, Any] = logged_in.get("/openapi.json").json()["paths"]
    for path in (
        "/api/time-entries",
        "/api/time-entries/{entry_id}",
        "/api/deals/{deal_id}/time-entries",
        "/api/deals/{deal_id}/time-summary",
        "/api/deals/{deal_id}/rates",
        "/api/costs",
        "/api/cost-categories",
        "/api/period-locks",
        "/api/users/{user_id}/rates",
    ):
        assert path in paths, path


def test_the_timer_round_trip_over_http(logged_in: TestClient) -> None:
    """Start, read, adjust, stop: the wire shape of the clock. `GET /timer` answers a
    200 with `null` when nothing runs -- the ordinary state of the page that asks -- and
    `stop` answers with the entry the timer became, so the client needs no second read."""
    deal_id, _ = _seed_deal_and_user(logged_in)

    empty = logged_in.get("/api/time-entries/timer")
    assert empty.status_code == 200
    assert empty.json() is None

    started = logged_in.post("/api/time-entries/timer/start", json={"description": "Call"})
    assert started.status_code == 201, started.text
    assert started.json()["deal_id"] is None

    again = logged_in.post("/api/time-entries/timer/start", json={})
    assert again.status_code == 409

    adjusted = logged_in.patch("/api/time-entries/timer", json={"deal_id": deal_id})
    assert adjusted.status_code == 200
    assert adjusted.json()["deal_id"] == deal_id
    assert logged_in.get("/api/time-entries/timer").json()["description"] == "Call"

    stopped = logged_in.post("/api/time-entries/timer/stop", json={})
    assert stopped.status_code == 201, stopped.text
    entry = stopped.json()
    assert entry["deal_id"] == deal_id
    assert entry["description"] == "Call"
    # Decimal-as-string, like every hour on this API; the smallest entry there is.
    assert entry["hours"] == "0.01"
    assert logged_in.get("/api/time-entries/timer").json() is None
    assert logged_in.get(f"/api/time-entries/{entry['id']}").status_code == 200


def test_stopping_without_a_deal_keeps_the_timer_and_says_what_is_missing(
    logged_in: TestClient,
) -> None:
    assert logged_in.post("/api/time-entries/timer/start", json={}).status_code == 201
    refused = logged_in.post("/api/time-entries/timer/stop", json={})
    assert refused.status_code == 422
    assert refused.json()["field"] == "deal_id"
    assert logged_in.get("/api/time-entries/timer").json() is not None
    assert logged_in.delete("/api/time-entries/timer").status_code == 204
    assert logged_in.get("/api/time-entries/timer").json() is None
    # Nothing to discard any more: a 404, and the problem document names the timer.
    assert logged_in.delete("/api/time-entries/timer").status_code == 404
