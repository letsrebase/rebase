"""Thin calls into `InvoiceService`, plus the declaration of what MCP deliberately
cannot reach.

An agent may **prepare**. It may not emit. See `apps/mcp/tests/test_mcp_invoice_ban.py`
for the three reasons, and note that the mechanism is the absence of a tool rather than
an authorisation check: R10 is open, so a PAT inherits the owner's full role and an
administrative token would pass any check written here.
"""

from datetime import date
from typing import Any
from uuid import UUID

from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
from pigrocrm.core.emitter.service import EmitterProfileService
from pigrocrm.core.errors import Conflict
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import (
    InvoiceCreate,
    InvoiceLineIn,
    InvoiceListQuery,
    InvoiceUpdate,
    PaymentState,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm_mcp.context import McpContext

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
# because `EmitterProfileService` has an `upsert` too), then reopened by ORB-188
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


def list_register_gaps(context: McpContext, anno: int) -> list[dict[str, Any]]:
    """Every table row is a fact somebody already declared, never a computation: this
    reads `register_gaps` (the same query `GET /api/invoices/register/{anno}/gaps`
    answers), it writes nothing, and it needs nothing an installation has to open --
    unlike `import_issued_invoice`/`declare_invoice_register_gaps`, which write the
    register and stay behind `mcp_full_access`."""
    return [
        g.model_dump(mode="json") for g in _invoices(context).register_gaps(anno, context.actor)
    ]


def get(context: McpContext, invoice_id: str) -> dict[str, Any]:
    service = _invoices(context)
    invoice = service.get(UUID(invoice_id), context.actor)
    return {
        **invoice.model_dump(mode="json"),
        "righe": [
            line.model_dump(mode="json") for line in service.lines(UUID(invoice_id), context.actor)
        ],
    }


def create_proforma(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    """`tipo` is forced to `proforma` here rather than taken from the caller: this is
    the only creation an agent performs, and letting it choose would put a draft
    invoice -- one button away from a consumed number -- on the agentic surface."""
    payload = {**data, "tipo": "proforma"}
    return (
        _invoices(context).create(InvoiceCreate(**payload), context.actor).model_dump(mode="json")
    )


def _require_proforma(service: InvoiceService, invoice_id: UUID, context: McpContext) -> None:
    invoice = service.get(invoice_id, context.actor)
    if invoice.tipo != "proforma":
        raise Conflict(
            "invoice",
            "da MCP si modificano solo le proforma: una fattura la prepara e la emette una persona",
            tipo=invoice.tipo,
            stato=invoice.stato,
        )


def replace_proforma_lines(
    context: McpContext, invoice_id: str, righe: list[dict[str, Any]]
) -> dict[str, Any]:
    service = _invoices(context)
    identifier = UUID(invoice_id)
    _require_proforma(service, identifier, context)
    return service.replace_lines(
        identifier, [InvoiceLineIn(**riga) for riga in righe], context.actor
    ).model_dump(mode="json")


def update_proforma(
    context: McpContext,
    invoice_id: str,
    *,
    causale: str | None,
    data_emissione: date | None,
    data_scadenza: date | None = None,
    competenza_da: date | None,
    competenza_a: date | None,
) -> dict[str, Any]:
    """The header of a proforma: causale, document date, due date, accrual period
    (ORB-61, ORB-63, REB-326).

    Same guard as `replace_proforma_lines`, and for the same reason: a proforma is the
    only document an agent shapes from here, and the service's own `ImmutableField`
    refuses a `consumata` one after the guard has let its `tipo` through. `None` means
    "not supplied" on this surface, never "clear it": only the keys with a value reach
    `InvoiceUpdate`, so an agent cannot blank a period or a date by omission. Clearing a
    period is the application's, where the two ends are one control.
    """
    service = _invoices(context)
    identifier = UUID(invoice_id)
    _require_proforma(service, identifier, context)
    supplied: dict[str, Any] = {
        key: value
        for key, value in {
            "causale": causale,
            "data_emissione": data_emissione,
            "data_scadenza": data_scadenza,
            "competenza_da": competenza_da,
            "competenza_a": competenza_a,
        }.items()
        if value is not None
    }
    # `model_validate` on the filtered dict rather than `InvoiceUpdate(**supplied)`: the
    # keys that are absent must stay *unset*, which is what lets `supplied_changes` tell
    # "not passed" from "clear it" -- and a keyword argument defaulted to `None` would be
    # set to `None`, which on this schema means clear.
    changes = InvoiceUpdate.model_validate(supplied)
    return service.update(identifier, changes, context.actor).model_dump(mode="json")


def confirm_proforma(context: McpContext, invoice_id: str) -> dict[str, Any]:
    """`bozza` -> `confermata` on a proforma (ORB-132): the amount is agreed and the
    document is ready to be issued, still without touching the register.

    Same guard as `replace_proforma_lines`, so a fattura is refused with the reason before
    the service refuses it for its own. The service then refuses a proforma that is not a
    draft and one without lines, naming the field. On the default surface and not behind
    `mcp_full_access`: confirming consumes nothing and forecloses nothing, since a
    confirmed proforma stays editable (`_is_editable`: `update_proforma`,
    `replace_proforma_lines`) and discardable. There is no way back to `bozza`, and none
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
    only makes the refusal for a fattura say why before either of those is reached.

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
            "questa fattura non ha ancora un file XML: va prodotto dall'applicazione",
            stato=invoice.stato,
        )
    return {
        "invoice_id": invoice_id,
        "numero": f"{invoice.anno}/{invoice.numero}",
        "download_url": f"/api/invoices/{invoice_id}/xml",
        "hash_sha256": invoice.xml_hash_sha256,
    }


def set_payment_state(
    context: McpContext, invoice_id: str, stato_pagamento: str, data_incasso: str | None
) -> dict[str, Any]:
    return (
        _invoices(context)
        .set_payment_state(
            UUID(invoice_id),
            PaymentState(
                stato_pagamento=stato_pagamento,  # type: ignore[arg-type]
                data_incasso=date.fromisoformat(data_incasso) if data_incasso else None,
            ),
            context.actor,
        )
        .model_dump(mode="json")
    )


def describe_fiscal_profile(context: McpContext) -> dict[str, Any]:
    return FiscalProfileService(context.session).describe(context.actor)


def update_fiscal_profile(context: McpContext, dati: dict[str, Any]) -> dict[str, Any]:
    """The whole profile, replaced (ORB-188). `require_admin` inside the service is the
    only gate, and it is enough: the row is rewritable, issued invoices keep their copy."""
    return (
        FiscalProfileService(context.session)
        .upsert(FiscalProfileUpsert.model_validate(dati), context.actor)
        .model_dump(mode="json")
    )


def update_emitter_profile(context: McpContext, dati: dict[str, Any]) -> dict[str, Any]:
    """The one emitter row, replaced (ORB-188): ragione sociale, partita IVA or codice
    fiscale, address, PEC, codice SDI, contacts. Admin-only through the service, which
    also checks the partita IVA and the codice SDI by shape."""
    return (
        EmitterProfileService(context.session)
        .upsert(EmitterProfileUpsert.model_validate(dati), context.actor)
        .model_dump(mode="json")
    )


def describe_emitter_profile(context: McpContext) -> dict[str, Any]:
    """Who the invoices and the documents say they come from.

    `EmitterProfileService.get` is one of the two reads in this product deliberately
    left un-role-gated at the service layer, because the PDF header needs it for every
    role -- so there is no role for which this is agent-only knowledge. `logo_key` and
    `firma_key` are storage keys, not bytes, exactly like every other identifier this
    surface returns; the write on the same row is `update_emitter_profile` (ORB-188).
    Without `id` and the timestamps, so that what this returns can be handed back to
    the write unchanged: `EmitterProfileUpsert` forbids extra keys, and the round trip
    the write's docstring prescribes («leggi prima, rimanda indietro l'oggetto») has to
    be possible. Same exclusion `as_template_values` and the fiscal `describe` apply.
    """
    return (
        EmitterProfileService(context.session)
        .get(context.actor)
        .model_dump(mode="json", exclude={"id", "created_at", "updated_at"})
    )
