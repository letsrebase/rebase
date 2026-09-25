"""What a match and its documents are doing, and what comes next, in the admin's words
(REB-477).

«Match e contratti», the «Match» list and the MCP tools read the same sentence and the
same next step from here, so the rules the web page used to hold (when «Invia per la
firma» applies, which signing action a state has, when a match is cancelled or closed)
live and are tested in one place. Every function is pure: the caller hands the facts,
each date already a day in Rome. The sentences say «il freelance», never a name: a
document read does not carry one, and the page's title already names the person. The
check's first sentence is the exception, since it is what step 3 of «Crea match» reads
back before anything is saved.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from rebase_core.contracts.fields import FEE, italian_date, rendered

if TYPE_CHECKING:
    from rebase_core.contract_schemas import LetteraFields, SendReport

Action = Literal[
    "invia", "reinvia_email", "aggiorna_stato", "annulla", "chiudi", "registra_disdetta"
]
# Where the freelancer's framework agreement stands when a match is about to be saved:
# one must leave first (none, or one generated and never sent), one is out for signature
# already, or one is active.
FrameworkStep = Literal["da_inviare", "in_firma", "attivo"]
Words = tuple[str, Action | None, list[Action]]

QUADRO = "quadro"
LETTERA = "lettera"
# How every refusal `SigningService` stores begins, with or without the freelancer's words.
REFUSED = "Rifiutato"
SENTENCE_ENDS = (".", "!", "?", "…")

MATCH_STATE_LABELS = {
    "bozza": "Da inviare",
    "in_firma": "In attesa di firma",
    "attivo": "Attivo",
    "concluso": "Concluso",
    "annullato": "Annullato",
}
DOCUMENT_STATE_LABELS = {
    "generato": "Pronto, non inviato",
    "in_attesa": "Parte dopo il contratto quadro",
    "inviato": "Da firmare",
    "firmato": "Firmato",
    "annullato": "Annullato",
    "disdetto": "Disdetto",
}

DRAFT_TEXT = " Il testo è ancora in bozza."
MISSING_TAX_DATA = "Mancano i dati fiscali del freelance: servono prima di salvare."
WHAT_LEAVES_FIRST: dict[FrameworkStep, str] = {
    "da_inviare": (
        "Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua firma."
    ),
    "in_firma": (
        "Il contratto quadro è già in firma: la lettera di incarico parte da sola dopo la sua "
        "firma."
    ),
    "attivo": "Il contratto quadro è già attivo: parte subito la lettera di incarico.",
}


@dataclass(frozen=True)
class DocumentFacts:
    """What the sentences read of a contract document, whichever read model it ends up
    in: `framework.document_facts` builds it from the row."""

    kind: str
    stato: str
    numero: str | None = None
    testo_bozza: bool = False
    sent_on: date | None = None
    signed_on: date | None = None
    notice_on: date | None = None
    cancel_reason: str | None = None
    ha_pdf_firmato: bool = False
    attivo: bool = False
    rinnovo: date | None = None
    ultimo_giorno_disdetta: date | None = None


def _on(day: date | None) -> str:
    return f" il {italian_date(day)}" if day is not None else ""


def _sentence(reason: str | None) -> str:
    """A stored `cancel_reason` is already a sentence («Annullato da rebase.», «Rifiutato
    dal freelance: …»), shown as it is: a freelancer's own words may end with their own
    full stop, question or exclamation mark, or with none, which gets a full stop."""
    text = (reason or "").strip()
    return text if not text or text.endswith(SENTENCE_ENDS) else f"{text}."


def _refusal(letter: DocumentFacts) -> str:
    """The freelancer's refusal, the one reason a match's sentence repeats after its own:
    the others (cancelled by rebase, with the match, on Documenso) say nothing more."""
    reason = _sentence(letter.cancel_reason)
    return f" {reason}" if reason.startswith(REFUSED) else ""


def _flat(text: str) -> str:
    """A paragraph the letter may print on several lines, on one line."""
    return " ".join(text.split())


def document_words(document: DocumentFacts) -> Words:
    sentence, next_action, others = _document_words(document)
    if document.kind == QUADRO and document.testo_bozza:
        sentence += DRAFT_TEXT
    return sentence, next_action, others


def _document_words(document: DocumentFacts) -> Words:
    framework = document.kind == QUADRO
    stato = document.stato
    if stato == "generato":
        if framework:
            return (
                "Pronto, non ancora inviato: parte con «Invia per la firma» sul match.",
                None,
                ["annulla"],
            )
        return "Pronta, non ancora inviata.", None, []
    if stato == "in_attesa":
        return "Parte da sola dopo la firma del contratto quadro.", None, []
    if stato == "inviato":
        sent = "Inviato" if framework else "Inviata"
        return (
            f"{sent}{_on(document.sent_on)}: aspetta la firma del freelance.",
            "reinvia_email",
            ["aggiorna_stato", "annulla"] if framework else ["aggiorna_stato"],
        )
    if stato == "firmato":
        signed = ("Firmato" if framework else "Firmata") + _on(document.signed_on)
        if not document.ha_pdf_firmato:
            return (
                f"{signed}; la copia firmata non è ancora arrivata.",
                "aggiorna_stato",
                ["registra_disdetta"] if document.attivo else [],
            )
        if not framework:
            return f"{signed}.", None, []
        if not document.attivo:
            return f"{signed}, non più attivo.", None, ["aggiorna_stato"]
        renewal = (
            f" Si rinnova da solo{_on(document.rinnovo)}; disdetta entro"
            f"{_on(document.ultimo_giorno_disdetta)}."
            if document.rinnovo is not None and document.ultimo_giorno_disdetta is not None
            else ""
        )
        return f"{signed}.{renewal}", None, ["aggiorna_stato", "registra_disdetta"]
    if stato == "annullato":
        cancelled = "Annullato." if framework else "Annullata."
        return _sentence(document.cancel_reason) or cancelled, None, []
    if stato == "disdetto":
        return f"Disdetto{_on(document.notice_on)}.", None, []
    return f"{DOCUMENT_STATE_LABELS.get(stato, stato)}.", None, []


def _period(start: str | None, end: str | None) -> str:
    """The letter's own `data-inizio` and `data-fine`, as it printed them."""
    return (f", dal {start}" if start else "") + (f" al {end}" if end else "")


