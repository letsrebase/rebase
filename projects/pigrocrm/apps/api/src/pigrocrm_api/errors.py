from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.openapi.constants import REF_PREFIX
from fastapi.openapi.utils import validation_error_definition, validation_error_response_definition
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from pigrocrm.core.errors import DomainError

STATUS_BY_CODE: dict[str, int] = {
    "not_found": 404,
    "validation_failed": 422,
    "conflict": 409,
    "permission_denied": 403,
    # 403, the same status as `permission_denied` and for the same reason a browser or an
    # agent client cares about: the request was understood and authenticated, and the
    # credential may not do this. Mapped explicitly rather than left to fall through to
    # the 400 default, which would render a policy refusal as "Errore di dominio" -- a
    # generic bad-request an agent has every reason to retry, and a status no client can
    # tell apart from a malformed body. The *sentence* is what distinguishes it from
    # `permission_denied` ("richiede una persona", not "richiede un ruolo migliore"), and
    # `code` carries the distinction machine-readably.
    "agent_forbidden": 403,
    "immutable_field": 409,
    # The four dead states of an invitation (spec 2026-09-17 §1), mapped by REB-290.
    # `invitation_unknown` is a 404: no row carries the hash, the same answer the
    # admin surface gives for an id that is not this space's. The other three are
    # 410 Gone: the credential existed and has ended, which is exactly the
    # distinction the acceptance page renders as three different sentences --
    # `code` in the problem document names which.
    "invitation_unknown": 404,
    "invitation_expired": 410,
    "invitation_revoked": 410,
    "invitation_used": 410,
    "domain_error": 400,
}

TITLE_BY_CODE: dict[str, str] = {
    "not_found": "Risorsa non trovata",
    "validation_failed": "Dati non validi",
    "conflict": "Conflitto con lo stato attuale",
    "permission_denied": "Permesso negato",
    "agent_forbidden": "Operazione riservata a una persona",
    "immutable_field": "Campo non modificabile",
    # The dead invitation's `detail` is the Italian sentence the acceptance page
    # shows; the title is only the category.
    "invitation_unknown": "Invito non trovato",
    "invitation_expired": "Invito scaduto",
    "invitation_revoked": "Invito revocato",
    "invitation_used": "Invito già usato",
    "domain_error": "Errore di dominio",
}


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """RFC 9457 problem details. The structured `details` survive to the client, which
    is what lets the UI highlight the offending field instead of showing a toast.

    `details` is typed `Any` at the source (`DomainError.__init__(self, message,
    **details)`) -- nothing there stops a caller from putting a UUID, a Decimal, or a
    datetime in it. `JSONResponse` renders through stdlib `json.dumps`, which cannot
    serialise any of those: passing the content straight through would let a single
    UUID in an error's details crash this handler while it builds the response, i.e.
    turn what should be a clean 4xx into an opaque, unhandled 500. `jsonable_encoder`
    is FastAPI's own recursive converter for exactly this gap (UUID/Decimal/datetime/
    enum/pydantic models -> JSON-safe primitives) and is run on the whole content dict,
    not just `details`, so it never needs to know the fixed keys from the variable
    ones."""
    assert isinstance(exc, DomainError)
    status = STATUS_BY_CODE.get(exc.code, 400)
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content=jsonable_encoder(
            {
                "type": f"https://pigrocrm.dev/errors/{exc.code}",
                "title": TITLE_BY_CODE.get(exc.code, "Errore"),
                "status": status,
                "detail": exc.message,
                "code": exc.code,
                "instance": str(request.url.path),
                **exc.details,
            }
        ),
    )


