"""The «Aziende» tools: the companies' requests, their thread, status and trail, and the CRM
link (`create_company`, `link_company_to_pigro`)."""

import re
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from rebase_core.admin_schemas import StatusChange
from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyAdminCreate, CompanyOverride
from rebase_core.notes import NoteService
from rebase_core.service import LIST_LIMIT_DEFAULT
from rebase_mcp.tools_common import (
    EMAILS_READ,
    READ_ENTITY,
    SOURCE,
    Deps,
    bounded,
    day,
    field_refusal,
    integer,
    moment,
    not_configured,
    number,
    read_entity_id,
    search_term,
    supplied_text,
    to_uuid,
)

_DEAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _deal_id(text: str | None) -> str | None:
    """The CRM deal's id out of what an admin gives: the id, or the deal's address."""
    if text is None or not text.strip():
        return None
    found = _DEAL_UUID.search(text)
    if found is None:
        raise ToolError("deal_id: not a deal id or a deal address")
    return found.group(0).lower()


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def list_companies(
        limit: int = LIST_LIMIT_DEFAULT,
        status: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        budget_min: str | None = None,
        budget_max: str | None = None,
        period_from: str | None = None,
        origin: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> dict[str, Any]:
        """The companies that described a project on the hub, like the «Aziende» screen:
        each row carries company, contact person, email, `phone`, `contact_points`, project,
        start date and duration, daily budget and status. Most recent first, or most relevant
        first when `q` narrows the list.
        `q` searches company name, contact person, email (the contact's extra addresses,
        phones and profiles too) and project; `status` filters (`new`,
        `contacted`, `in_progress`, `closed`) and `by_status` counts with every filter except
        `status`; `budget_min` and `budget_max` apply to the daily budget (a string: «500»,
        «450.50» or Italian style «1.500», «1.234,50»), `period_from` to the project start (ISO
        8601), `origin` to the page the request came from, `created_from` and
        `created_to` to the creation date (ISO 8601, `YYYY-MM-DD` is enough).
        `next_cursor` is the opaque cursor of the next page, `None` on the last one:
        pass it back in `cursor` together with the same parameters; a cursor created with
        a different `q` is refused. Each row carries the state of the CRM link
        (`crm_status`, `crm_situation`, `crm_deal_url`). Read only."""
        term = search_term(q)
        bounded(status, "status")
        bounded(origin, "origin")
        bounded(cursor, "cursor")
        budget_from = number(budget_min, "budget_min")
        budget_to = number(budget_max, "budget_max")
        period_start = day(period_from, "period_from")
        created_after = moment(created_from, "created_from")
        created_before = moment(created_to, "created_to")

        def call(session: Session) -> dict[str, Any]:
            page = CompanyService(session).list_recent(
                limit=limit,
                status=status,
                q=term,
                cursor=cursor,
                budget_min=budget_from,
                budget_max=budget_to,
                period_from=period_start,
                origin=origin,
                created_from=created_after,
                created_to=created_before,
            )
            body = page.model_dump(mode="json")
            # Every row carries the referente's address: a list of them is a read of
            # personal data an agent made, and the trail says so (REB-803).
            deps.record_read(
                session,
                READ_ENTITY,
                read_entity_id("list_companies"),
                EMAILS_READ,
                {"tool": "list_companies", "lines": len(page.items)},
            )
            return body

        return deps.call(call)

    @mcp.tool()
    def get_company(company_id: str) -> dict[str, Any]:
        """A company's request, by id, with `note`: the thread of the people who followed
        it, most recent first, each with `id`, author, date, `source` (`web`, `mcp`,
        `person`, `system`), last edit and `editable`. Deleted notes are not
        included. It also carries where the link to the rebase CRM stands: `crm_status`
        (`to_link`, `linked`, `error`, `rejected`; empty for a request created
        before the link existed), `crm_situation` with the CRM's sentence when it is not
        linked, `crm_deal_url` with the deal's address and `crm_attempted_at`. `phone` is the
        contact's primary phone point and `contact_points` every way to reach them: the login
        address first (primary email, no `id`), then the extra addresses, phones and profiles
        with their label and note."""
        return deps.run(lambda s: CompanyService(s).get(to_uuid(company_id, "company_id")))

    @mcp.tool()
    def set_company_status(company_id: str, status: str) -> dict[str, Any]:
        """Moves a request between `new`, `contacted`, `in_progress` and `closed`. The change
        is written to the log of `get_company_audit` (`status_changed`, with before and after):
        for the reason use `add_company_note`."""
        return deps.run(
            lambda s: CompanyService(s).set_status(
                to_uuid(company_id, "company_id"),
                StatusChange(status=status),
                admin_id=deps.admin().id,
                source=SOURCE,
            )
        )

    @mcp.tool()
    def add_company_note(company_id: str, text: str) -> dict[str, Any]:
        """Adds a note to a company request's thread, without touching the
        status: how the call went, what they asked, what is left to do. The note is already
        signed (the admin behind the token) and dated, with `source` `mcp`: the text must not
        start with a date or a time nor name who writes it. Up to 4000 characters, multiple
        lines allowed. It is corrected with `update_note` and deleted with `delete_note`, and
        every addition is written to the log of `get_company_audit`."""
        return deps.run(
            lambda s: NoteService(s).add(
                "company",
                to_uuid(company_id, "company_id"),
                text,
                author_id=deps.admin().id,
                source=SOURCE,
            )
        )

    @mcp.tool()
    def override_company(
        company_id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        linkedin_url: str | None = None,
        company_name: str | None = None,
        requested_role: str | None = None,
        project: str | None = None,
        period_from: str | None = None,
        duration: str | None = None,
        daily_budget: str | None = None,
        remote: str | None = None,
        onsite_days: str | None = None,
        headcount: str | None = None,
        status: str | None = None,
        contact_points: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Writes or clears any field of the request: the contact person's first and last
        name and LinkedIn profile (on the shared identity in `users`), company name,
        requested role, project, start date, duration, daily budget, work mode, onsite days
        per week, number of people requested and status. Notes are not a field: they live in
        the thread (`add_company_note`, `update_note`). Every omitted parameter stays as it
        was; an empty string clears only where allowed (LinkedIn, onsite days), elsewhere
        it is refused because the field cannot be left without a value.
        `onsite_days` must be given only, and always, when `remote` is `hybrid` --
        refused otherwise, by the same database `CHECK`.
        `contact_points` replaces the contact person's whole list of extra contact points (`[]`
        clears it): a list of objects `{"kind": "email"|"phone"|"linkedin", "value": str,
        "label": "work"|"personal"|"billing"|"other", "note": str|null (at most 200
        characters), "is_primary": bool}`. The login address is shown first in every read as
        the primary email, without an `id`, and is not part of the list: it stays the identity,
        receives the sign-in link and is what campaigns write to, so an extra address is never
        a login and never a recipient. An extra address is never primary; of the phones and
        profiles, one is primary (the first, when none is flagged). An address that is another
        person's login or extra address is refused with its name; the same value twice is
        refused. `id` and `position` of a point read back are ignored on write. Every real
        change is written to the log of `get_company_audit`, with the value before and after;
        to undo it use `revert_company_action`."""
        kwargs: dict[str, Any] = {}
        if first_name is not None:
            kwargs["first_name"] = supplied_text(first_name)
        if last_name is not None:
            kwargs["last_name"] = supplied_text(last_name)
        if linkedin_url is not None:
            kwargs["linkedin_url"] = supplied_text(linkedin_url)
        if company_name is not None:
            kwargs["company_name"] = supplied_text(company_name)
        if requested_role is not None:
            kwargs["requested_role"] = supplied_text(requested_role)
        if project is not None:
            kwargs["project"] = supplied_text(project)
        if period_from is not None:
            kwargs["period_from"] = None if period_from == "" else day(period_from, "period_from")
        if duration is not None:
            kwargs["duration"] = supplied_text(duration)
        if daily_budget is not None:
            kwargs["daily_budget"] = (
                None if daily_budget == "" else number(daily_budget, "daily_budget")
            )
        if remote is not None:
            kwargs["remote"] = supplied_text(remote)
        if onsite_days is not None:
            kwargs["onsite_days"] = integer(onsite_days, "onsite_days")
        if headcount is not None:
            kwargs["headcount"] = integer(headcount, "headcount")
        if status is not None:
            kwargs["status"] = supplied_text(status)
        if contact_points is not None:
            kwargs["contact_points"] = contact_points
        override = CompanyOverride(**kwargs)
        return deps.run(
            lambda s: CompanyService(s).override(
                to_uuid(company_id, "company_id"), override, deps.admin().id
            )
        )

    @mcp.tool()
    def delete_company(company_id: str) -> dict[str, Any]:
        """Deletes the request: it leaves the companies list. Reversible, never a real
        deletion: `restore_company` brings it back as it was."""
        return deps.run(
            lambda s: CompanyService(s).soft_delete(
                to_uuid(company_id, "company_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def restore_company(company_id: str) -> dict[str, Any]:
        """Restores a deleted request: it returns to the companies list. On an already
        active request nothing changes."""
        return deps.run(
            lambda s: CompanyService(s).restore(to_uuid(company_id, "company_id"), deps.admin().id)
        )

    @mcp.tool()
    def get_company_audit(company_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Who edited, deleted or restored this request, and when: each entry with the
        administrator, the kind of action and, for a field edit, the value before and
        after. It also works on a deleted request. Most recent first. Read only; to undo
        a field edit use `revert_company_action` with the entry's id."""
        return deps.run_list(
            lambda s: CompanyService(s).audit_timeline(to_uuid(company_id, "company_id"), limit)
        )

    @mcp.tool()
    def revert_company_action(company_id: str, action_id: str) -> dict[str, Any]:
        """Puts a field back to the value it had before a past edit, read from the
        log entry `action_id` (from `get_company_audit`). Only on an entry that is a
        field edit, not on a deletion or a restore."""
        return deps.run(
            lambda s: CompanyService(s).revert(
                to_uuid(company_id, "company_id"), to_uuid(action_id, "action_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def create_company(
        company_name: str,
        contact_first_name: str,
        contact_last_name: str,
        email: str,
        phone: str,
        requested_role: str,
        project: str,
        period_from: str,
        duration: str,
        daily_budget: str,
        remote: str,
        headcount: int = 1,
        onsite_days: int | None = None,
        deal_id: str | None = None,
        contact_points: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Opens a company's request on behalf of the contact person, for a company met
        directly: the same fields and the same checks as the wizard (`period_from` as
        `YYYY-MM-DD`, `daily_budget` in euros per day, `remote` one of `remote`,
        `hybrid`, `on_site`; `onsite_days` only, and always, for hybrid). The contact person
        is found or created from the email. No email is sent, neither to the contact person
        nor to rebase. `email` is the contact's login address and primary email, and `phone`
        their primary number. `contact_points` are their other ways to reach them: a list of
        objects `{"kind": "email"|"phone"|"linkedin", "value": str,
        "label": "work"|"personal"|"billing"|"other", "note": str|null (at most 200
        characters)}`, stored on the contact at creation and sent to the CRM with the request.
        When the contact is already on file their own points stay: the new ones are added, a
        point they already have is left as it is, and none is primary on its own (the first
        phone or profile becomes primary when there is none; an extra address is never
        primary). An address that is another person's login or extra address is refused with
        its name, before anything is written. Without `deal_id` it is a request like any
        other: the CRM records it as a new customer and a new deal. With `deal_id` (the id of
        a deal the CRM already has, or its address `.../app/deal/<id>`) the CRM links that deal
        and creates nothing: neither a second customer nor a second deal. It returns the
        request as it is, with `crm_status` (`linked`, or `rejected`/`error` with the CRM's
        sentence in `crm_situation`, for example for a deal the CRM does not know) and
        `crm_deal_url`. When the CRM is not configured on this environment the request is still
        opened and stays `to_link`."""
        fields: dict[str, Any] = {
            "company_name": company_name,
            "contact_first_name": contact_first_name,
            "contact_last_name": contact_last_name,
            "email": email,
            "phone": phone,
            "requested_role": requested_role,
            "project": project,
            "period_from": period_from,
            "duration": duration,
            "daily_budget": daily_budget,
            "remote": remote,
            "headcount": headcount,
            "onsite_days": onsite_days,
            "deal_id": _deal_id(deal_id),
            "contact_points": contact_points or [],
        }
        try:
            data = CompanyAdminCreate.model_validate(fields)
        except PydanticValidationError as exc:
            raise field_refusal(exc) from None

        # A refusal of the contact's points is said first: it spends no model call.
        deps.call(lambda s: CompanyService(s).check_admin_create(data))
        crm_on = (
            deps.crm_leads is not None
            and deps.settings is not None
            and bool(deps.settings.pigro_engagements_token)
        )
        if crm_on and data.deal_id is None:
            # A new deal gets the model's draft: counted before the request is written.
            deps.spend_model_call()

        def call(session: Session) -> dict[str, Any]:
            service = CompanyService(session)
            created = service.create_by_admin(data, deps.admin().id, source=SOURCE)
            if crm_on and deps.crm_leads is not None:
                deps.crm_leads(session).link(created.id, deps.admin().id)
            return service.get(created.id).model_dump(mode="json")

        return deps.call(call)

    @mcp.tool()
    def link_company_to_pigro(company_id: str, deal_id: str | None = None) -> dict[str, Any]:
        """«Riprova» on a company's request: records it now in the rebase CRM (customer,
        contact and deal with the AI draft), on behalf of the admin behind the token, and
        returns the request as it is: `crm_status` (`linked`, or `error`/`rejected` with the
        CRM's sentence in `crm_situation`) and `crm_deal_url`. It also records a request
        created before the link existed. With `deal_id` (the id of a deal the CRM already
        has) it is «Collega a un altro deal»: the CRM links that deal and creates nothing,
        and a refusal leaves the link as it was. Refused for a request that is not found or
        deleted and when the CRM is not configured on this environment."""
        if (
            deps.crm_leads is None
            or deps.settings is None
            or not deps.settings.pigro_engagements_token
        ):
            raise not_configured("The CRM", "REBASE_PIGRO_ENGAGEMENTS_TOKEN")
        build = deps.crm_leads
        key = to_uuid(company_id, "company_id")
        deal = to_uuid(deal_id, "deal_id") if deal_id is not None else None
        if deal is None:
            # Filing a request with no deal named asks the model for a draft.
            deps.spend_model_call()

        def call(session: Session) -> dict[str, Any]:
            service = CompanyService(session)
            service.get(key)
            build(session).link(key, deps.admin().id, deal)
            return service.get(key).model_dump(mode="json")

        return deps.call(call)
