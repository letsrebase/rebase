from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _seed_pipeline(logged_in: TestClient) -> None:
    assert logged_in.post("/api/pipeline-stages/seed").status_code == 200


def test_full_customer_lifecycle_over_http(logged_in: TestClient) -> None:
    created = logged_in.post("/api/customers", json={"legal_name": "ACME Srl"})
    assert created.status_code == 201
    customer_id = created.json()["id"]

    assert logged_in.get(f"/api/customers/{customer_id}").json()["legal_name"] == "ACME Srl"

    updated = logged_in.patch(
        f"/api/customers/{customer_id}",
        json={"contact_points": [{"kind": "phone", "value": "0212345"}]},
    )
    assert updated.json()["phone"] == "0212345"

    assert logged_in.delete(f"/api/customers/{customer_id}").status_code == 204
    assert logged_in.get(f"/api/customers/{customer_id}").status_code == 404


def test_missing_customer_returns_a_problem_document(logged_in: TestClient) -> None:
    from uuid import uuid4

    response = logged_in.get(f"/api/customers/{uuid4()}")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "not_found"


def test_a_custom_field_defined_over_http_is_immediately_usable(logged_in: TestClient) -> None:
    """This is the whole point of dynamic fields: define it, then use it, with no deploy."""
    definition = logged_in.post(
        "/api/field-definitions",
        json={
            "entity_type": "customer",
            "key": "settore",
            "label": "Settore",
            "field_type": "select",
            "options": ["IT", "Retail"],
        },
    )
    assert definition.status_code == 201

    ok = logged_in.post(
        "/api/customers", json={"legal_name": "Beta", "custom_fields": {"settore": "IT"}}
    )
    assert ok.status_code == 201
    assert ok.json()["custom_fields"] == {"settore": "IT"}

    bad = logged_in.post(
        "/api/customers", json={"legal_name": "Gamma", "custom_fields": {"settore": "Altro"}}
    )
    assert bad.status_code == 422
    assert "IT" in bad.json()["expected"]


def test_schema_endpoint_describes_the_current_shape(logged_in: TestClient) -> None:
    logged_in.post(
        "/api/field-definitions",
        json={
            "entity_type": "customer",
            "key": "settore",
            "label": "Settore",
            "field_type": "text",
        },
    )
    body = logged_in.get("/api/schema/customer").json()

    assert body["entity_type"] == "customer"
    assert any(f["key"] == "settore" for f in body["custom_fields"])
    assert "legal_name" in body["native_fields"]


def test_schema_endpoint_matches_describe_entity_exactly(
    logged_in: TestClient, api_session: Session
) -> None:
    """`routers/schema.py` returns `EntitySchema(**describe_entity(...))` -- a
    fixed Pydantic model with named fields, unlike the MCP `describe_schema` tool
    (Task 16), which returns `describe_entity`'s dict verbatim with no model in
    between. If `describe_entity` ever grew a key `EntitySchema` doesn't declare,
    Pydantic's default `extra="ignore"` would drop it here silently -- no error,
    no failing test, just this endpoint quietly falling behind what MCP reports
    for the exact same entity. The two surfaces are identical today (verified
    below, not assumed), and this guards that they stay that way by comparing
    them directly on the same database, not by trusting they agree because they
    call the same function.

    Lives here rather than in `apps/mcp/tests` because `apps/mcp` may not import
    `pigrocrm_api` (enforced by `TID251`; confirmed by attempting exactly that
    import from a file under `apps/mcp/tests` and getting the ban error) and this
    project has no reverse constraint stopping `apps/api/tests` from importing
    `pigrocrm.core` directly.
    """
    from pigrocrm.core.schema_registry import describe_entity

    logged_in.post(
        "/api/field-definitions",
        json={
            "entity_type": "customer",
            "key": "settore",
            "label": "Settore",
            "field_type": "text",
        },
    )

    direct = describe_entity(api_session, "customer")
    over_http = logged_in.get("/api/schema/customer").json()

    assert over_http == direct


def test_deal_lifecycle_including_the_kanban_move(logged_in: TestClient) -> None:
    _seed_pipeline(logged_in)
    stages = logged_in.get("/api/pipeline-stages").json()
    offer = next(s for s in stages if s["name"] == "Offerta")

    customer_id = logged_in.post("/api/customers", json={"legal_name": "ACME"}).json()["id"]
    deal = logged_in.post(
        "/api/deals", json={"name": "Progetto X", "customer_id": customer_id}
    ).json()

    moved = logged_in.patch(f"/api/deals/{deal['id']}/stage", json={"stage_id": offer["id"]})
    assert moved.status_code == 200
    assert moved.json()["pipeline_stage_id"] == offer["id"]