def match_words(
    stato: str,
    letter: DocumentFacts,
    framework_stato: str | None,
    letter_start: str | None,
    letter_end: str | None,
) -> Words:
    """`framework_stato` is where the freelancer's framework agreement stands
    (`framework.framework_states`): `firmato` for an active one, else the pending one's
    `inviato` or `generato`, `None` when there is none. A waiting letter leaves by itself
    after one out for signature; otherwise «Invia per la firma» sends it (an active one,
    its release missed), sends the framework agreement first (one generated and never
    sent) or writes a new one (none, or one cancelled or refused)."""
    numero = letter.numero
    if stato == "bozza":
        return (
            f"Da inviare: la lettera n. {numero} è pronta, il freelance non ha ancora ricevuto "
            "nulla.",
            "invia",
            ["annulla"],
        )
    if stato == "in_firma" and letter.stato == "in_attesa":
        if framework_stato == "firmato":
            return (
                f"La lettera n. {numero} è pronta a partire: il contratto quadro è già firmato.",
                "invia",
                ["annulla"],
            )
        if framework_stato == "inviato":
            return (
                f"La lettera n. {numero} aspetta la firma del contratto quadro e parte da sola "
                "dopo.",
                None,
                ["annulla"],
            )
        if framework_stato == "generato":
            return (
                f"La lettera n. {numero} parte dopo il contratto quadro: «Invia per la firma» lo "
                "manda al freelance.",
                "invia",
                ["annulla"],
            )
        return (
            f"La lettera n. {numero} aspetta un contratto quadro: «Invia per la firma» ne genera "
            "uno nuovo.",
            "invia",
            ["annulla"],
        )
    if stato == "in_firma" and letter.stato == "inviato":
        return (
            f"Lettera n. {numero} inviata{_on(letter.sent_on)}: aspetta la firma del freelance.",
            "reinvia_email",
            ["aggiorna_stato", "annulla"],
        )
    if stato in ("in_firma", "attivo") and letter.stato == "firmato" and not letter.ha_pdf_firmato:
        return (
            f"Lettera n. {numero} firmata{_on(letter.signed_on)}; la copia firmata non è ancora "
            "arrivata.",
            "aggiorna_stato",
            ["chiudi"] if stato == "attivo" else ["annulla"],
        )
    if stato == "attivo":
        return (
            f"Attivo: lettera n. {numero} firmata{_on(letter.signed_on)}"
            f"{_period(letter_start, letter_end)}.",
            None,
            ["chiudi"],
        )
    if stato == "concluso":
        return f"Concluso: lettera n. {numero}{_period(letter_start, letter_end)}.", None, []
    if stato == "annullato":
        return (
            f"Annullato: la lettera n. {numero} non va più firmata.{_refusal(letter)}",
            None,
            [],
        )
    # `in_firma` with a letter refused or cancelled on the signing site: the match stays in
    # signature until an admin cancels it.
    if letter.stato == "annullato":
        return f"Lettera n. {numero} annullata.{_refusal(letter)}", None, ["annulla"]
    label = MATCH_STATE_LABELS.get(stato, stato)
    return f"{label}: lettera n. {numero}.", None, ["annulla"] if stato == "in_firma" else []


