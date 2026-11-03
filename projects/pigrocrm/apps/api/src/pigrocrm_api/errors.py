from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.constants import REF_PREFIX
from fastapi.openapi.utils import validation_error_definition, validation_error_response_definition
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import DBAPIError

from pigrocrm.core.db.scope import is_policy_refusal
from pigrocrm.core.errors import DomainError, OutOfScope
from pigrocrm.core.validation_messages import validation_failed_from_error

STATUS_BY_CODE: dict[str, int] = {
    "not_found": 404,
    "validation_failed": 422,
    "conflict": 409,
    "permission_denied": 403,
    # Same status as `permission_denied`, a different `code`: the credential is an agent's
    # and the operation is reserved to a person. Left unmapped it would fall to the 400
    # default, a generic bad request that an agent has every reason to retry.
    "agent_forbidden": 403,
    "immutable_field": 409,
    # The dead states of an invitation (REB-290): unknown is a 404 like any id that is not
    # this space's; the other three are 410 Gone (the credential existed and has ended),
    # which the acceptance page renders as three sentences told apart by `code`.
    "invitation_unknown": 404,
    "invitation_expired": 410,
    "invitation_revoked": 410,
    "invitation_used": 410,
    # REB-292: about the state of the space's admins, not the caller (who is an admin).
    "last_active_admin": 409,
    "self_account_change": 409,
    # REB-622: a settings save built on a row that changed since it was read; `updated_at`
    # in the document is the row's current version.
    "stale_row": 409,
    "domain_error": 400,
}

TITLE_BY_CODE: dict[str, str] = {
    "not_found": "Risorsa non trovata",
    "validation_failed": "Dati non validi",
    "conflict": "Conflitto con lo stato attuale",
    "permission_denied": "Permesso negato",
    "agent_forbidden": "Operazione riservata a una persona",
    "immutable_field": "Campo non modificabile",
    "invitation_unknown": "Invito non trovato",
    "invitation_expired": "Invito scaduto",
    "invitation_revoked": "Invito revocato",
    "invitation_used": "Invito già usato",
    "last_active_admin": "Amministratore insufficiente",
    "self_account_change": "Modifica non consentita sul proprio account",
    "stale_row": "Riga cambiata nel frattempo",
    "domain_error": "Errore di dominio",
}


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """RFC 9457 problem details. The structured `details` survive to the client, which is
    what lets the UI highlight the offending field instead of showing a toast.

    `details` is free-form (`DomainError.__init__(self, message, **details)`), so it is
    spread first and the fixed keys are written after it: a detail named `code`, `type`,
    `title`, `status`, `detail` or `instance` can never overwrite the problem's own.
    The whole document goes through `jsonable_encoder` because a UUID, a Decimal or a
    datetime in the details would make `json.dumps` fail and turn a 4xx into a 500."""
    assert isinstance(exc, DomainError)
    status = STATUS_BY_CODE.get(exc.code, 400)
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content=jsonable_encoder(
            {
                **exc.details,
                "type": f"https://pigrocrm.dev/errors/{exc.code}",
                "title": TITLE_BY_CODE.get(exc.code, "Errore"),
                "status": status,
                "detail": exc.message,
                "code": exc.code,
                "instance": str(request.url.path),
            }
        ),
    )


async def insufficient_privilege_handler(request: Request, exc: Exception) -> JSONResponse:
    """A write the row-level policies refused is answered as the row not existing
    (REB-634): a scoped person reads a 404 for a row outside their scope, and a write
    that reaches outside gets the same answer rather than a 500 that says more. Any other
    database error goes on to the 500 it always was."""
    assert isinstance(exc, DBAPIError)
    if not is_policy_refusal(exc):
        raise exc
    return await domain_error_handler(request, OutOfScope())


def _italian_message(error: dict[str, Any]) -> str:
    """The sentence the UI shows for one request error: pydantic's own `msg` is English
    (`Field required`), and `fieldErrorFrom` puts it under the input as it is."""
    if error.get("type") == "extra_forbidden":
        return "campo non riconosciuto"
    loc = error.get("loc") or ()
    failed = validation_failed_from_error(error, field=str(loc[-1]) if loc else "valore")
    reason = str(failed.details["reason"])
    expected = failed.details.get("expected")
    if expected and error.get("type") != "missing":
        return f"{reason} (atteso: {expected})"
    return reason


