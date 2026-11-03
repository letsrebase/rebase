"""Final review, group 1 (items 1, 2, 4 of the fix wave): the input-bounding class,
swept across every entity's create endpoint over real HTTP -- not re-verified as a
list of the specific fields a reviewer happened to try.

Six times before this fix wave, this project closed "an unvalidated input reaches
Postgres and comes back as a raw exception" as six separate, column-shape-specific
fixes. This module is the regression net for the three shapes that were never
swept: a NUL byte in a native string/text column (item 1), a plain `Integer` column
with no bound at all (item 2), and a `String`-backed schema field with a column
width but no matching Pydantic bound (item 4). Every case here must come back as a
clean 422 (a domain `ValidationFailed` rendered as `application/problem+json`, or an
ordinary Pydantic `ValidationError` rendered by FastAPI's own request parsing as
`application/json`) -- never a 500, which is what every one of these looked like
before `pigrocrm.core.validation.SafeStr` and the `Field(ge=..., le=...)` bounds in
`fields/schemas.py` and `pipeline/schemas.py` existed.

The field lists below are maintained by hand, not derived from the schemas via
introspection: `SafeStr`'s `Annotated` wrapping makes `model_fields[...].annotation`
an unreliable signal for "is this a plain string field" (confirmed while writing
this module -- an introspection-based version silently matched only 2 of 15
`CustomerCreate` fields). A hand-maintained list next to a schema that itself
documents every field is the same trade-off this project already made deliberately
for customers/people/deals duplicating each other's shape (see progress.md's ruling)
rather than a gap.
"""

from fastapi.testclient import TestClient

NUL = "\x00"


