from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pigrocrm.core.errors import Conflict, DomainError
from pigrocrm_api.errors import domain_error_handler
from pigrocrm_api.main import create_app


def _throwaway_app() -> FastAPI:
    """A minimal app, not the real one: nothing in the real auth router raises a
    domain error carrying a UUID in its details yet, so this is the only way to
    exercise the rendering bug without waiting for a later task's entity routers."""
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/boom")
    def boom() -> None:
        raise Conflict("pipeline_stage", "ha ancora dei deal collegati", stage_id=uuid4())

    @app.get("/shadow")
    def shadow() -> None:
        raise Conflict(
            "pipeline_stage",
            "ha ancora dei deal collegati",
            code="lead",
            type="x",
            title="x",
            status=1,
            detail="x",
            instance="x",
            extra="kept",
        )

    return app


def test_a_uuid_in_domain_error_details_still_renders_a_clean_problem_document() -> None:
    """`DomainError`'s **details is typed Any -- nothing stops a caller from putting a
    UUID in it, and json.dumps (what JSONResponse uses to render its body) cannot
    serialise one. Before jsonable_encoder, this raised inside domain_error_handler
    itself while building the response, turning what should be a clean 409 into an
    opaque, unhandled 500."""
    with TestClient(_throwaway_app()) as client:
        response = client.get("/boom")

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "conflict"
    # jsonable_encoder turns the UUID into its string form -- json.dumps could not
    # have produced this body at all otherwise.
    assert isinstance(body["stage_id"], str)
    assert len(body["stage_id"]) == 36


def test_the_openapi_document_declares_both_shapes_a_422_can_actually_be() -> None:
    """`PROBLEM_RESPONSES` (errors.py) declares a 422 on every router, which
    suppresses FastAPI's own automatic `HTTPValidationError` documentation for
    that status -- a real regression caught in review by regenerating a
    TypeScript client with `openapi-typescript` and finding the array-of-errors
    shape (`{"loc": [...], "msg": ..., "type": ...}`) nowhere in the generated
    422 type, even though the server still returns exactly that shape whenever
    FastAPI's own request parsing rejects something before an endpoint ever
    runs (e.g. a non-UUID path segment). slice 1B's planned `fieldErrorFrom`
    reads `HTTPValidationError.detail[].loc` to find the offending form field;
    losing this from the document breaks that silently, with nothing visibly
    wrong anywhere. Uses the real `create_app()`, not a throwaway one: the
    whole point is to check what every router actually publishes. No database
    fixture needed -- building the app and generating its schema never opens a
    session; `SessionDep` is only resolved per-request, and this test never
    makes one.
    """
    app = create_app()
    schema = app.openapi()

    schemas = schema["components"]["schemas"]
    assert "HTTPValidationError" in schemas, "the $ref below would point at nothing"
    assert "ValidationError" in schemas, "HTTPValidationError.detail[] itself $refs this"

    op = schema["paths"]["/api/customers/{customer_id}"]["get"]
    content = op["responses"]["422"]["content"]
    assert set(content) == {"application/problem+json", "application/json"}
    assert content["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HTTPValidationError"
    }


def test_details_never_overwrite_the_fixed_keys_of_the_problem_document() -> None:
    """The details are spread into the document, so a detail that shares a name with one
    of the fixed keys must not win: clients branch on `code`."""
    with TestClient(_throwaway_app()) as client:
        body = client.get("/shadow").json()

    assert body["code"] == "conflict"
    assert body["status"] == 409
    assert body["type"] == "https://pigrocrm.dev/errors/conflict"
    assert body["title"] == "Conflitto con lo stato attuale"
    assert body["detail"].endswith("ha ancora dei deal collegati")
    assert body["instance"] == "/shadow"
    assert body["extra"] == "kept"


def test_a_duplicate_stage_code_answers_the_conflict_code_and_names_the_stage_code(
    logged_in: TestClient,
) -> None:
    """The duplicate-stage conflict used to carry the stage's own code as `code` and
    overwrite the problem's: a client branching on `code == "conflict"` saw `"lead"`."""
    stage = {"nome": "Prima", "position": 90, "code": "stage-dup-test"}
    assert logged_in.post("/api/pipeline-stages", json=stage).status_code == 201
    response = logged_in.post(
        "/api/pipeline-stages", json={**stage, "nome": "Seconda", "position": 91}
    )

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "conflict"
    assert body["stage_code"] == "stage-dup-test"
