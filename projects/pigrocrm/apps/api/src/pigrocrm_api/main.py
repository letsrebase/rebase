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
    accountant_export,
    activities,
    ai_model,
    analytics,
    approval_requests,
    approvals,
    auth,
    automations,
    bank_accounts,
    bank_movements,
    calendar,
    cash,
    collaborators,
    contract_billing,
    contract_emails,
    contract_expenses,
    contracts,
    cost_categories,
    dashboard,
    deals,
    documenso,
    documents,
    drive,
    due_items,
    email_drafts,
    engagements,
    expense_notes,
    f24_groups,
    fields,
    freelancer_door,
    gmail,
    identity,
    inbox,
    invoices,
    iva_period_locks,
    iva_registers,
    iva_settlements,
    legal_entities,
    mailboxes,
    notes,
    organizations,
    own_engagement,
    payment_reminders,
    people,
    period_locks,
    pipeline,
    proposals,
    purchases,
    push,
    recurring_expenses,
    reminders,
    scheduled_jobs,
    schema,
    search,
    shared_export,
    slack,
    space_modules,
    space_settings,
    staff,
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
        organizations,
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
        purchases,
        cost_categories,
        period_locks,
        analytics,
        iva_settlements,
        iva_registers,
        iva_period_locks,
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
        # The hub's reads of a freelancer through the same door, by the hub's freelancer id:
        # availability, the member's contracts, the CV's fiscal identifiers, the merge check.
        freelancer_door,
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
        # REB-976: engagements with freelancers and their availability, `collaborators` module.
        collaborators,
        # REB-1019: Documenso's webhook, root only, behind its own secret: routers/documenso.py.
        documenso,
        # REB-1035: the freelancer's own card on their contract, read from rebase's space.
        own_engagement,
        # REB-971: the inbox of incoming documents, filled by upload. Before `notes`, which stays
        # last: the web tests type their mock of `api.GET` against the last path of the API.
        inbox,
        # REB-1015: IMAP and PEC mailboxes, their sealed password and their health.
        mailboxes,
        # REB-879: the signed, dated notes thread of a deal, a customer and a person.
        notes,
        # REB-967: the amounts that fall due and what settles them, core in every space.
        due_items,
        # REB-977: employees and directors with their pay, behind the `staff` module.
        staff,
        # REB-1046: a staff member's receipts from submission to reimbursement, behind `staff`
        # and `purchases`.
        expense_notes,
        # REB-972: the model PigroCRM reads documents with: settings, key, cap, «Prova».
        ai_model,
        # REB-1011: the period bundle for the accountant.
        accountant_export,
        # REB-1047: the door of an export's share link, with no login: the token is the credential.
        shared_export,
        # REB-1022: the recurrence rules that forecast money going out, `purchases` module.
        recurring_expenses,
        # REB-1025: bank accounts, statements and movements, behind the `cash` module.
        bank_accounts,
        # REB-1042: matching a movement to the due items it pays, behind the `cash` module.
        bank_movements,
        # REB-1025: the cash schedule, the payables mirror and the phone's money figures, core.
        cash,
        # REB-1076: due items grouped into an F24 paid by one movement, behind `taxes`.
        f24_groups,
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