def test_a_deal_carries_the_name_of_its_customer(logged_in: TestClient) -> None:
    """Over HTTP, on both read shapes: the deal list prints the customer under the deal's
    own name, and the detail page reads the single deal. A `customer_id` alone would make
    the list fetch one customer per row just to print a name -- the same gap
    `customer_legal_name` closed for Persone."""
    _seed_pipeline(logged_in)
    customer_id = logged_in.post("/api/customers", json={"legal_name": "ACME Srl"}).json()["id"]
    created = logged_in.post(
        "/api/deals", json={"name": "Progetto X", "customer_id": customer_id}
    ).json()
    assert created["customer_legal_name"] == "ACME Srl"

    deal_id = created["id"]
    assert logged_in.get(f"/api/deals/{deal_id}").json()["customer_legal_name"] == "ACME Srl"

    page = logged_in.get("/api/deals", params={"customer_id": customer_id}).json()
    assert [d["customer_legal_name"] for d in page["items"]] == ["ACME Srl"]


def test_timeline_endpoint_reports_what_happened(logged_in: TestClient) -> None:
    customer_id = logged_in.post("/api/customers", json={"legal_name": "ACME"}).json()["id"]
    logged_in.patch(
        f"/api/customers/{customer_id}",
        json={"contact_points": [{"kind": "phone", "value": "021234"}]},
    )

    timeline = logged_in.get(f"/api/customers/{customer_id}/timeline").json()
    assert {entry["kind"] for entry in timeline} == {"created", "updated"}
    assert all(entry["actor_type"] == "user" for entry in timeline)


def test_timeline_endpoint_respects_a_bounded_limit(logged_in: TestClient) -> None:
    customer_id = logged_in.post("/api/customers", json={"legal_name": "ACME"}).json()["id"]
    for index in range(5):
        logged_in.patch(
            f"/api/customers/{customer_id}",
            json={"contact_points": [{"kind": "phone", "value": f"02123{index}"}]},
        )

    limited = logged_in.get(f"/api/customers/{customer_id}/timeline", params={"limit": 2})
    assert len(limited.json()) == 2

    too_big = logged_in.get(f"/api/customers/{customer_id}/timeline", params={"limit": 201})
    assert too_big.status_code == 422


def test_deleting_a_customer_with_deals_is_409_and_says_how_many(logged_in: TestClient) -> None:
    _seed_pipeline(logged_in)
    customer_id = logged_in.post("/api/customers", json={"legal_name": "ACME"}).json()["id"]
    logged_in.post("/api/deals", json={"name": "X", "customer_id": customer_id})

    response = logged_in.delete(f"/api/customers/{customer_id}")
    assert response.status_code == 409
    assert response.json()["active_deals"] == 1


def test_list_supports_search_and_pagination(logged_in: TestClient) -> None:
    for index in range(3):
        logged_in.post("/api/customers", json={"legal_name": f"Cliente {index}"})

    page = logged_in.get("/api/customers", params={"limit": 2}).json()
    assert len(page["items"]) == 2
    assert page["next_cursor"] is not None

    found = logged_in.get("/api/customers", params={"search": "Cliente 1"}).json()
    assert len(found["items"]) == 1


def test_a_pat_is_shown_once_and_then_only_by_prefix(logged_in: TestClient) -> None:
    created = logged_in.post("/api/tokens", json={"name": "Claude"}).json()
    assert created["token"].startswith("pgc_")

    listed = logged_in.get("/api/tokens").json()
    assert listed[0]["prefix"] == created["token"][:12]
    assert all("token" not in entry for entry in listed)


def test_a_person_can_be_created_without_a_customer(logged_in: TestClient) -> None:
    response = logged_in.post("/api/people", json={"first_name": "Mario"})
    assert response.status_code == 201
    assert response.json()["customer_id"] is None
    assert response.json()["customer_legal_name"] is None


