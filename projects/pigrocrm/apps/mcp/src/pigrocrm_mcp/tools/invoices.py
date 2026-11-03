"""Thin calls into `InvoiceService`, plus the declaration of what MCP deliberately
cannot reach.

An agent may **prepare**. It may not emit. See `apps/mcp/tests/test_mcp_invoice_ban.py`
for the three reasons, and note that the mechanism is the absence of a tool rather than
an authorisation check: R10 is open, so a PAT inherits the owner's full role and an
administrative token would pass any check written here.
"""

from datetime import date
from typing import Any, Final
from uuid import UUID

from pigrocrm.core.errors import Conflict
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import (
    InvoiceCreate,
    InvoiceLineIn,
    InvoiceListQuery,
    InvoiceRead,
    InvoiceUpdate,
    PaymentState,
    PaymentStatus,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import (
    TEMPLATE_EXCLUDED_FIELDS,
    LegalEntityCreate,
    LegalEntityRead,
    LegalEntityUpsert,
)
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.changes import refuse_nothing_given, report
from pigrocrm_mcp.tools.params import iso_date, literal_arg, parse_legal_entity_id

# Three tables used to live here: which operations are forbidden, which public methods
# are simply unexposed and why, and which method backs which tool name. They are gone,
# and their content lives in `apps/mcp/tests/test_mcp_surface_coverage.py`, keyed by
# `(service class, method)` and covering every service rather than these two.
#
# Not a tidying. They claimed to be enforced -- "asserted to be exactly these four names
# by the ban test" -- and nothing imported them: no test read a single one, so all three
# had already drifted. `MCP_FORBIDDEN_OPERATIONS` named `update_fiscal_profile` while the
# ban test's own list did not; `MCP_UNEXPOSED_OPERATIONS` was keyed on bare method names,
# so its `get` and `update` entries silently spoke for a dozen services that also define
# one. Documentation that describes a guarantee nobody checks is worse than none: it
# reads exactly like the guarantee.
#
# The `update_fiscal_profile` disagreement was first settled in the ban's favour (slice
# 3 §11's four names include it, banned as the `(FiscalProfileService, upsert)` pair
# because `LegalEntityService` has an `upsert` too), then reopened by ORB-188
# (2026-09-12): the profile is a total replacement that can be replaced again, an issued
# invoice keeps its own copy, and in a space born empty «imposta il mio profilo fiscale»
# is the first thing a person asks their assistant. So the write is on the default
# surface below, next to the read, admin-only through the service; and the emitter's
# identity, which had no tool under any switch, is written the same way. What stays
# behind `mcp_full_access` is what cannot be undone: issuing, annulling, the register.


def _invoices(context: McpContext) -> InvoiceService:
    return InvoiceService(context.session, context.storage)


def search(context: McpContext, query: InvoiceListQuery) -> dict[str, Any]:
    page = _invoices(context).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "next_cursor": str(page.next_cursor) if page.next_cursor else None,
    }


def list_register_gaps(
    context: McpContext, year: int, legal_entity_id: str | None = None
) -> list[dict[str, Any]]:
    """Every table row is a fact somebody already declared, never a computation: this
    reads `register_gaps` (the same query `GET /api/invoices/register/{anno}/gaps`
    answers), it writes nothing, and it needs nothing an installation has to open --
    unlike `import_issued_invoice`/`declare_invoice_register_gaps`, which write the
    register and stay behind `mcp_full_access`. One register per legal entity (REB-619):
    none named is the default's."""
    gaps = _invoices(context).register_gaps(
        year, context.actor, parse_legal_entity_id(legal_entity_id)
    )
    return [g.model_dump(mode="json") for g in gaps]


def get(context: McpContext, invoice_id: str) -> dict[str, Any]:
    service = _invoices(context)
    invoice = service.get(UUID(invoice_id), context.actor)
    return {
        **invoice.model_dump(mode="json"),
        "lines": [
            line.model_dump(mode="json") for line in service.lines(UUID(invoice_id), context.actor)
        ],
    }


