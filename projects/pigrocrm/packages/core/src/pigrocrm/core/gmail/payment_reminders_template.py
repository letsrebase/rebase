"""The reminder text, carried from the previous system and de-personalised.

`buildReminderInvoiceEmailBody` (`.reference-*/website/src/App.jsx:2329-2372`) is
text that went to real clients for years, with the right running order: invoice number,
date, due date, amount, IBAN. That is carried, order included, because that order is the
part a recipient's eye already knows how to read.

What is not carried is the signature -- `Mario Rossi / CTO / mobile +39 02 1234567 /
web https://www.example.com`, duplicated verbatim in both of the previous system's builders --
nor the `NOME_CLIENTE` placeholder syntax, which slice 2 already replaced with `{{}}`. The
signature is data now: the free-text block comes from `legal_entities.email_signature`, and
the company name, the phone and the website are read from their own columns rather than
retyped into it, so the number cannot diverge between two places the way it did between
The previous system's two builders.

The reminder level is a **variable**, not three templates. Three templates that resemble
each other diverge; this is the same decision slice 2 made when it refused to duplicate
the offer template. `level_flags` is the whole of the branching, because the template
engine has no comparison operator and giving it one for this is a feature nobody asked
for.

The body renders in the "plain" escaping context, never the default "markdown" one --
see `render_payment_reminder_body`, which is the only supported way to render this source.
"""

from dataclasses import asdict
from typing import Any

from pigrocrm.core.templates.renderer import DeclaredVariable, render_template

PAYMENT_REMINDER_TEMPLATE_NAME = "Sollecito di pagamento"

# `--` on its own line is the RFC 3676 signature delimiter: a mail client that knows the
# convention collapses everything below it, which is exactly the treatment a signature
# wants. Each optional line carries its own leading newline *inside* the `{{#if}}`, so an
# emitter with no phone number leaves no blank line behind rather than an empty gap the
# sender never sees and the client does.
PAYMENT_REMINDER_TEMPLATE_SOURCE = """Gentile {{cliente}},

{{#if primo}}con la presente Le notifichiamo un sollecito di pagamento relativo alla \
fattura indicata di seguito, che risulta non ancora saldata.{{/if}}\
{{#if secondo}}torniamo a scriverLe in merito alla fattura indicata di seguito, che \
risulta ancora non saldata nonostante il precedente sollecito.{{/if}}\
{{#if terzo}}con questo terzo e ulteriore sollecito Le segnaliamo che la fattura \
indicata di seguito risulta ancora non saldata, nonostante i precedenti solleciti.\
{{/if}}

Fattura: {{numero_fattura}}
Data fattura: {{data_fattura}}
Scadenza: {{scadenza}}
Importo: {{importo}}
IBAN: {{iban}}

Qualora avesse già provveduto al pagamento, La preghiamo di ignorare questo messaggio.

{{#if allegato}}In allegato trova copia di cortesia della fattura. {{/if}}L'originale è \
stato trasmesso digitalmente tramite il Sistema di Interscambio (SdI) secondo le \
modalità previste.

Restiamo a disposizione per qualsiasi chiarimento e cogliamo l'occasione per porgere \
cordiali saluti.

--{{#if firma_email}}
{{firma_email}}{{/if}}
{{emittente.ragione_sociale}}{{#if emittente.telefono}}
tel. {{emittente.telefono}}{{/if}}{{#if emittente.sito_web}}
{{emittente.sito_web}}{{/if}}
"""

