"""§10's fourth prompt, and the only one that embeds a resource.

It is the only one because it is the only one whose resource **already exists**:
`organization://{id}`, from slice 1 §8.4. A resource block needs a URI, and there is no URI for
"the commercial dashboard for March" that would not be a new resource invented in order to
have one -- which §11.1 explicitly declines.

**The text block carries only what the card does not.** `render_organization` already renders
the fiscal identity, the contacts, the deals and the timeline, and an `EmbeddedResource`
must carry the resource's text -- `TextResourceContents.text` is required by the protocol
types, so the card is in the message either way. Repeating the deal list beside it would
double the section that costs the most and add nothing a reader could not already see, which
is exactly the tax §10 is about. What the card has no notion of is money owed, so that is
what the text block is: the unpaid invoices, and the posture.
"""

from typing import Any
from uuid import UUID

from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.resources import entities

# The cap, for the reason `prompts/dashboards.py` gives beside its own two: this list is
# rendered into the model's context window on every call.
UNPAID_INVOICES_SHOWN = 15


def status_organization(context: McpContext, organization_id: str) -> list[dict[str, Any]]:
    """The briefing before a phone call.

    The card is rendered before anything else so an unknown id is a `NotFound` the guard
    renders as a sentence, rather than an empty briefing about nobody -- which would read as
    "this organization has nothing open" and is the more dangerous of the two answers.
    """
    identifier = UUID(organization_id)
    card = entities.render_organization(context, identifier)

    # One row past the cap, and then discarded: it is asked for only so the prompt can say
    # that it truncated. A briefing that omits its tail silently cannot be told apart from a
    # short register, which is the reading that gets somebody told an organization owes less than
    # they do.
    fetched = InvoiceService(context.session, context.storage).unpaid_for_customer(
        identifier, context.actor, limit=UNPAID_INVOICES_SHOWN + 1
    )
    unpaid = fetched[:UNPAID_INVOICES_SHOWN]
    truncated = len(fetched) > UNPAID_INVOICES_SHOWN

    lines = ["## Unpaid invoices", ""]
    if not unpaid:
        lines.append("No invoices to collect.")
    else:
        for row in unpaid:
            invoice = row.invoice
            # A draft that was issued always has both `year` and `number`
            # (`ck_invoices_year_number_together`), but the pair is nullable on the column,
            # so it is rendered defensively rather than assumed.
            number = (
                f"{invoice.year}/{invoice.number}" if invoice.number is not None else "no number"
            )
            due_date = row.due_date or "no due date"
            left = str(row.remaining).replace(".", ",")
            if row.remaining == invoice.total:
                lines.append(f"- {number} — {left} € (total incl. VAT), due {due_date}")
            else:
                # Partly paid: what is still owed, and the document's total beside it.
                total = str(invoice.total).replace(".", ",")
                lines.append(
                    f"- {number} — {left} € still owed of {total} € (total incl. VAT), "
                    f"due {due_date}"
                )
        if truncated:
            lines += [
                "",
                f"_Showing the {UNPAID_INVOICES_SHOWN} with the nearest due date; there are "
                "more. The full list is `list_invoices`._",
            ]
    lines += [
        "",
        "---",
        "",
        "The organization's full record — tax data, contacts, deals and timeline — is "
        "attached as a resource: read it there, it is not repeated here. Prepare the briefing "
        "for a phone call: what is open, what is overdue, and the two or three questions to "
        "ask. Unpaid invoices are receivables including VAT, not revenue. Do not suggest "
        "sending reminders: just tell me what is there.",
    ]
    return [
        {"role": "user", "content": {"type": "text", "text": "\n".join(lines)}},
        {
            "role": "user",
            "content": {
                "type": "resource",
                "resource": {
                    "uri": f"organization://{identifier}",
                    # Required by `TextResourceContents`, and the reason the resource block
                    # *is* the context rather than a pointer to it: a client that cannot
                    # follow the URI still has the card, so "the context arrives inside the
                    # prompt" holds without the model having to go and fetch anything.
                    "text": card,
                    "mimeType": "text/markdown",
                },
            },
        },
    ]