class ProblemDetail(BaseModel):
    """RFC 9457 problem document -- the exact shape `domain_error_handler` above
    renders every `DomainError` into. Documented once here and attached, as a
    shared `responses=` dict, to every router at construction time (see
    `PROBLEM_RESPONSES` below and its use in each `routers/*.py`), rather than
    enumerated per endpoint: any route that resolves an `Actor` or looks up an
    entity can raise `NotFound`/`Conflict`/`ValidationFailed`/`PermissionDenied`,
    so restating that per route, 37 times over, would document nothing a reader
    couldn't already infer.

    Extra fields are allowed on purpose: `domain_error_handler` spreads
    `exc.details` at the top level -- e.g. `active_deals` on a delete conflict,
    `expected` on a validation failure -- and those vary by error, not by route,
    so they are not modeled as individual named fields here.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str
    code: str
    instance: str


_PROBLEM_DETAIL_SCHEMA: dict[str, Any] = ProblemDetail.model_json_schema()
# Pydantic copies the class docstring above into the schema's own "description" --
# useful reading the source, pure noise repeated in the rendered OpenAPI document:
# this same dict is deep-copied into every one of the ~150 response entries below
# (37 routes x 4 codes), and each entry already carries its own short, specific
# `description` (see `_problem_response`) at the response-object level, one level
# up from this schema. Dropping the duplicate here is the difference between a
# `/openapi.json` a client generator can skim and one padded with the same essay
# repeated 150 times.
_PROBLEM_DETAIL_SCHEMA.pop("description", None)


def _problem_response(description: str) -> dict[str, Any]:
    """A hand-built `content` mapping, not FastAPI's `"model": ProblemDetail`
    shortcut: that shortcut always files the schema under the route's own success
    media type (`application/json` here), which would misdocument every one of
    these as the wrong content type -- `domain_error_handler` above always
    responds `application/problem+json`, never plain `application/json`."""
    return {
        "description": description,
        "content": {"application/problem+json": {"schema": _PROBLEM_DETAIL_SCHEMA}},
    }


def _domain_and_request_validation_response(description: str) -> dict[str, Any]:
    """422 is genuinely two different runtime shapes sharing one status code, not
    one shape documented sloppily. Almost every 422 these routers raise themselves
    (an invalid custom-field value, a bad VAT number, ...) is a `ValidationFailed`
    rendered by `domain_error_handler` as `application/problem+json`. But FastAPI's
    own request parsing can *also* reject a request before an endpoint ever runs --
    e.g. a non-UUID path segment, or a malformed JSON body -- entirely outside
    `domain_error_handler`, and that path always answers `application/json` with
    FastAPI's own `HTTPValidationError` shape (`{"detail": [{"loc": [...], "msg":
    ..., "type": ...}]}`).

    An earlier version of this fix declared only the domain shape for 422, on the
    (correct, but incomplete) reasoning that a shared `responses=` dict doesn't
    need to enumerate which codes each route can *actually* raise. What that
    version missed: declaring 422 at all -- regardless of which schema -- makes
    FastAPI skip its own automatic `HTTPValidationError` documentation for that
    status (see `get_openapi_path` in `fastapi/openapi/utils.py`: it only adds the
    generic entry when no 422/4XX/default key is already present), so the generic
    shape silently disappeared from the generated document even though the server
    still returns it. Verified with `openapi-typescript`: the generated TS type for
    422 only carried `ProblemDetail`, with no trace of the array-of-errors shape.
    That is a real, load-bearing gap for slice 1B's planned `fieldErrorFrom`
    (highlights the offending form field from `HTTPValidationError.detail[].loc`)
    -- it would simply never fire for a FastAPI-level validation error, with
    nothing visibly broken, and no obvious link back to this cause.

    Two distinct media types on the same response, not an `anyOf` inside one --
    `application/problem+json` and `application/json` really are two different
    `Content-Type` headers the server can send for this one status code, so the
    document should say that plainly rather than collapsing them into a single
    content type with two possible bodies.
    """
    return {
        "description": description,
        "content": {
            "application/problem+json": {"schema": _PROBLEM_DETAIL_SCHEMA},
            "application/json": {"schema": {"$ref": f"{REF_PREFIX}HTTPValidationError"}},
        },
    }


# Attached to every router in main.py's registration loop: the domain-error
# outcomes any endpoint that resolves an Actor or touches an entity can produce,
# described once instead of per-route -- see ProblemDetail's docstring for why
# enumerating which codes each individual endpoint can raise would not be worth
# it. Passing this blanket means an endpoint that cannot actually raise, say, 409
# still lists it -- an accepted over-approximation, not an attempt to model each
# route's exact exception set.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: _problem_response("Permesso negato: l'actor non ha il ruolo richiesto."),
    404: _problem_response("La risorsa richiesta non esiste o è stata rimossa."),
    409: _problem_response("La richiesta è in conflitto con lo stato attuale della risorsa."),
    422: _domain_and_request_validation_response(
        "Una regola di dominio non è stata rispettata (application/problem+json), "
        "oppure il corpo, i parametri o il path della richiesta non hanno la forma "
        "attesa e non hanno mai raggiunto l'endpoint (application/json)."
    ),
}


# The two problem shapes only the public invitation routes can answer (the peek and
# the accept in `routers/auth.py`), attached per route rather than through
# `PROBLEM_RESPONSES`: the blanket dict rides on every router in `main.py`, and a 410
# there would claim that any endpoint in the API can report a dead invitation.
INVITE_GONE_RESPONSE = _problem_response(
    "L'invito esiste ma è finito: scaduto, revocato o già usato. `code` distingue i "
    "tre casi (`invitation_expired`, `invitation_revoked`, `invitation_used`)."
)
INVITE_NOT_FOUND_RESPONSE = _problem_response("Nessun invito porta questo link.")


def ensure_validation_error_schemas_are_declared(schema: dict[str, Any]) -> dict[str, Any]:
    """`PROBLEM_RESPONSES`'s 422 entry `$ref`s `HTTPValidationError` (see
    `_domain_and_request_validation_response` above) -- the same schema FastAPI
    would normally register in `components.schemas` on its own, except it only
    does that for a route whose 422 is left to its own default handling, and
    every router here deliberately declares its own 422. Left alone, that `$ref`
    would point at nothing and the document would not validate.

    Reuses FastAPI's own two definitions (`ValidationError`/`HTTPValidationError`)
    rather than hand-copying their shape -- the two must stay byte-for-byte the
    schema FastAPI itself would have generated, since that shape is exactly what
    a non-UUID path segment or a malformed body actually produces at runtime; a
    hand-maintained copy could silently drift from it.

    Called from `main.py`'s `app.openapi` override, on the dict `app.openapi()`
    already builds and caches -- `setdefault` makes this idempotent, so calling
    it again on an already-patched, cached schema is a no-op.
    """
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas.setdefault("ValidationError", validation_error_definition)
    schemas.setdefault("HTTPValidationError", validation_error_response_definition)
    return schema
