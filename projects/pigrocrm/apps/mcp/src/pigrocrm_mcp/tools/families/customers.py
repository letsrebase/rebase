"""Customers: create, read, search, archive."""

from collections.abc import Callable
from typing import Any, cast

from mcp.server import MCPServer

from pigrocrm.core.customers.schemas import CustomerListQuery
from pigrocrm.core.db import SortDirection
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    customers,
    invoices,
)
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    CustomerChanges,
    OptionalContactPoints,
    parse_legal_entity_id,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- customers -------------------------------------------------------

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_customer(
        legal_name: str,
        vat_number: str | None = None,
        tax_code: str | None = None,
        sdi_code: str | None = None,
        certified_email: str | None = None,
        address: str | None = None,
        postal_code: str | None = None,
        city: str | None = None,
        province: str | None = None,
        contact_points: OptionalContactPoints = None,
        note: str | None = None,
        payment_days: int | None = None,
        payment_end_of_month: bool = False,
        custom_fields: dict[str, Any] | None = None,
        country: str | None = None,
        legal_entity_id: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a customer.

        `owner_id` is who is responsible for the customer and receives reminders about invoices
        and contracts (id in `list_members`): if omitted, it is the creator; only an administrator
        can name someone else, as with `update_customer`.

        `payment_days` and `payment_end_of_month` are the agreed terms («30 days invoice date
        end of month» = `30` with `True`); `issue_invoice` computes the due date from them,
        otherwise it uses the fiscal profile.

        `country`: ISO 3166-1 alpha-2 (`IT` if omitted). `legal_entity_id`: the company that
        invoices them (`list_legal_entities`); if omitted, the one from `suggest_legal_entity`.

        `contact_points`: see the server instructions; the PEC stays in `certified_email`.

        `note` is the customer's first note (without date or author: the CRM signs it).
        """
        return customers.create(
            context,
            {
                "legal_name": legal_name,
                "vat_number": vat_number,
                "tax_code": tax_code,
                "sdi_code": sdi_code,
                "certified_email": certified_email,
                "address": address,
                "postal_code": postal_code,
                "city": city,
                "province": province,
                "contact_points": contact_points or [],
                "note": note,
                "payment_days": payment_days,
                "payment_end_of_month": payment_end_of_month,
                "custom_fields": custom_fields or {},
                **({"country": country} if country is not None else {}),
                "legal_entity_id": parse_legal_entity_id(legal_entity_id),
                "owner_id": owner_id,
            },
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def suggest_legal_entity(country: str | None = None) -> dict[str, Any]:
        """The company that would invoice a new customer of `country` (ISO 3166-1 alpha-2, `IT` if
        omitted): the same one `create_customer` picks without `legal_entity_id`. Ask for it to
        show it to the person before creating.
        """
        return invoices.suggest_legal_entity(context, country)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_customer(customer_id: str, changes: CustomerChanges) -> dict[str, Any]:
        """Update a customer. Only an administrator can change `owner_id` (from `list_members`);
        `null` removes the owner.
        """
        return customers.update(context, customer_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_customer(customer_id: str) -> dict[str, Any]:
        """Read a customer, with the latest 50 notes (`note`, most recent first; `note_total`
        counts them, all of them are in `customer://<id>`).
        """
        return customers.get(context, customer_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def search_customers(
        search: str | None = None,
        status: str | None = None,
        custom: dict[str, Any] | None = None,
        legal_entity_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
        sort: str | None = None,
        # `str` and not `Literal["asc", "desc"]`, following the runtime-permissive /
        # schema-strict convention of `tools/params.py`: an out-of-range value then
        # raises `pydantic.ValidationError` inside `_guard`, which renders it as a
        # domain error the agent can read, instead of failing in the SDK's pre-call
        # `validate_arguments` where the message is not ours.
        dir: str = "asc",
    ) -> dict[str, Any]:
        """Search customers by legal name, VAT number, tax code or any email or phone. `sort`:
        `created_at`, `updated_at`, `legal_name`. `legal_entity_id` filters by company.
        """
        return customers.search(
            context,
            CustomerListQuery(
                search=search,
                status=status,
                custom=custom,
                legal_entity_id=parse_legal_entity_id(legal_entity_id),
                # cast: limit is `int | str` at runtime for the SDK-bypass reason
                # documented on `BoundedLimit` in `tools/params.py`; the *ListQuery schema this
                # feeds is what actually enforces (and coerces) "must be an int".
                limit=cast(int, limit),
                # Since slice 6 the cursor is an opaque string, not a UUID: it encodes
                # `(sort value, id)`. Passing it through unparsed is the whole
                # contract -- `UUID(cursor)` here would raise a bare `ValueError` on
                # every `next_cursor` this tool itself just handed the agent. The
                # tool's own signature and JSON Schema are unchanged, so nothing an
                # agent sees moves. `list_invoices` and the time-tracking tools keep
                # their `UUID(cursor)`: those pages still key on the id alone.
                cursor=cursor,
                sort=sort,
                dir=cast(SortDirection, dir),
            ),
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_customer(customer_id: str) -> dict[str, str]:
        """Archive a customer (reversible with `restore_customer`). Fails if it has active deals."""
        return customers.archive(context, customer_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_customer(customer_id: str) -> dict[str, Any]:
        """Restore an archived customer."""
        return customers.restore(context, customer_id)
