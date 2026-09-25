import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from rebase_api.deps import SessionDep
from rebase_api.routers import (
    admin,
    companies,
    documenso,
    freelancers,
    matches,
    members,
    pigro,
    signups,
    tokens,
)
from rebase_core import analytics
from rebase_core.contracts.fields import ContractFailed
from rebase_core.errors import (
    DocumensoFailed,
    DomainError,
    LlmUnavailable,
    NotFound,
    SigningUnavailable,
    ValidationFailed,
)

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Nothing at start; at stop, the last completion event leaves before the process
    does (`rebase_core.analytics.shutdown`, a no-op when no key built a client)."""
    yield
    analytics.shutdown()


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """A domain error is a sentence and a status, never a stack trace. `NotFound` is a
    404, `ValidationFailed` a 422 in FastAPI's own shape so a form can point at the
    field, `LlmUnavailable` a 502, `ContractFailed` a 503, `DocumensoFailed` a 502,
    `SigningUnavailable` a 503, anything else a 409."""
    assert isinstance(exc, DomainError)
    if isinstance(exc, NotFound):
        return JSONResponse({"detail": exc.message}, status_code=404)
    if isinstance(exc, ValidationFailed):
        return JSONResponse(
            {
                "detail": [
                    {
                        "loc": ["body", exc.details["field"]],
                        "msg": exc.details["reason"],
                        "type": "value_error",
                    }
                ]
            },
            status_code=422,
        )
    if isinstance(exc, LlmUnavailable):
        # Claude did not answer: a gateway's failure, in the one Italian sentence a
        # page can show as it stands (`llm.py`, REB-508).
        return JSONResponse({"detail": exc.message}, status_code=502)
    if isinstance(exc, ContractFailed):
        # A contract that could not be typeset is the server's failure, not the
        # request's: pandoc missing, a template that no longer compiles, a malformed
        # REBASE_SIGNER_JSON. A sentence, and a status that says so.
        return JSONResponse({"detail": exc.message}, status_code=503)
    if isinstance(exc, DocumensoFailed):
        # The signing site refused or did not answer: a gateway's failure, in its words
        # and never its stack trace, which only the log keeps.
        _log.warning("documenso refused a call: %s", exc.detail)
        return JSONResponse({"detail": exc.message}, status_code=502)
    if isinstance(exc, SigningUnavailable):
        return JSONResponse({"detail": exc.message}, status_code=503)
    return JSONResponse({"detail": exc.message}, status_code=409)


def create_app() -> FastAPI:
    app = FastAPI(title="rebase API", version="0.1.0", lifespan=lifespan)
    app.add_exception_handler(DomainError, domain_error_handler)
    app.include_router(signups.router)
    app.include_router(freelancers.router)
    app.include_router(companies.router)
    app.include_router(admin.router)
    app.include_router(matches.router)
    app.include_router(documenso.router)
    app.include_router(members.router)
    app.include_router(pigro.router)
    app.include_router(tokens.router)

    @app.get("/health")
    def health(session: SessionDep) -> dict[str, str]:
        """Touches the database on purpose: a probe that answers from configuration
        alone reports a healthy deploy with Postgres on the floor
        (`docs/adding-a-project.md` §7)."""
        session.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
