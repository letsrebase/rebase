"""What an engagement letter or a framework agreement prints, as pure functions: the payment term
written from the engagement's rule, the fee and the dates as a contract says them, the front
matter of a text, and the one-time key map that turned the hub's texts into templates of this
module (collaborators design § 1, «The documents»).

The field rules themselves (a fee above zero, a payment term within the limit of law 81/2017
art. 3, the dates, `payment_rule` and `collection_days` from 1 to 60) are `rules.py`'s: the
engagement is refused on them when it is written, and `payment_term` asks them again here, so a
letter can never print a term the law does not allow. Nothing in this module reads a session or
runs a binary.

Italian is the product copy: month names, `fattura`, `ricevuta`, and the sentence of the
pay-after-the-client clause (REB-979), moved unchanged from the hub.
"""

import datetime as dt
import re
from decimal import Decimal
from typing import Final

from pigrocrm.core.collaborators import rules
from pigrocrm.core.collaborators.models import PAYMENT_DAYS_LIMIT
from pigrocrm.core.errors import ValidationFailed

ENTITY = "letter"

MONTHS = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)

FRAMEWORK_AGREEMENT: Final = "framework_agreement"
ENGAGEMENT_LETTER: Final = "engagement_letter"
LETTER_TEMPLATE_TYPES: tuple[str, ...] = (FRAMEWORK_AGREEMENT, ENGAGEMENT_LETTER)


def italian(number: Decimal, places: int) -> str:
    """`1234.5` as `1.234,50`: a dot between thousands, a comma before the decimals."""
    text = f"{number:,.{places}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def italian_money(amount: Decimal) -> str:
    """`450` as `450,00 €`: the fee as the letter prints it."""
    return f"{italian(amount, 2)} €"


def italian_date(day: dt.date) -> str:
    """`2026-10-01` as `1° ottobre 2026`: the ordinal on the first of the month, the way a
    contract writes it, the plain number on every other day."""
    number = "1°" if day.day == 1 else str(day.day)
    return f"{number} {MONTHS[day.month - 1]} {day.year}"


def collection_term(days: int, paid_on: str) -> str:
    """The payment term of a letter that waits for the client (REB-979), moved unchanged: the
    freelancer is paid `days` days after the client has paid rebase for the same work, and in any
    case within 60 days of receiving the invoice or receipt."""
    unit = "giorno" if days == 1 else "giorni"
    return (
        f"{days} {unit} dopo che il Cliente ha saldato rebase per lo stesso lavoro, e in ogni"
        f" caso entro {PAYMENT_DAYS_LIMIT} giorni dal ricevimento della {paid_on}"
    )


def payment_term(
    rule: str,
    payment_days: int | None,
    end_of_month: bool,
    collection_days: int | None,
    *,
    receipt: bool,
) -> str:
    """The sentence of the «Pagamento» row. The rule is checked first (`rules.check_payment_rule`:
    the 60 days of law 81/2017 art. 3, 30 when counted from the end of the month, 1 to 60 for the
    wait on the client), so a term over the limit is refused here and never printed. `receipt` is
    an occasional letter, paid on a ricevuta and not on a fattura."""
    checked = rules.check_payment_rule(rule, payment_days, end_of_month, collection_days)
    paid_on = "ricevuta" if receipt else "fattura"
    if checked.collection_days is not None:
        return collection_term(checked.collection_days, paid_on)
    assert checked.payment_days is not None
    return f"{checked.payment_days} giorni data {paid_on}" + (
        " fine mese" if checked.payment_end_of_month else ""
    )


def days_per_week_words(days: Decimal) -> str:
    """`2.5` as `2,5 giorni a settimana`, `1` as `1 giorno a settimana`."""
    number = format(days.normalize(), "f").replace(".", ",")
    return f"{number} {'giorno' if days == 1 else 'giorni'} a settimana"


FEE_UNIT_PHRASE = {"day": "al giorno", "hour": "all'ora", "flat": "a corpo"}
FEE_MODE_PHRASE = {"day": "a giornata", "hour": "a ore", "flat": "a corpo"}


def document_filename(kind: str, letter_type: str | None, version: str, number: str | None) -> str:
    """The name a document is sent and downloaded under (and Documenso seals as
    `<name>_signed.pdf`, probe § 4): its own kind, regime and text version, so the two families
    never share one."""
    occasional = "-occasionale" if letter_type == "occasional" else ""
    if kind == FRAMEWORK_AGREEMENT:
        return f"contratto-quadro{occasional}-v{version}.pdf"
    return f"lettera-di-incarico{occasional}-{number or 'senza-numero'}-v{version}.pdf"


# ---- the text's front matter and drafts ----------------------------------------------------

_FRONT_MATTER = re.compile(r"---\n(.*?)\n---\n", re.S)


def split_front_matter(markdown: str) -> tuple[str, str]:
    """The front matter block, delimiters included, and the text after it."""
    front = _FRONT_MATTER.match(markdown)
    if front is None:
        raise ValidationFailed(
            ENTITY,
            "body_markdown",
            "il testo non ha l'intestazione (title, version, date, status)",
            expected="un blocco --- con title, version, date e status",
        )
    return markdown[: front.end()], markdown[front.end() :]


def front_matter(markdown: str) -> dict[str, str]:
    """The front matter's `key: value` lines (title, subtitle, version, date, status), quotes
    stripped. The version is what a document row records as `text_version`."""
    block, _ = split_front_matter(markdown)
    found: dict[str, str] = {}
    for line in block.splitlines()[1:-1]:
        key, separator, value = line.partition(":")
        if separator and key.strip() and not key.startswith((" ", "\t")):
            found[key.strip()] = value.strip().strip("\"'")
    return found