def _seed_customer(logged_in: TestClient) -> str:
    created = logged_in.post("/api/customers", json={"legal_name": "ACME"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


# --- Item 1 (CRITICAL): a NUL byte in any native string/text field ---------------

# Every plain `str`/`str | None` field on CustomerCreate (customers/schemas.py).
CUSTOMER_STRING_FIELDS = [
    "legal_name",
    "vat_number",
    "tax_code",
    "sdi_code",
    "certified_email",
    "address",
    "cap",
    "city",
    "province",
    "country",
    "email",
    "phone",
    "website",
    "status",
    "note",
]


def test_a_nul_byte_in_any_customer_field_is_rejected_not_500(logged_in: TestClient) -> None:
    for field in CUSTOMER_STRING_FIELDS:
        body = {"legal_name": "ACME", field: NUL}
        response = logged_in.post("/api/customers", json=body)
        assert response.status_code == 422, f"{field}: {response.status_code} {response.text}"


# Every plain `str`/`str | None` field on PersonCreate (people/schemas.py).
PERSON_STRING_FIELDS = ["first_name", "last_name", "email", "phone", "role", "linkedin", "note"]


def test_a_nul_byte_in_any_person_field_is_rejected_not_500(logged_in: TestClient) -> None:
    for field in PERSON_STRING_FIELDS:
        body = {"first_name": "Mario", field: NUL}
        response = logged_in.post("/api/people", json=body)
        assert response.status_code == 422, f"{field}: {response.status_code} {response.text}"


# Every plain `str`/`str | None` field on DealCreate (deals/schemas.py). `name` is
# required, `customer_id`/`pipeline_stage_id`/`owner_id` are UUID, `probability` is
# int, `expected_close_date` is a date -- none of those are in scope for SafeStr.
DEAL_STRING_FIELDS = ["name", "note"]


def test_a_nul_byte_in_any_deal_field_is_rejected_not_500(logged_in: TestClient) -> None:
    customer_id = _seed_customer(logged_in)
    for field in DEAL_STRING_FIELDS:
        body = {"name": "Progetto", "customer_id": customer_id, field: NUL}
        response = logged_in.post("/api/deals", json=body)
        assert response.status_code == 422, f"{field}: {response.status_code} {response.text}"


def test_a_nul_byte_in_a_field_definitions_label_is_rejected_not_500(logged_in: TestClient) -> None:
    body = {"entity_type": "customer", "key": "x", "label": NUL, "field_type": "text"}
    response = logged_in.post("/api/field-definitions", json=body)
    assert response.status_code == 422, response.text


def test_a_nul_byte_inside_a_field_definitions_option_is_rejected_not_500(
    logged_in: TestClient,
) -> None:
    """`options` is a list, not a scalar string -- the fix has to reach into it."""
    body = {
        "entity_type": "customer",
        "key": "settore",
        "label": "Settore",
        "field_type": "select",
        "options": ["IT", f"Retail{NUL}"],
    }
    response = logged_in.post("/api/field-definitions", json=body)
    assert response.status_code == 422, response.text


def test_a_nul_byte_in_a_new_users_name_is_rejected_not_500(logged_in: TestClient) -> None:
    body = {"email": "new@pigro.it", "password": "supersegreta1", "name": NUL}
    response = logged_in.post("/api/users", json=body)
    assert response.status_code == 422, response.text


def test_a_nul_byte_in_a_tokens_name_is_rejected_not_500(logged_in: TestClient) -> None:
    response = logged_in.post("/api/tokens", json={"name": NUL})
    assert response.status_code == 422, response.text


# --- Item 2 (CRITICAL): plain Integer columns were unbounded ---------------------


def test_pipeline_stage_position_far_beyond_the_bound_is_422_not_500(
    logged_in: TestClient,
) -> None:
    """Before Field(ge=..., le=...) existed on PipelineStageCreate.posizione, this
    reached Postgres raw as `psycopg.errors.NumericValueOutOfRange` (`integer out of
    range`) -- an `Integer` column can hold at most ~2.1 billion, and 2**40 is
    roughly 500 times that."""
    response = logged_in.post("/api/pipeline-stages", json={"name": "X", "position": 2**40})
    assert response.status_code == 422, response.text


def test_field_definition_position_far_beyond_the_bound_is_422_not_500(
    logged_in: TestClient,
) -> None:
    body = {
        "entity_type": "customer",
        "key": "x",
        "label": "X",
        "field_type": "text",
        "position": 2**40,
    }
    response = logged_in.post("/api/field-definitions", json=body)
    assert response.status_code == 422, response.text


# --- Item 4 (CRITICAL): UserCreate.name had no max_length -------------------------


def test_a_users_name_over_the_column_width_is_422_not_500(logged_in: TestClient) -> None:
    """Mirrors `users.name`'s column width (String(200), auth/models.py). Before
    this fix, an over-length value reached `flush()` as a raw, session-poisoning
    `sqlalchemy.exc.DataError` (`StringDataRightTruncation`)."""
    body = {"email": "long@pigro.it", "password": "supersegreta1", "name": "x" * 201}
    response = logged_in.post("/api/users", json=body)
    assert response.status_code == 422, response.text


# --- Coordinator follow-up on item 1: the list endpoints' own `search`/`status`/ --
# --- `custom` query parameters were left unguarded, and `custom` is a surface  --
# --- this very fix wave introduced (item 10). Unlike a Create/Update body, a   --
# --- *ListQuery is hand-built inside the route function from already-parsed   --
# --- FastAPI parameters, so putting SafeStr only on *ListQuery's own fields    --
# --- does not help: a ValidationError raised there is not converted to a 422  --
# --- by FastAPI's automatic request-validation handling the way a request     --
# --- body's is. The fix instead annotates the *query parameter itself* as     --
# --- SafeStr, so FastAPI's own parameter validation catches it -- the same    --
# --- mechanism that already bounds `limit` on these same routes.              --


def test_a_nul_byte_in_customer_search_is_422_not_500(logged_in: TestClient) -> None:
    response = logged_in.get("/api/customers", params={"search": NUL})
    assert response.status_code == 422, response.text


def test_to_nul_byte_in_customer_status_is_422_not_500(logged_in: TestClient) -> None:
    response = logged_in.get("/api/customers", params={"status": NUL})
    assert response.status_code == 422, response.text


def test_a_nul_byte_in_customer_custom_filter_is_422_not_500(logged_in: TestClient) -> None:
    response = logged_in.get("/api/customers", params={"custom": f"settore:{NUL}"})
    assert response.status_code == 422, response.text


def test_a_nul_byte_in_person_search_is_422_not_500(logged_in: TestClient) -> None:
    response = logged_in.get("/api/people", params={"search": NUL})
    assert response.status_code == 422, response.text


def test_a_nul_byte_in_person_custom_filter_is_422_not_500(logged_in: TestClient) -> None:
    response = logged_in.get("/api/people", params={"custom": f"seniority:{NUL}"})
    assert response.status_code == 422, response.text


def test_a_nul_byte_in_deal_search_is_422_not_500(logged_in: TestClient) -> None:
    response = logged_in.get("/api/deals", params={"search": NUL})
    assert response.status_code == 422, response.text


def test_a_nul_byte_in_deal_custom_filter_is_422_not_500(logged_in: TestClient) -> None:
    response = logged_in.get("/api/deals", params={"custom": f"fonte:{NUL}"})
    assert response.status_code == 422, response.text


def test_ordinary_search_status_and_custom_values_still_work_on_customers(
    logged_in: TestClient,
) -> None:
    """Regression guard: the SafeStr annotation on the query parameter must not
    reject or otherwise disturb an ordinary, valid request."""
    logged_in.post(
        "/api/field-definitions",
        json={
            "entity_type": "customer",
            "key": "settore",
            "label": "Settore",
            "field_type": "text",
        },
    )
    created = logged_in.post(
        "/api/customers",
        json={
            "legal_name": "ACME Srl",
            "status": "active",
            "custom_fields": {"settore": "IT"},
        },
    )
    assert created.status_code == 201, created.text

    response = logged_in.get(
        "/api/customers",
        params={"search": "ACME", "status": "active", "custom": "settore:IT"},
    )
    assert response.status_code == 200, response.text
    assert [c["legal_name"] for c in response.json()["items"]] == ["ACME Srl"]