def check_sentences(
    nome: str,
    cliente: str,
    lettera: "LetteraFields",
    quadro: FrameworkStep,
    *,
    dati_fiscali_mancanti: bool,
) -> tuple[list[str], str]:
    """`(riepilogo, cosa_succede)`: step 3 of «Crea match», what the letter says in three
    sentences and which document leaves first."""
    where = f", {_flat(lettera.luogo)}" if lettera.luogo else ""
    until = f" al {italian_date(lettera.data_fine)}" if lettera.data_fine is not None else ""
    riepilogo = [
        f"{nome} lavorerà per {cliente} come {_flat(lettera.ruolo)}{where}, dal "
        f"{italian_date(lettera.data_inizio)}{until}."
    ]
    if lettera.impegno:
        riepilogo.append(f"Impegno: {_flat(lettera.impegno).rstrip('.')}.")
    unit = lettera.unita or lettera.modalita
    per = f" {_flat(unit)}" if unit else ""
    month_end = " fine mese" if lettera.fine_mese else ""
    # The fee exactly as the letter prints it, «450,00 €», so step 3 and the PDF agree.
    fee = rendered(FEE, lettera.to_fields()[FEE])
    riepilogo.append(
        f"Compenso: {fee}{per}, IVA esclusa, pagato a {lettera.giorni_pagamento} giorni{month_end}."
    )
    if dati_fiscali_mancanti:
        riepilogo.append(MISSING_TAX_DATA)
    return riepilogo, WHAT_LEAVES_FIRST[quadro]


def send_report_sentence(report: "SendReport") -> str:
    """What «Invia per la firma» did, in the sentence the pages show after it (REB-390)
    and `send_match_for_signature` answers: the document that left, or the letter waiting
    for a framework agreement already out for signature, and a mail that did not leave."""
    numero = report.match.lettera.numero
    if report.inviato == QUADRO:
        sent = (
            f"Partito il contratto quadro: la lettera n. {numero} partirà da sola dopo la sua "
            "firma."
        )
    elif report.inviato == LETTERA:
        sent = f"Partita la lettera di incarico n. {numero}."
    else:
        sent = (
            f"La lettera n. {numero} aspetta il contratto quadro già in firma e partirà da sola "
            "dopo."
        )
    if report.mail_inviata is False:
        return f"{sent} La mail però non è partita: usa «Reinvia email»."
    return sent
