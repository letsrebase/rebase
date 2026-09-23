from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pigrocrm.core.errors import DomainError
from pigrocrm_api.errors import domain_error_handler, ensure_validation_error_schemas_are_declared
from pigrocrm_api.routers import (
    activities,
    analytics,
    auth,
    automations,
    calendar,
    cost_categories,
    costs,
    customers,
    dashboard,
    deals,
    documents,
    drive,
    email_drafts,
    emitter,
    fields,
    fiscal_profile,
    gmail,
    identity,
    invoices,
    payment_reminders,
    people,
    period_locks,
    pipeline,
    schema,
    search,
    space_settings,
    templates,
    tenants,
    time_entries,
    tokens,
    users,
)
from pigrocrm_api.tenancy import TenantPrefixMiddleware


def create_app() -> FastAPI:
    app = FastAPI(
        title="PigroCRM API",
        version="0.1.0",
        description=(
            "API pubblica di PigroCRM. La UI e il server MCP usano esclusivamente questi servizi. "
            "Il campo `custom_fields` di ogni entità è deliberatamente un oggetto libero in "
            "questo documento: le chiavi realmente presenti dipendono dalle field_definitions "
            "configurate a runtime da ciascun tenant e non sono, e non possono essere, fissate "
            "qui. Chiama `GET /api/schema/{entity_type}` per scoprire, in ogni momento, quali "
            "campi custom esistono per una data entità, con il loro tipo e le eventuali opzioni."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(DomainError, domain_error_handler)

    for module in (
        auth,
        customers,
        people,
        deals,
        fields,
        pipeline,
        users,
        tokens,
        schema,
        search,
        documents,
        templates,
        emitter,
        invoices,
        fiscal_profile,
        time_entries,
        costs,
        cost_categories,
        period_locks,
        analytics,
        gmail,
        drive,
        email_drafts,
        payment_reminders,
        # Slice 6. The tuple is grouped by slice rather than sorted -- the import list
        # above is what stays alphabetical -- so a reader can see which surface each
        # release added.
        dashboard,
        automations,
        # Spaces (2026-09-08). Public signup, on the registry database: routers/tenants.py.
        tenants,
        # Cross-space identity (2026-09-23, REB-376): also root-scoped, on the registry.
        identity,
        # Settings → Space: the settings a database decides for itself.
        space_settings,
        # Slice 10 (2026-09-09): the commitments, and the month that reads them beside
        # the hours and the invoices falling due.
        activities,
        calendar,
    ):
        app.include_router(module.router)

    # PROBLEM_RESPONSES (attached to every router above) declares a 422 that
    # `$ref`s HTTPValidationError alongside ProblemDetail -- see
    # ensure_validation_error_schemas_are_declared's docstring for why that
    # reference would otherwise point at nothing: declaring 422 at all, on every
    # router, suppresses the automatic registration FastAPI would otherwise do.
    # `generate_openapi` (the bound method, captured before it is replaced below)
    # already handles the title/version/description/routes wiring and the
    # `self.openapi_schema` caching; this only post-processes its result.
    generate_openapi = app.openapi

    def openapi_with_validation_error_schemas() -> dict[str, Any]:
        return ensure_validation_error_schemas_are_declared(generate_openapi())

    app.openapi = openapi_with_validation_error_schemas  # type: ignore[method-assign]

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Outermost, so `/<slug>/api/...` is `/api/...` by the time CORS, the routers and
    # every dependency see it (tenancy.py). Added after the routes so FastAPI's own
    # middleware stack is complete; `add_middleware` wraps what is already there.
    app.add_middleware(TenantPrefixMiddleware)

    return app


app = create_app()