def create_proforma(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    """`type` is forced to `proforma` here rather than taken from the caller: this is
    the only creation an agent performs, and letting it choose would put a draft
    invoice -- one button away from a consumed number -- on the agentic surface."""
    payload = {**data, "type": "proforma"}
    return (
        _invoices(context).create(InvoiceCreate(**payload), context.actor).model_dump(mode="json")
    )


def _require_proforma(
    service: InvoiceService, invoice_id: UUID, context: McpContext
) -> InvoiceRead:
    invoice = service.get(invoice_id, context.actor)
    if invoice.type != "proforma":
        raise Conflict(
            "invoice",
            "over MCP only proformas can be edited: an invoice is prepared and issued by a person",
            type=invoice.type,
            status=invoice.status,
        )
    return invoice


def replace_proforma_lines(
    context: McpContext, invoice_id: str, lines: list[dict[str, Any]]
) -> dict[str, Any]:
    service = _invoices(context)
    identifier = UUID(invoice_id)
    _require_proforma(service, identifier, context)
    return service.replace_lines(
        identifier, [InvoiceLineIn(**line_data) for line_data in lines], context.actor
    ).model_dump(mode="json")


def update_proforma(
    context: McpContext,
    invoice_id: str,
    *,
    memo: str | None,
    issue_date: date | None,
    due_date: date | None = None,
    accrual_from: date | None,
    accrual_to: date | None,
    purchase_order_number: str | None = None,
    purchase_order_date: date | None = None,
    purchase_order_tender_code: str | None = None,
    purchase_order_project_code: str | None = None,
) -> dict[str, Any]:
    """The header of a proforma: reason, document date, due date, accrual period,
    purchase order (ORB-61, ORB-63, REB-326, REB-693).

    Same guard as `replace_proforma_lines`, and for the same reason: a proforma is the
    only document an agent shapes from here, and the service's own `ImmutableField`
    refuses a `consumed` one after the guard has let its `type` through. `None` means
    "not supplied" on this surface, never "clear it": only the keys with a value reach
    `InvoiceUpdate`, so an agent cannot blank a period or a date by omission. Clearing a
    period is the application's, where the two ends are one control.
    """
    refuse_nothing_given(
        memo=memo,
        issue_date=issue_date,
        due_date=due_date,
        accrual_from=accrual_from,
        accrual_to=accrual_to,
        purchase_order_number=purchase_order_number,
        purchase_order_date=purchase_order_date,
        purchase_order_tender_code=purchase_order_tender_code,
        purchase_order_project_code=purchase_order_project_code,
    )
    service = _invoices(context)
    identifier = UUID(invoice_id)
    before = _require_proforma(service, identifier, context)
    supplied: dict[str, Any] = {
        key: value
        for key, value in {
            "memo": memo,
            "issue_date": issue_date,
            "due_date": due_date,
            "accrual_from": accrual_from,
            "accrual_to": accrual_to,
            "purchase_order_number": purchase_order_number,
            "purchase_order_date": purchase_order_date,
            "purchase_order_tender_code": purchase_order_tender_code,
            "purchase_order_project_code": purchase_order_project_code,
        }.items()
        if value is not None
    }
    # `model_validate` on the filtered dict rather than `InvoiceUpdate(**supplied)`: the
    # keys that are absent must stay *unset*, which is what lets `supplied_changes` tell
    # "not passed" from "clear it" -- and a keyword argument defaulted to `None` would be
    # set to `None`, which on this schema means clear.
    changes = InvoiceUpdate.model_validate(supplied)
    return report(before, service.update(identifier, changes, context.actor))


def confirm_proforma(context: McpContext, invoice_id: str) -> dict[str, Any]:
    """`draft` -> `confirmed` on a proforma (ORB-132): the amount is agreed and the
    document is ready to be issued, still without touching the register.

    Same guard as `replace_proforma_lines`, so an invoice is refused with the reason before
    the service refuses it for its own. The service then refuses a proforma that is not a
    draft and one without lines, naming the field. On the default surface and not behind
    `mcp_full_access`: confirming consumes nothing and forecloses nothing, since a
    confirmed proforma stays editable (`_is_editable`: `update_proforma`,
    `replace_proforma_lines`) and discardable. There is no way back to `draft`, and none
    is needed. `issue_invoice`, which turns the confirmed proforma into a numbered
    document, stays privileged.
    """
    service = _invoices(context)
    identifier = UUID(invoice_id)
    _require_proforma(service, identifier, context)
    return service.confirm_proforma(identifier, context.actor).model_dump(mode="json")


def discard_proforma(context: McpContext, invoice_id: str) -> dict[str, Any]:
    """The same guard as `replace_proforma_lines`, for the same reason: from the MCP a
    proforma is the only document an agent shapes, and discarding one it got wrong is the
    natural end of that shaping. `discard`, not `delete`: `test_no_destructive_delete_tool_
    exists` keeps that prefix off the surface, and this is the application's soft delete,
    the row stays. `InvoiceService.soft_delete` refuses on its own anything
    that consumed a number, and the table CHECK behind it refuses again; the guard here
    only makes the refusal for an invoice say why before either of those is reached.

    One-way, unlike the `archive_*` tools: `InvoiceService` has no restore, and none is
    owed to a document that never took a number and can be recreated from what
    `get_invoice` showed. The server's INSTRUCTIONS name it as the one exception."""
    service = _invoices(context)
    identifier = UUID(invoice_id)
    _require_proforma(service, identifier, context)
    service.soft_delete(identifier, context.actor)
    return {"status": "scartata", "invoice_id": invoice_id}


def render_proforma_pdf(context: McpContext, invoice_id: str) -> dict[str, Any]:
    service = _invoices(context)
    identifier = UUID(invoice_id)
    _require_proforma(service, identifier, context)
    artifacts = service.produce_artifacts(identifier, context.actor)
    # A proforma always yields exactly one artefact (the PDF: `produce_artifacts`
    # only appends the XML for an issued fattura), but `next(... if a.kind == "pdf")`
    # reads correctly even if that invariant ever changes, rather than assuming
    # `artifacts[0]` is the PDF.
    artifact = next(a for a in artifacts if a.kind == "pdf")
    return {
        **artifact.model_dump(mode="json"),
        # An identifier and a URL, never the bytes: a base64 PDF inside a model's own
        # context is waste and risk (slice 2 §7).
        "download_url": f"/api/invoices/{invoice_id}/pdf",
    }


def xml_url(context: McpContext, invoice_id: str) -> dict[str, Any]:
    """The URL of an already-produced XML. Never the bytes, and never a production:
    producing one requires an issued invoice, and MCP does not issue."""
    invoice = _invoices(context).get(UUID(invoice_id), context.actor)
    if invoice.xml_document_id is None:
        raise Conflict(
            "invoice",
            "this invoice has no XML file yet: it must be produced by the application",
            status=invoice.status,
        )
    return {
        "invoice_id": invoice_id,
        "number": f"{invoice.year}/{invoice.number}",
        "download_url": f"/api/invoices/{invoice_id}/xml",
        "hash_sha256": invoice.xml_hash_sha256,
    }


def set_payment_state(
    context: McpContext, invoice_id: str, payment_status: str, payment_date: str | None
) -> dict[str, Any]:
    return (
        _invoices(context)
        .set_payment_state(
            UUID(invoice_id),
            PaymentState(
                payment_status=literal_arg("payment_status", payment_status, PaymentStatus),
                payment_date=iso_date(payment_date, "payment_date"),
            ),
            context.actor,
        )
        .model_dump(mode="json")
    )


def describe_fiscal_profile(
    context: McpContext, legal_entity_id: str | None = None
) -> dict[str, Any]:
    """The fiscal parameters of one legal entity; none means the default, which on a space
    with one legal entity is the only one (REB-616, spec 2026-10-03 §7)."""
    return FiscalProfileService(context.session).describe(
        context.actor, parse_legal_entity_id(legal_entity_id)
    )


def update_fiscal_profile(
    context: McpContext, data: dict[str, Any], legal_entity_id: str | None = None
) -> dict[str, Any]:
    """The whole profile, replaced (ORB-188). `require_admin` inside the service is the
    only gate, and it is enough: the row is rewritable, issued invoices keep their copy."""
    return (
        FiscalProfileService(context.session)
        .upsert(
            FiscalProfileUpsert.model_validate(data),
            context.actor,
            parse_legal_entity_id(legal_entity_id),
        )
        .model_dump(mode="json")
    )


# What an agent needs to pick a legal entity, and nothing it does not: the two flags, the
# nation and the three names (REB-616).
LEGAL_ENTITY_FIELDS: Final[set[str]] = {
    "id",
    "name",
    "legal_name",
    "vat_number",
    "country",
    "is_default",
    "active",
}


# What `describe_legal_entity` adds beyond the row and `update_legal_entity` ignores on the way
# back.
READ_ONLY_FLAGS: Final[frozenset[str]] = frozenset({"has_logo", "has_signature"})


def suggest_legal_entity(context: McpContext, country: str | None) -> dict[str, Any]:
    """The legal entity `LegalEntityService.propose` picks for a customer of `country`
    (REB-624, spec 2026-10-03 §1.6), in the shape `list_legal_entities` answers."""
    row = LegalEntityService(context.session).propose(country)
    return LegalEntityRead.model_validate(row).model_dump(mode="json", include=LEGAL_ENTITY_FIELDS)


def list_legal_entities(context: McpContext) -> list[dict[str, Any]]:
    """Every active legal entity of the space, the default first: id, short name, legal
    name, country and the two flags, which is what an agent needs to pick one."""
    return [
        a.model_dump(mode="json", include=LEGAL_ENTITY_FIELDS)
        for a in LegalEntityService(context.session).list(context.actor)
    ]


def describe_legal_entity(
    context: McpContext, legal_entity_id: str | None = None
) -> dict[str, Any]:
    """Who the invoices and the documents say they come from.

    `LegalEntityService.get` is one of the two reads in this product deliberately left
    un-role-gated at the service layer, because the PDF header needs it for every
    role -- so there is no role for which this is agent-only knowledge. The logo and the
    signature appear as `has_logo` and `has_signature`, since REB-627: the files are uploaded
    over the REST API and their keys are the server's. The write on the same row is
    `update_legal_entity` (ORB-188). Without `id`, the two state flags, the timestamps and the
    two image flags, what this returns can be handed back to the write unchanged:
    `LegalEntityUpsert` forbids extra keys, and the round trip the write's docstring
    prescribes («leggi prima, rimanda indietro l'oggetto») has to be possible.
    """
    profile = LegalEntityService(context.session).get(
        context.actor, parse_legal_entity_id(legal_entity_id)
    )
    # Whether the two images are set, never the storage keys (REB-627): the keys are
    # the server's, `LegalEntityUpsert` no longer takes them, and an agent reading this to
    # hand it back to `update_legal_entity` must get an object the write accepts.
    return {
        **profile.model_dump(
            mode="json", exclude={*TEMPLATE_EXCLUDED_FIELDS, "logo_key", "signature_key"}
        ),
        "has_logo": profile.logo_key is not None,
        "has_signature": profile.signature_key is not None,
    }


def create_legal_entity(
    context: McpContext, data: dict[str, Any], tax_profile: dict[str, Any]
) -> dict[str, Any]:
    """A second legal entity with its fiscal profile, in one call (REB-631, spec 2026-10-03
    §7): `data` in `LegalEntityCreate`'s shape, `tax_profile` in `FiscalProfileUpsert`'s,
    the two bodies the REST route takes as one. Admin-only through the service, and
    agent-allowed like `update_legal_entity` since ORB-188: setting a space up is not a
    fiscal act. The two image flags are dropped as `update_legal_entity` drops them, so an
    agent that copies `describe_legal_entity`'s answer as a starting point is not refused."""
    payload = LegalEntityCreate.model_validate(
        {
            **{key: value for key, value in data.items() if key not in READ_ONLY_FLAGS},
            "fiscal_profile": tax_profile,
        }
    )
    return (
        LegalEntityService(context.session).create(payload, context.actor).model_dump(mode="json")
    )


def update_legal_entity(
    context: McpContext, data: dict[str, Any], legal_entity_id: str | None = None
) -> dict[str, Any]:
    """One legal entity's row, replaced (ORB-188): legal name, VAT number or tax
    code, address, PEC, SDI code, contacts. Admin-only through the service, which
    also checks the VAT number and the SDI code by shape. With no `legal_entity_id` it is
    the default legal entity, created on the spot when the space has none yet: the first
    thing a person asks the assistant they just connected is to set it (REB-188), and
    a prompt written for a one-legal entity space keeps working."""
    # Spelled as `LegalEntityService(...)` on each call rather than bound to a local:
    # `test_mcp_surface_coverage.py` resolves receivers per function, so a local would be
    # read too; this stays as written because it reads the same either way.
    # The two image flags `describe_legal_entity` answers are read-only facts, not columns:
    # dropped here so the read can be handed back whole, as the tool's own docstring
    # prescribes, without `LegalEntityUpsert` refusing them as extra keys.
    payload = LegalEntityUpsert.model_validate(
        {key: value for key, value in data.items() if key not in READ_ONLY_FLAGS}
    )
    target = parse_legal_entity_id(legal_entity_id)
    if target is None:
        return (
            LegalEntityService(context.session)
            .upsert_default(payload, context.actor)
            .model_dump(mode="json")
        )
    return (
        LegalEntityService(context.session)
        .update(target, payload, context.actor)
        .model_dump(mode="json")
    )