def test_a_person_carries_the_name_of_the_company_they_belong_to(logged_in: TestClient) -> None:
    """Over HTTP, on both read shapes: the Persone screen renders the list, the detail
    page reads the single person, and a `customer_id` alone would make the list fetch
    one customer per row just to print a name."""
    customer_id = logged_in.post("/api/customers", json={"legal_name": "ACME Srl"}).json()["id"]
    person_id = logged_in.post(
        "/api/people", json={"first_name": "Mario", "customer_id": customer_id}
    ).json()["id"]

    assert logged_in.get(f"/api/people/{person_id}").json()["customer_legal_name"] == "ACME Srl"

    page = logged_in.get("/api/people", params={"customer_id": customer_id}).json()
    assert [p["customer_legal_name"] for p in page["items"]] == ["ACME Srl"]


# --- Final review item 10 (MINOR): *ListQuery.custom was implemented, GIN-indexed --
# --- and service-tested, but no router and no MCP tool ever passed it. -----------


def test_list_customers_filters_by_a_repeated_custom_query_param(logged_in: TestClient) -> None:
    logged_in.post(
        "/api/field-definitions",
        json={
            "entity_type": "customer",
            "key": "settore",
            "label": "Settore",
            "field_type": "text",
        },
    )
    logged_in.post("/api/customers", json={"legal_name": "A", "custom_fields": {"settore": "IT"}})
    logged_in.post(
        "/api/customers", json={"legal_name": "B", "custom_fields": {"settore": "Retail"}}
    )

    page = logged_in.get("/api/customers", params={"custom": "settore:IT"}).json()
    assert [c["legal_name"] for c in page["items"]] == ["A"]


def test_list_customers_custom_filter_repeated_twice_is_an_and(logged_in: TestClient) -> None:
    """Mirrors JSONB containment semantics (`@>`): every key given must match."""
    for key in ("settore", "priorita"):
        logged_in.post(
            "/api/field-definitions",
            json={"entity_type": "customer", "key": key, "label": key, "field_type": "text"},
        )
    logged_in.post(
        "/api/customers",
        json={"legal_name": "A", "custom_fields": {"settore": "IT", "priorita": "alta"}},
    )
    logged_in.post(
        "/api/customers",
        json={"legal_name": "B", "custom_fields": {"settore": "IT", "priorita": "bassa"}},
    )

    page = logged_in.get(
        "/api/customers", params=[("custom", "settore:IT"), ("custom", "priorita:alta")]
    ).json()
    assert [c["legal_name"] for c in page["items"]] == ["A"]


def test_list_customers_custom_filter_without_a_colon_is_benign_not_a_500(
    logged_in: TestClient,
) -> None:
    """A malformed `custom` entry must not crash the request -- there is no
    obviously "correct" interpretation of a value with no `key:value` separator,
    so it degrades to an empty-string value instead of raising."""
    response = logged_in.get("/api/customers", params={"custom": "not-key-value"})
    assert response.status_code == 200


def test_list_people_filters_by_a_custom_query_param(logged_in: TestClient) -> None:
    logged_in.post(
        "/api/field-definitions",
        json={
            "entity_type": "person",
            "key": "seniority",
            "label": "Seniority",
            "field_type": "text",
        },
    )
    logged_in.post(
        "/api/people", json={"first_name": "A", "custom_fields": {"seniority": "senior"}}
    )
    logged_in.post(
        "/api/people", json={"first_name": "B", "custom_fields": {"seniority": "junior"}}
    )

    page = logged_in.get("/api/people", params={"custom": "seniority:senior"}).json()
    assert [p["first_name"] for p in page["items"]] == ["A"]


def test_list_deals_filters_by_a_custom_query_param(logged_in: TestClient) -> None:
    _seed_pipeline(logged_in)
    logged_in.post(
        "/api/field-definitions",
        json={"entity_type": "deal", "key": "source", "label": "Fonte", "field_type": "text"},
    )
    customer_id = logged_in.post("/api/customers", json={"legal_name": "ACME"}).json()["id"]
    created_a = logged_in.post(
        "/api/deals",
        json={"name": "A", "customer_id": customer_id, "custom_fields": {"source": "referral"}},
    )
    assert created_a.status_code == 201, created_a.text
    created_b = logged_in.post(
        "/api/deals",
        json={"name": "B", "customer_id": customer_id, "custom_fields": {"source": "outbound"}},
    )
    assert created_b.status_code == 201, created_b.text

    page = logged_in.get("/api/deals", params={"custom": "source:referral"}).json()
    assert [d["name"] for d in page["items"]] == ["A"]


# --- contact points (REB-941) ------------------------------------------------------------


