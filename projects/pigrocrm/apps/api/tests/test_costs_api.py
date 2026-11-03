"""The HTTP surface for costs, categories and period locks -- thin adapters over
`packages/core`. Business rules are proven there; here we assert statuses, problem
documents, and that the wire shape matches what the frontend generator expects.
"""

from fastapi.testclient import TestClient


def _second_actor(admin_client: TestClient, role: str) -> TestClient:
    """See the identical helper in `test_time_entries_api.py`/`test_invoices_api.py`:
    a genuinely independent session over the same ASGI app and database, needed
    because `logged_in` and a second role share one cookie jar when requested
    together."""
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


def _seed_category(logged_in: TestClient) -> str:
    response = logged_in.post("/api/cost-categories", json={"name": "Software e licenze"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_post_creates_and_returns_a_decimal_as_a_string(logged_in: TestClient) -> None:
    category_id = _seed_category(logged_in)
    response = logged_in.post(
        "/api/costs",
        json={
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "120.00",
            "description": "Licenza annuale",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == "120.00"
    assert '"amount":"120.00"' in response.text.replace(" ", "")


def test_a_negative_amount_is_accepted_and_zero_is_a_422(logged_in: TestClient) -> None:
    category_id = _seed_category(logged_in)
    refund = logged_in.post(
        "/api/costs",
        json={
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "-45.50",
            "description": "Nota di credito",
        },
    )
    assert refund.status_code == 201, refund.text
    assert refund.json()["amount"] == "-45.50"

    zero = logged_in.post(
        "/api/costs",
        json={
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "0.00",
            "description": "x",
        },
    )
    assert zero.status_code == 422
    assert zero.json()["field"] == "amount"


def test_soft_delete_then_restore_over_http(logged_in: TestClient) -> None:
    category_id = _seed_category(logged_in)
    created = logged_in.post(
        "/api/costs",
        json={
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "10.00",
            "description": "x",
        },
    )
    cost_id = created.json()["id"]
    assert logged_in.delete(f"/api/costs/{cost_id}").status_code == 204
    assert logged_in.get("/api/costs").json()["items"] == []
    restored = logged_in.post(f"/api/costs/{cost_id}/restore")
    assert restored.status_code == 200
    assert [c["id"] for c in logged_in.get("/api/costs").json()["items"]] == [cost_id]


def test_cost_category_lifecycle_over_http(logged_in: TestClient) -> None:
    created = logged_in.post("/api/cost-categories", json={"name": "Viaggi"})
    assert created.status_code == 201
    category_id = created.json()["id"]

    renamed = logged_in.patch(f"/api/cost-categories/{category_id}", json={"name": "Trasferte"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Trasferte"

    archived = logged_in.post(f"/api/cost-categories/{category_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["archived"] is True

    unarchived = logged_in.post(f"/api/cost-categories/{category_id}/unarchive")
    assert unarchived.status_code == 200
    assert unarchived.json()["archived"] is False


def test_only_an_admin_may_write_a_cost_category(logged_in: TestClient) -> None:
    collaborator = _second_actor(logged_in, "collaborator")
    response = collaborator.post("/api/cost-categories", json={"name": "Materiali"})
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_seeding_categories_is_idempotent_over_http(logged_in: TestClient) -> None:
    first = logged_in.post("/api/cost-categories/seed")
    assert first.status_code == 200
    assert len(first.json()) == 5

    second = logged_in.post("/api/cost-categories/seed")
    assert second.status_code == 200
    assert second.json() == []


def test_period_lock_close_and_reopen_over_http(logged_in: TestClient) -> None:
    closed = logged_in.post("/api/period-locks", json={"year": 2026, "month": 5})
    assert closed.status_code == 201
    body = closed.json()
    assert body["year"] == 2026
    assert body["month"] == 5
    assert body["closed_at"] is not None

    listed = logged_in.get("/api/period-locks", params={"year": 2026})
    assert listed.status_code == 200
    assert {(lock["year"], lock["month"]) for lock in listed.json()} == {(2026, 5)}

    assert logged_in.delete("/api/period-locks/2026/5").status_code == 204
    assert logged_in.get("/api/period-locks", params={"year": 2026}).json() == []


def test_only_an_admin_may_close_a_period(logged_in: TestClient) -> None:
    collaborator = _second_actor(logged_in, "collaborator")
    response = collaborator.post("/api/period-locks", json={"year": 2026, "month": 6})
    assert response.status_code == 403


def test_set_and_clear_user_rates_over_http(logged_in: TestClient, admin_user) -> None:
    set_response = logged_in.put(
        f"/api/users/{admin_user.id}/rates",
        json={"default_hourly_rate": "80.000000", "default_hourly_cost": "30.000000"},
    )
    assert set_response.status_code == 204

    updated = logged_in.get("/api/users").json()
    admin_row = next(u for u in updated if u["id"] == str(admin_user.id))
    assert admin_row["default_hourly_rate"] == "80.000000"
    assert admin_row["default_hourly_cost"] == "30.000000"

    # `exclude_unset`, not `exclude_none`: an omitted field on the PUT leaves the
    # existing value untouched (proven above), but a field sent as `null` clears it
    # back to NULL -- an unclearable rate would be a number nobody chose staying in
    # force forever.
    cleared = logged_in.put(f"/api/users/{admin_user.id}/rates", json={"default_hourly_rate": None})
    assert cleared.status_code == 204
    after_clear = next(
        u for u in logged_in.get("/api/users").json() if u["id"] == str(admin_user.id)
    )
    assert after_clear["default_hourly_rate"] is None
    assert after_clear["default_hourly_cost"] == "30.000000"


def test_a_collaborator_cannot_set_user_rates(logged_in: TestClient, admin_user) -> None:
    collaborator = _second_actor(logged_in, "collaborator")
    response = collaborator.put(
        f"/api/users/{admin_user.id}/rates", json={"default_hourly_rate": "80.000000"}
    )
    assert response.status_code == 403
