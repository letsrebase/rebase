"""§10's fourth prompt, and the only one that embeds a resource.

It is the only one because it is the only one whose resource **already exists**:
`customer://{id}`, from slice 1 §8.4. A resource block needs a URI, and there is no URI for
"the commercial dashboard for March" that would not be a new resource invented in order to
have one -- which §11.1 explicitly declines.

**The text block carries only what the card does not.** `render_customer` already renders
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


def stato_cliente(context: McpContext, customer_id: str) -> list[dict[str, Any]]:
    """The briefing before a phone call.

    The card is rendered before anything else so an unknown id is a `NotFound` the guard
    renders as a sentence, rather than an empty briefing about nobody -- which would read as
    "this customer has nothing open" and is the more dangerous of the two answers.
    """
    identifier = UUID(customer_id)
    card = entities.render_customer(context, identifier)

    # One row past the cap, and then discarded: it is asked for only so the prompt can say
    # that it truncated. A briefing that omits its tail silently cannot be told apart from a
    # short register, which is the reading that gets somebody told a customer owes less than
    # they do.
    fetched = InvoiceService(context.session, context.storage).unpaid_for_customer(
        identifier, context.actor, limit=UNPAID_INVOICES_SHOWN + 1
    )
    unpaid = fetched[:UNPAID_INVOICES_SHOWN]
    troncato = len(fetched) > UNPAID_INVOICES_SHOWN

    lines = ["## Fatture non incassate", ""]
    if not unpaid:
        lines.append("Nessuna fattura da incassare.")
    else:
        for invoice in unpaid:
            # A draft that was issued always has both `anno` and `numero`
            # (`ck_invoices_anno_numero_together`), but the pair is nullable on the column,
            # so it is rendered defensively rather than assumed.
            numero = (
                f"{invoice.anno}/{invoice.numero}" if invoice.numero is not None else "senza numero"
            )
            scadenza = invoice.data_scadenza or "senza scadenza"
            totale = str(invoice.totale).replace(".", ",")
            lines.append(f"- {numero} — {totale} € (totale con IVA), scadenza {scadenza}")
        if troncato:
            lines += [
                "",
                f"_Mostrate le {UNPAID_INVOICES_SHOWN} con scadenza più vicina; ce ne sono "
                "altre. L'elenco completo è `list_invoices`._",
            ]
    lines += [
        "",
        "---",
        "",
        "La scheda completa del cliente — dati fiscali, contatti, deal e timeline — è "
        "allegata come risorsa: leggila lì, non è ripetuta qui. Preparami il briefing per "
        "una telefonata: cosa è aperto, cosa è in ritardo, e le due o tre domande da fare. "
        "Le fatture non incassate sono crediti con IVA, non ricavi. Non proporre di mandare "
        "solleciti: dimmi solo cosa c'è.",
    ]
    return [
        {"role": "user", "content": {"type": "text", "text": "\n".join(lines)}},
        {
            "role": "user",
            "content": {
                "type": "resource",
                "resource": {
                    "uri": f"customer://{identifier}",
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
