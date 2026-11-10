"""The HTTP surface for purchases, categories and period locks -- thin adapters over
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
        "/api/purchases",
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
        "/api/purchases",
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
        "/api/purchases",
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
        "/api/purchases",
        json={
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "10.00",
            "description": "x",
        },
    )
    purchase_id = created.json()["id"]
    assert logged_in.delete(f"/api/purchases/{purchase_id}").status_code == 204
    assert logged_in.get("/api/purchases").json()["items"] == []
    restored = logged_in.post(f"/api/purchases/{purchase_id}/restore")
    assert restored.status_code == 200
    assert [c["id"] for c in logged_in.get("/api/purchases").json()["items"]] == [purchase_id]


def _supplier(logged_in: TestClient) -> str:
    response = logged_in.post(
        "/api/organizations", json={"legal_name": "Vendor Srl", "roles": ["supplier"]}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_an_invoice_with_its_supplier_and_recap_is_registered_and_filtered(
    logged_in: TestClient,
) -> None:
    category_id = _seed_category(logged_in)
    supplier_id = _supplier(logged_in)
    created = logged_in.post(
        "/api/purchases",
        json={
            "kind": "invoice",
            "supplier_id": supplier_id,
            "number": "FA/1",
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "122.00",
            "description": "Consulenza",
            "vat_lines": [{"rate": "22.00", "taxable_amount": "100.00", "vat_amount": "22.00"}],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "invoice" and body["status"] == "registered"
    assert body["cost_amount"] == "122.00" and body["iva_period"] == "2026-03-05"
    assert body["vat_lines"][0]["deductible_share"] == "100.00"
    listed = logged_in.get("/api/purchases", params={"supplier_id": supplier_id, "kind": "invoice"})
    assert [item["id"] for item in listed.json()["items"]] == [body["id"]]

    mismatch = logged_in.post(
        "/api/purchases",
        json={
            "kind": "invoice",
            "supplier_id": supplier_id,
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "130.00",
            "description": "x",
            "vat_lines": [{"rate": "22.00", "taxable_amount": "100.00", "vat_amount": "22.00"}],
        },
    )
    assert mismatch.status_code == 422 and mismatch.json()["field"] == "amount"


def test_a_purchase_is_cancelled_over_http_and_leaves_the_registered_list(
    logged_in: TestClient,
) -> None:
    category_id = _seed_category(logged_in)
    created = logged_in.post(
        "/api/purchases",
        json={
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "10.00",
            "description": "x",
        },
    )
    purchase_id = created.json()["id"]
    cancelled = logged_in.post(f"/api/purchases/{purchase_id}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert logged_in.post(f"/api/purchases/{purchase_id}/cancel").status_code == 409
    assert logged_in.get("/api/purchases", params={"status": "registered"}).json()["items"] == []
    assert (
        logged_in.patch(f"/api/purchases/{purchase_id}", json={"description": "y"}).status_code
        == 409
    )


def test_the_list_says_how_many_match_and_their_total_whatever_the_page(
    logged_in: TestClient,
) -> None:
    category_id = _seed_category(logged_in)
    for day, amount in (
        ("2026-03-01", "100.00"),
        ("2026-03-02", "50.50"),
        ("2026-04-01", "-20.25"),
    ):
        created = logged_in.post(
            "/api/purchases",
            json={
                "category_id": category_id,
                "date": day,
                "amount": amount,
                "description": "x",
            },
        )
        assert created.status_code == 201, created.text

    everything = logged_in.get("/api/purchases", params={"limit": 1}).json()
    assert len(everything["items"]) == 1
    assert everything["total_count"] == 3
    assert everything["total_amount"] == "130.25"

    march = logged_in.get(
        "/api/purchases", params={"from": "2026-03-01", "to": "2026-03-31"}
    ).json()
    assert march["total_count"] == 2 and march["total_amount"] == "150.50"

    none = logged_in.get("/api/purchases", params={"from": "2027-01-01"}).json()
    assert none["items"] == [] and none["total_count"] == 0 and none["total_amount"] == "0.00"


def test_a_purchase_has_a_timeline_and_an_unknown_one_is_a_404(logged_in: TestClient) -> None:
    category_id = _seed_category(logged_in)
    created = logged_in.post(
        "/api/purchases",
        json={
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "10.00",
            "description": "x",
        },
    )
    purchase_id = created.json()["id"]
    timeline = logged_in.get(f"/api/purchases/{purchase_id}/timeline")
    assert timeline.status_code == 200, timeline.text
    assert [entry["kind"] for entry in timeline.json()] == ["created"]
    missing = logged_in.get("/api/purchases/00000000-0000-4000-8000-000000000000/timeline")
    assert missing.status_code == 404


def test_no_cost_route_answers(logged_in: TestClient) -> None:
    assert logged_in.get("/api/costs").status_code == 404
    assert logged_in.post("/api/costs", json={}).status_code in (404, 405)


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


def test_a_purchase_with_a_duplicate_photo_is_linked_to_the_original_and_its_file(
    logged_in: TestClient,
) -> None:
    photo = b"\xff\xd8\xff\xe0" + b"\x01" * 32
    category_id = _seed_category(logged_in)
    ids = []
    for name in ("uno.jpg", "due.jpg"):
        response = logged_in.post(
            "/api/inbox/upload?read=false", files=[("file", (name, photo, "image/jpeg"))]
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["items"][0])
    first, second = ids
    assert second["state"] == "duplicate"

    created = logged_in.post(
        "/api/purchases",
        json={
            "category_id": category_id,
            "date": "2026-03-05",
            "amount": "12.50",
            "description": "Bar Roma",
            "inbox_item_id": second["id"],
        },
    )
    assert created.status_code == 201, created.text
    read = logged_in.get(f"/api/purchases/{created.json()['id']}")
    assert read.json()["inbox_item_id"] == first["id"]
    file = logged_in.get(f"/api/inbox/items/{read.json()['inbox_item_id']}/file")
    assert file.status_code == 200 and file.content == photo
    assert logged_in.get(f"/api/inbox/items/{second['id']}").json()["state"] == "duplicate"