async def request_validation_handler(_request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's own 422 for a body, query or path that never reached the endpoint, with
    the same `{"detail": [{loc, msg, type, ...}]}` shape it always had (the web client's
    `toProblem` reads it) and `msg` in Italian, worded by the same translator the MCP
    tools use. The first error is the one the UI shows, so every error is translated."""
    assert isinstance(exc, RequestValidationError)
    errors = [{**error, "msg": _italian_message(dict(error))} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


class ProblemDetail(BaseModel):
    """RFC 9457 problem document, the shape `domain_error_handler` renders every
    `DomainError` into. Extra fields are allowed: the details (`active_deals`,
    `expected`, ...) vary by error, not by route."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str
    code: str
    instance: str


_PROBLEM_DETAIL_SCHEMA: dict[str, Any] = ProblemDetail.model_json_schema()
# The class docstring would be repeated in every response entry of the document.
_PROBLEM_DETAIL_SCHEMA.pop("description", None)


def _problem_response(description: str) -> dict[str, Any]:
    """Built by hand, not with FastAPI's `"model": ProblemDetail`: that files the schema
    under the route's success media type (`application/json`), while
    `domain_error_handler` always answers `application/problem+json`."""
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": _PROBLEM_DETAIL_SCHEMA}},
    }


def _domain_and_request_validation_response(description: str) -> dict[str, Any]:
    """422 has two runtime shapes: a `ValidationFailed` rendered as
    `application/problem+json`, and FastAPI's own request parsing failure (a non-UUID
    path segment, a malformed body), which answers `application/json` with
    `HTTPValidationError` and never reaches `domain_error_handler`. Declaring a 422 at
    all makes FastAPI skip documenting the second shape, and the web client's
    `fieldErrorFrom` reads it, so both are declared, as two media types."""
    return {
        "description": description,
        "content": {
            "application/problem+json": {"schema": _PROBLEM_DETAIL_SCHEMA},
            "application/json": {"schema": {"$ref": f"{REF_PREFIX}HTTPValidationError"}},
        },
    }


# Attached to every router: the outcomes any endpoint that resolves an Actor or touches
# an entity can produce, described once instead of per route. An over-approximation on
# purpose (a route that cannot raise a 409 still lists it), public routers included:
# narrowing it per route would change the generated client's types for no gain.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: _problem_response("Permission denied: the actor does not have the required role."),
    404: _problem_response("The requested resource does not exist or was removed."),
    409: _problem_response("The request conflicts with the current state of the resource."),
    422: _domain_and_request_validation_response(
        "A domain rule was not met (application/problem+json), or the request's body, "
        "parameters or path did not have the expected shape and never reached the "
        "endpoint (application/json)."
    ),
}


# Only the public invitation routes (`routers/auth.py`) can answer these two, so they are
# attached per route: a 410 in `PROBLEM_RESPONSES` would claim any endpoint can report a
# dead invitation.
INVITE_GONE_RESPONSE = _problem_response(
    "The invitation exists but is over: expired, revoked or already used. `code` tells "
    "the three cases apart (`invitation_expired`, `invitation_revoked`, `invitation_used`)."
)
INVITE_NOT_FOUND_RESPONSE = _problem_response("No invitation carries this link.")


def ensure_validation_error_schemas_are_declared(schema: dict[str, Any]) -> dict[str, Any]:
    """`PROBLEM_RESPONSES`'s 422 `$ref`s `HTTPValidationError`, which FastAPI registers in
    `components.schemas` only for a route whose 422 it documents itself; every router here
    declares its own, so the reference would point at nothing. FastAPI's own two
    definitions are reused so the schema stays what a malformed body really produces.
    Called from `main.py`'s `app.openapi` override; `setdefault` makes it idempotent."""
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas.setdefault("ValidationError", validation_error_definition)
    schemas.setdefault("HTTPValidationError", validation_error_response_definition)
    return schema