PAYMENT_REMINDER_DECLARED_VARIABLES: tuple[DeclaredVariable, ...] = (
    DeclaredVariable(name="cliente", label="Cliente", type="text", required=True),
    DeclaredVariable(name="numero_fattura", label="Numero fattura", type="text", required=True),
    DeclaredVariable(name="data_fattura", label="Data fattura", type="text", required=True),
    DeclaredVariable(name="scadenza", label="Scadenza", type="text", required=True),
    DeclaredVariable(name="importo", label="Importo", type="text", required=True),
    DeclaredVariable(name="iban", label="IBAN", type="text", required=True),
    # The three flags are derived from `level` by `level_flags`, never typed by a
    # human -- they are declared anyway, and as optional, because an undeclared name is
    # an *error* to the renderer when the template reads it and every one of the three
    # is read on every render. Optional, not required: exactly one of them is `True` on
    # any given render, so requiring them would fail every reminder ever sent.
    DeclaredVariable(name="primo", label="Primo sollecito", type="checkbox", required=False),
    DeclaredVariable(name="secondo", label="Secondo sollecito", type="checkbox", required=False),
    DeclaredVariable(name="terzo", label="Terzo sollecito", type="checkbox", required=False),
    # Derived from whether there is actually a document to attach, never typed by a
    # human -- the same shape as the three level flags, and declared optional for the
    # same reason. It exists because `GmailRepository.invoice_pdf_version_ids` may
    # legitimately answer `[]` (a proforma converted before the PDF existed, a document
    # since removed), and a letter to a paying client that says «in allegato trova copia
    # di cortesia della fattura» with nothing attached is worse than one that says
    # nothing: it invites them to look for a file that is not there and then to distrust
    # the figure next to it.
    DeclaredVariable(
        name="allegato",
        label="Copia della fattura allegata",
        type="checkbox",
        required=False,
    ),
    # Optional: an emitter profile saved before this column existed has no signature
    # block, and a reminder with no free-text signature is still a correct reminder --
    # `emittente.ragione_sociale` is always there under it.
    DeclaredVariable(name="firma_email", label="Firma", type="text", required=False),
)

# The same declarations as JSONB-storable dicts, for `TemplateService.seed_defaults`.
# Derived, never retyped: two hand-maintained copies of one list is the duplication this
# whole module exists to undo.
PAYMENT_REMINDER_TEMPLATE_VARIABLES: tuple[dict[str, Any], ...] = tuple(
    asdict(variable) for variable in PAYMENT_REMINDER_DECLARED_VARIABLES
)

# Above this, the wording stops escalating. `max_reminders` defaults to 3, and a fourth
# register would have to be a legal threat -- which is not a sentence this project has
# any standing to put in a freelancer's name.
MAX_PAYMENT_REMINDER_LEVEL = 3


def level_flags(level: int) -> dict[str, bool]:
    """`level` -> the three flags the template branches on.

    Exactly one is ever `True`, for every integer: two opening sentences stacked in one
    paragraph is what an overlapping set of conditions produces, and it is the kind of
    thing that only shows up in a client's inbox.
    """
    return {
        "primo": level <= 1,
        "secondo": level == 2,
        "terzo": level >= MAX_PAYMENT_REMINDER_LEVEL,
    }


def render_payment_reminder_body(
    values: dict[str, Any], *, level: int, with_attachment: bool
) -> str:
    """The reminder body, as the plain text a mail client will show.

    The only supported way to render `PAYMENT_REMINDER_TEMPLATE_SOURCE`. Three things it makes
    unforgettable, all of which are wrong-in-the-client's-inbox rather than
    wrong-in-a-test if a caller open-codes `render_template` instead:

    * the "plain" context. The default "markdown" one escapes the full ASCII
      punctuation class, so `2026/14` reaches a paying client as `2026\\/14`.
    * the level flags. `values` carries the invoice's own frozen figures; the tone of
      the sequence is derived here from `level`, so no caller can send a third
      reminder wearing the wording of a first.
    * the attachment sentence. `with_attachment` is a **required** keyword and has no
      default on purpose: the honest default would be `False`, which silently drops a
      sentence a caller meant to keep, and the convenient default would be `True`, which
      is precisely the defect -- a body promising a courtesy copy of the invoice while
      `attachment_version_ids` is empty. A caller that has to say which one it is cannot
      get it wrong by forgetting.
    """
    return render_template(
        PAYMENT_REMINDER_TEMPLATE_SOURCE,
        {**values, **level_flags(level), "allegato": with_attachment},
        PAYMENT_REMINDER_DECLARED_VARIABLES,
        context="plain",
    )