def check_front_matter(markdown: str) -> dict[str, str]:
    """The front matter of a text of these types: it names a `version` and a `status` (`draft` or
    `final`). Checked when the template is saved, so a text is never found incomplete when a
    letter is already being written."""
    front = front_matter(markdown)
    for key in ("version", "status"):
        if not front.get(key):
            raise ValidationFailed(
                ENTITY,
                "body_markdown",
                f"l'intestazione del testo non dice `{key}`",
                expected=f"{key}: ...",
            )
    if front["status"] not in ("draft", "final"):
        raise ValidationFailed(
            ENTITY,
            "body_markdown",
            f"lo stato del testo e' «{front['status']}»",
            expected="draft | final",
        )
    return front


def is_draft(markdown: str) -> bool:
    return check_front_matter(markdown)["status"] == "draft"


def refuse_draft(markdown: str, name: str) -> None:
    """A text still in draft is never sent for signature (the hub's rule, § 1 of the design)."""
    if is_draft(markdown):
        raise ValidationFailed(
            ENTITY,
            "status",
            f"il testo «{name}» e' ancora una bozza: non parte per la firma",
            expected="un testo con status: final",
        )


# ---- the key map ---------------------------------------------------------------------------

# The hub's texts name a field with one flat hyphenated key (`{{professionista-nome}}`); the CRM's
# engine reads dotted ones (`{{professionista.nome}}`). This is the one-time map the four texts
# (and the NDA and the company contract) go through when they are seeded; it stays here because
# the import of the hub's texts at the cutover runs the same map over whatever the texts say then.
# A key that is not listed is a field the admin writes for each letter: it becomes a root
# variable, the hyphens turned into underscores (`{{periodo-verifica}}` is `{{periodo_verifica}}`).
HUB_KEY_MAP: dict[str, str] = {
    # rebase, the issuer: the legal entity of the space (`emittente`).
    "rebase-ragione-sociale": "emittente.ragione_sociale",
    "rebase-sede": "emittente.sede",
    "rebase-cf": "emittente.codice_fiscale",
    "rebase-piva": "emittente.partita_iva",
    "rebase-codice-destinatario": "emittente.codice_sdi",
    "rebase-pec": "emittente.pec",
    "rebase-rappresentante": "emittente.rappresentante",
    # The freelancer: the organization the engagement belongs to.
    "professionista-nome": "professionista.nome",
    "professionista-piva": "professionista.partita_iva",
    "professionista-cf": "professionista.codice_fiscale",
    "professionista-email": "professionista.email",
    "professionista-pec": "professionista.pec",
    "professionista-domicilio": "professionista.domicilio",
    # The client of the engagement.
    "cliente-ragione-sociale": "cliente.ragione_sociale",
    "cliente-piva": "cliente.partita_iva",
    "cliente-sede": "cliente.sede",
    "cliente-pec": "cliente.pec",
    "cliente-codice-destinatario": "cliente.codice_sdi",
    # What the engagement itself says.
    "numero": "incarico.numero",
    "data-contratto-quadro": "incarico.data_contratto_quadro",
    "ruolo": "incarico.ruolo",
    "data-inizio": "incarico.data_inizio",
    "data-fine": "incarico.data_fine",
    "modalita": "incarico.modalita",
    "compenso": "incarico.compenso",
    "unita": "incarico.unita",
    "termine-pagamento": "incarico.termine_pagamento",
    "giorni-incasso": "incarico.giorni_incasso",
    # The signature blocks: blanks the signing site fills, and what rebase prints in its own.
    "luogo-firma": "firma.luogo",
    "data-firma": "firma.data",
    "firma-rebase": "firma.rebase",
    "firma-professionista": "firma.professionista",
    "firma-cliente": "firma.cliente",
}

_HUB_FIELD = re.compile(r"\{\{([a-z0-9]+(?:-[a-z0-9]+)*)\}\}")
_HUB_IF = re.compile(r"\{\{se ([a-z0-9]+(?:-[a-z0-9]+)*)\}\}")
_HUB_END_IF = re.compile(r"\{\{fine se\}\}")


def dotted(key: str) -> str:
    """A hub key as the CRM's engine reads it."""
    return HUB_KEY_MAP.get(key, key.replace("-", "_"))


def convert_hub_text(markdown: str) -> str:
    """A hub text as a template body: every `{{flat-key}}` through the map, and the hub's
    conditional (`{{se key}}` ... `{{fine se}}`, REB-979) as the engine's `{{#if}}` block.
    Everything else, the front matter, the proposals in `[[...]]` and the prose, is kept as it
    is."""
    converted = _HUB_IF.sub(lambda found: "{{#if " + dotted(found.group(1)) + "}}", markdown)
    converted = _HUB_END_IF.sub("{{/if}}", converted)
    return _HUB_FIELD.sub(lambda found: "{{" + dotted(found.group(1)) + "}}", converted)


# What the signing site fills, by the name of its blank: the freelancer's signatures and the date
# of signing (`firma ...` and `data firma`, as the hub's Typst template names them).
SIGNING_BLANKS = ("firma professionista", "firma cliente", "data firma")


# The labels the hub's template gave to the blanks whose dotted path does not read the same.
BLANK_LABELS = {("firma", "data"): "data firma", ("firma", "luogo"): "luogo firma"}


def blank_label(path: tuple[str, ...]) -> str:
    """The label printed under a blank line: `firma.professionista` is `firma professionista`,
    `firma.data` is `data firma`, `cliente.sede` is `cliente sede`, and a fact of the engagement
    (`incarico.data_fine`) goes by its own name, `data fine`, as the hub's keys did."""
    if path in BLANK_LABELS:
        return BLANK_LABELS[path]
    parts = path[1:] if path[0] == "incarico" and len(path) > 1 else path
    return " ".join(part.replace("_", " ") for part in parts)
