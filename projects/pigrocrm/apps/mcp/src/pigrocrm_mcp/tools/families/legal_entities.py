"""Members, aziende (the emitters) and the fiscal profile."""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.auth.service import UserService
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    invoices,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # The fiscal profile and the emitter are written here, on the default surface
    # (ORB-188, 2026-09-12). The line this surface draws is not "reads yes, writes no":
    # it is "what cannot be undone stays behind the switch". A profile or an emitter is
    # one rewritable row; an issued invoice keeps its own copy of both. A space is born
    # empty and the first thing a person asks the assistant they just connected is to
    # set them, so `update_fiscal_profile` and `update_legal_entity` sit next to their reads,
    # admin-only through the services' own `require_admin`.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_members() -> list[dict[str, Any]]:
        """The active people in the space: `id`, `name`, `role` and `email` (only for an
        admin, otherwise `null`). Use it to find the id for `owner_id` (customers and deals)
        and `assigned_to` (tasks).
        """
        directory = UserService(context.session).directory(context.actor)
        return [member.model_dump(mode="json") for member in directory]

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_legal_entities() -> list[dict[str, Any]]:
        """The legal entities in the space, the default first: id, short name, legal name,
        VAT number, country. With a single legal entity, every tool with `legal_entity_id` uses it
        on its own.
        """
        return invoices.list_legal_entities(context)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_fiscal_profile(legal_entity_id: str | None = None) -> dict[str, Any]:
        """The tax regime and the parameters that decide rates, VAT nature and stamp duty of a
        legal entity;
        `legal_entity_id` omitted: default. Useful to understand why a line has a given VAT.
        """
        return invoices.describe_fiscal_profile(context, legal_entity_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_legal_entity(legal_entity_id: str | None = None) -> dict[str, Any]:
        """Who issues: legal name, VAT number, address and contact details of the header of
        invoices and documents; `legal_entity_id` omitted: default. Read it instead of asking
        the user for this data.
        """
        return invoices.describe_legal_entity(context, legal_entity_id)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_fiscal_profile(
        data: dict[str, Any], legal_entity_id: str | None = None
    ) -> dict[str, Any]:
        """Rewrite the fiscal profile of a legal entity: **full replacement**, every missing key
        goes back to its default. `legal_entity_id` omitted: default. Decides rate, VAT nature,
        stamp duty and legal reference of future invoices, not of those already issued. Read
        `describe_fiscal_profile` first, send back the whole object with the changes and ask the
        person to confirm on the summary. Admin only.

        `data`: `pack_id` (`it-flat-rate` default, flat-rate regime with `regime_code` `RF19`;
        `it-standard`, standard regime with `RF01` and 22% VAT unless you say otherwise; `non-it`,
        foreign company with no `regime_code`, stamp duty or flat-rate parameters (leave them out);
        a code from another pack is rejected), `profitability_coefficient`,
        `substitute_tax_rate` and `social_security_rate` as percentages (flat-rate only),
        `payment_method` (SdI code, `MP05` bank transfer), `due_days`, `iban`, VAT
        and stamp duty parameters. `inps_category` (`gestione_separata` or
        `artigiani_commercianti`) and `isa` (true or false) are left as they are when not sent.
        """
        return invoices.update_fiscal_profile(context, data, legal_entity_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_legal_entity(data: dict[str, Any], tax_profile: dict[str, Any]) -> dict[str, Any]:
        """Open a second legal entity with its fiscal profile in one call: customers can be
        assigned to it, its invoices have their own register and documents carry its
        header. Admin only, after the data is confirmed: once an invoice is issued, a legal entity
        can no longer be deleted, only deactivated. It does not become the default, unless the space
        has none.

        `data` as in `update_legal_entity` with `name` required (short name for the selector and
        lists); `tax_profile` as in `update_fiscal_profile` (`non-it` with zero VAT requires
        `default_vat_nature`). A rejected profile rejects the whole call. Fiscal data
        not provided is never invented: ask for it.
        """
        return invoices.create_legal_entity(context, data, tax_profile)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_legal_entity(
        data: dict[str, Any], legal_entity_id: str | None = None
    ) -> dict[str, Any]:
        """Write who issues: the header of offers and invoices of a legal entity. `legal_entity_id`
        omitted: default, created if missing.

        **Full replacement**: every missing key goes back to empty (`country` to `IT`;
        `legal_name` required). Read `describe_legal_entity` first and send back the object with
        only the changes. Keys: `name`, `legal_name`, `vat_number` (11 digits) or
        `tax_code`, `address`, `postal_code`, `city`, `province`, `country`, `certified_email`,
        `sdi_code` (7 characters), `phone`, `email`, `website`, `tax_regime` (footer text),
        `email_signature`, `logo_key`, `signature_key` (storage keys, not bytes),
        `fiscal_year_end_month` and `fiscal_year_end_day` together (default 12 and 31; left out,
        they keep what the row has). Ask for
        confirmation on the summary; do not invent fiscal data. Admin only.
        """
        return invoices.update_legal_entity(context, data, legal_entity_id)
