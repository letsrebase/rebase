from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import DBAPIError

from pigrocrm.core.config import get_settings, require_production_secrets
from pigrocrm.core.errors import DomainError
from pigrocrm_api.errors import (
    domain_error_handler,
    ensure_validation_error_schemas_are_declared,
    insufficient_privilege_handler,
    request_validation_handler,
)
from pigrocrm_api.routers import (
    activities,
    analytics,
    approval_requests,
    approvals,
    auth,
    automations,
    calendar,
    contract_billing,
    contract_emails,
    contract_expenses,
    contracts,
    cost_categories,
    costs,
    customers,
    dashboard,
    deals,
    documents,
    drive,
    email_drafts,
    engagements,
    fields,
    gmail,
    identity,
    invoices,
    legal_entities,
    notes,
    payment_reminders,
    people,
    period_locks,
    pipeline,
    proposals,
    push,
    reminders,
    scheduled_jobs,
    schema,
    search,
    slack,
    space_modules,
    space_settings,
    templates,
    tenants,
    time_entries,
    tokens,
    users,
    work_units,
)
from pigrocrm_api.tenancy import TenantPrefixMiddleware


def create_app() -> FastAPI:
    # A placeholder signing key on a TLS installation is a forgeable session: the app does
    # not exist until the secret is real (PCORE-B-05).
    require_production_secrets(get_settings())
    app = FastAPI(
        title="PigroCRM API",
        version="0.1.0",
        description=(
            "Public PigroCRM API. The UI and the MCP server use these services exclusively. The "
            "`custom_fields` field of every entity is deliberately a free-form object in this "
            "document: the keys actually present depend on the field_definitions each tenant "
            "configures at runtime, and they are not, and cannot be, fixed here. Call `GET "
            "/api/schema/{entity_type}` to discover, at any time, which custom fields exist for a"
            " given entity, with their type and any options."
        ),
    )
    # The SPA is same-origin behind nginx, and the dev server proxies `/api` (vite.config),
    # so there is no CORS by default; an installation that serves the SPA elsewhere lists
    # its origins in `PIGROCRM_CORS_ORIGINS`.
    cors_origins = get_settings().cors_origin_list
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(DBAPIError, insufficient_privilege_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)

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
        invoices,
        time_entries,
        costs,
        cost_categories,
        period_locks,
        analytics,
        legal_entities,
        gmail,
        drive,
        # REB-739: the Slack app installed per space, and who in it is whom.
        slack,
        email_drafts,
        payment_reminders,
        # Slice 6. Grouped by release rather than sorted, unlike the import list.
        dashboard,
        automations,
        # Spaces (2026-09-08). Public signup, on the registry database: routers/tenants.py.
        tenants,
        # rebase's engagements door (spec 2026-09-25 § 2.3/§ 2.4), on the registry
        # database, under its own token: routers/engagements.py.
        engagements,
        # Cross-space identity (2026-09-23, REB-376): also root-scoped, on the registry.
        identity,
        # Settings → Space: the settings a database decides for itself.
        space_settings,
        # The profile and the module switches (spec 2026-11-09): routes of their own, so the
        # generic settings page above cannot write them.
        space_modules,
        # Slice 10 (2026-09-09): the commitments, and the month that reads them beside
        # the hours and the invoices falling due.
        activities,
        calendar,
        # REB-358: the mastro-ledger foundations -- contracts and their rate cards.
        contracts,
        # REB-360: rebillable expenses against a contract's own spending policy.
        contract_expenses,
        # REB-751/752: billing a contract's days and the register that goes with the invoice.
        contract_billing,
        # REB-753: saved email templates per contract and the draft they make.
        contract_emails,
        # REB-362: the review-before-write step -- a contract's first draft, or a
        # day's evidence -- accepted or rejected by a human.
        proposals,
        # REB-745: the day ledger on screen -- record a day, read it, move it.
        work_units,
        # REB-747: record a client's approval and the days it covers.
        approvals,
        # REB-749: ask a client for prior approval of named days, from the contract.
        approval_requests,
        # REB-732: what the CRM raises on its own, and what a person does about it.
        reminders,
        # REB-738: the browsers a person receives reminders on.
        push,
        # REB-764: what the cron commands last did in this space.
        scheduled_jobs,
        # REB-879: the signed, dated notes thread of a deal, a customer and a person.
        notes,
    ):
        app.include_router(module.router)

    # The leads route of rebase's engagements door (spec 2026-10-30 § 2): its own prefix,
    # beside the engagements router above, never inside it.
    app.include_router(engagements.leads_router)

    # Every router declares a 422 that `$ref`s HTTPValidationError, which FastAPI then no
    # longer registers by itself: this adds the two schemas to the generated document.
    generate_openapi = app.openapi

    def openapi_with_validation_error_schemas() -> dict[str, Any]:
        return ensure_validation_error_schemas_are_declared(generate_openapi())

    app.openapi = openapi_with_validation_error_schemas  # type: ignore[method-assign]

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Outermost, so `/<slug>/api/...` is `/api/...` by the time CORS, the routers and every
    # dependency see it (tenancy.py). Added last so FastAPI's own stack is complete.
    app.add_middleware(TenantPrefixMiddleware)

    return app


app = create_app()