def test_a_person_with_two_emails_and_a_labelled_phone_is_read_back_and_found_by_either(
    logged_in: TestClient,
) -> None:
    created = logged_in.post(
        "/api/people",
        json={
            "first_name": "Ada",
            "last_name": "Byron",
            "contact_points": [
                {"kind": "email", "value": "ada@acme.it", "label": "work"},
                {"kind": "email", "value": "Ada.Private@gmail.com", "label": "personal"},
                {
                    "kind": "phone",
                    "value": "+39 02 1234567",
                    "label": "billing",
                    "note": "centralino",
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["email"] == "ada@acme.it"  # the first of its kind is the primary
    assert body["phone"] == "+39 02 1234567"
    assert body["linkedin"] is None
    assert [
        (p["kind"], p["value"], p["label"], p["note"], p["is_primary"], p["position"])
        for p in body["contact_points"]
    ] == [
        ("email", "ada@acme.it", "work", None, True, 0),
        ("email", "Ada.Private@gmail.com", "personal", None, False, 1),
        ("phone", "+39 02 1234567", "billing", "centralino", True, 2),
    ]
    assert all(p["id"] for p in body["contact_points"])

    read = logged_in.get(f"/api/people/{body['id']}").json()
    assert read["email"] == "ada@acme.it"
    for term in ("ada.private@gmail", "ada@acme.it", "0212345"):
        found = logged_in.get("/api/people", params={"search": term}).json()["items"]
        assert [p["id"] for p in found] == [body["id"]], term


def test_the_list_replaces_and_a_flagged_point_becomes_the_primary(logged_in: TestClient) -> None:
    person = logged_in.post(
        "/api/people",
        json={"first_name": "Ada", "contact_points": [{"kind": "email", "value": "a@acme.it"}]},
    ).json()

    updated = logged_in.patch(
        f"/api/people/{person['id']}",
        json={
            "contact_points": [
                {"kind": "email", "value": "a@acme.it"},
                {"kind": "email", "value": "b@acme.it", "is_primary": True},
            ]
        },
    ).json()

    assert updated["email"] == "b@acme.it"
    assert [p["value"] for p in updated["contact_points"]] == ["a@acme.it", "b@acme.it"]
    cleared = logged_in.patch(f"/api/people/{person['id']}", json={"contact_points": []}).json()
    assert cleared["contact_points"] == [] and cleared["email"] is None


def test_the_old_flat_fields_are_refused_on_write(logged_in: TestClient) -> None:
    person = logged_in.post("/api/people", json={"first_name": "Ada"}).json()
    customer = logged_in.post("/api/customers", json={"legal_name": "ACME"}).json()

    assert (
        logged_in.post("/api/people", json={"first_name": "X", "email": "x@x.it"}).status_code
        == 422
    )
    assert (
        logged_in.patch(f"/api/people/{person['id']}", json={"phone": "0212345"}).status_code == 422
    )
    assert (
        logged_in.post("/api/customers", json={"legal_name": "X", "email": "x@x.it"}).status_code
        == 422
    )
    assert (
        logged_in.patch(f"/api/customers/{customer['id']}", json={"email": "x@x.it"}).status_code
        == 422
    )


def test_a_refused_list_is_a_422_that_names_the_point(logged_in: TestClient) -> None:
    for points, field in (
        ([{"kind": "email", "value": "not-an-email"}], "contact_points[0]"),
        (
            [{"kind": "email", "value": "a@x.it"}, {"kind": "email", "value": "A@x.it"}],
            "contact_points[1]",
        ),
        (
            [
                {"kind": "phone", "value": "0212345", "is_primary": True},
                {"kind": "phone", "value": "0212346", "is_primary": True},
            ],
            "contact_points[1]",
        ),
    ):
        response = logged_in.post(
            "/api/people", json={"first_name": "Ada", "contact_points": points}
        )
        assert response.status_code == 422, response.text
        assert response.json()["field"] == field


def test_a_customer_takes_contact_points_and_is_found_by_any(logged_in: TestClient) -> None:
    created = logged_in.post(
        "/api/customers",
        json={
            "legal_name": "ACME Srl",
            "certified_email": "acme@pec.it",
            "contact_points": [
                {"kind": "email", "value": "info@acme.it", "label": "billing"},
                {"kind": "phone", "value": "+39 02 7654321"},
                {"kind": "email", "value": "ordini@acme.it"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["email"], body["phone"], body["certified_email"]) == (
        "info@acme.it",
        "+39 02 7654321",
        "acme@pec.it",
    )
    for term in ("ordini@acme", "76543"):
        found = logged_in.get("/api/customers", params={"search": term}).json()["items"]
        assert [c["id"] for c in found] == [body["id"]], term
