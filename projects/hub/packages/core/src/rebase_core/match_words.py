"""What a match and its documents are doing, and what comes next, in the admin's words
(REB-477).

«Match e contratti», the «Match» list and the MCP tools read the same sentence and the
same next step from here, so the rules the web page used to hold (when
«Invia per la firma» applies, which signing action a state has, when a match is cancelled or closed)
live and are tested in one place. Every function is pure: the caller hands the facts,
each date already a day in Rome. The sentences say «il freelance», never a name: a
document read does not carry one, and the page's title already names the person. The
check's first sentence is the exception, since it is what step 3 of «Crea match» reads
back before anything is saved. Nor does a sentence open with its state's label: the
pages show that label beside it.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from rebase_core.contracts.fields import FEE, italian_date, rendered
from rebase_core.regimes import OCCASIONAL, VAT_REGISTERED

if TYPE_CHECKING:
    from rebase_core.contract_schemas import LetterFields, SendReport

# What the page prints for each pay mode (`contract_schemas.PAY_MODES`): the contract text
# is Italian.
PAY_MODE_WORDS = {"to_work_unit": "a giornata", "to_body": "a corpo"}

# A state as a sentence names it («questo è annullato»): the stored values are English, the
# sentences are Italian.
STATE_WORDS = {
    "draft": "bozza",
    "awaiting_signature": "in firma",
    "active": "attivo",
    "concluded": "concluso",
    "cancelled": "annullato",
    "generated": "generato",
    "pending": "in attesa",
    "sent": "inviato",
    "signed": "firmato",
    "notice_served": "disdetto",
}

Action = Literal[
    "send",
    "resend_email",
    "status_refresh",
    "cancel",
    "close",
    "record_notice",
    "retry_pigro",
]
# Where the freelancer's framework agreement stands when a match is about to be saved:
# one must leave first (none, or one generated and never sent), one is out for signature
# already, or one is active.
FrameworkStep = Literal["to_send", "awaiting_signature", "active"]
Words = tuple[str, Action | None, list[Action]]

FRAMEWORK = "framework"
LETTER = "letter"
# How every refusal `SigningService` stores begins, with or without the freelancer's words.
REFUSED = "Rifiutato"
SENTENCE_ENDS = (".", "!", "?", "…")
# The match states a signed letter still waiting for its copy can be in, each with what
# else its match can do: a closed match's letter still fetches the copy (REB-477).
AWAITING_COPY: dict[str, tuple[Action, ...]] = {
    "awaiting_signature": ("cancel",),
    "active": ("close",),
    "concluded": (),
}

MATCH_STATE_LABELS = {
    "draft": "Da inviare",
    "awaiting_signature": "In attesa di firma",
    "active": "Attivo",
    "concluded": "Concluso",
    "cancelled": "Annullato",
}
DOCUMENT_STATE_LABELS = {
    "generated": "Pronto, non inviato",
    "pending": "Parte dopo il contratto quadro",
    "sent": "Da firmare",
    "signed": "Firmato",
    "cancelled": "Annullato",
    "notice_served": "Disdetto",
}

DRAFT_TEXT = " Il testo è ancora in bozza."
MISSING_TAX_DATA = "Mancano i dati fiscali del freelance: servono prima di salvare."
# Where an active match's link to Pigro stands (`models.PIGRO_STATES`), read again here,
# each module its own copy of the four words rather than an import (`engagements.py`
# does the same): `None` until the trigger runs, one of the other four after (REB-498).
TO_LINK, LINKED, ERROR, REJECTED = "to_link", "linked", "error", "rejected"
PIGRO_RETRY_STATES = (TO_LINK, ERROR, REJECTED)
PIGRO_LINKED = "Le ore si consuntivano su Pigro."
WHAT_LEAVES_FIRST: dict[FrameworkStep, str] = {
    "to_send": (
        "Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua firma."
    ),
    "awaiting_signature": (
        "Il contratto quadro è già in firma: la lettera di incarico parte da sola dopo la sua "
        "firma."
    ),
    "active": "Il contratto quadro è già attivo: parte subito la lettera di incarico.",
}

# The same three sentences for an occasional letter (REB-687), naming its own documents.
WHAT_LEAVES_FIRST_OCCASIONAL: dict[FrameworkStep, str] = {
    "to_send": (
        "Prima parte il contratto quadro per prestazioni occasionali; la lettera di incarico "
        "occasionale parte da sola dopo la sua firma."
    ),
    "awaiting_signature": (
        "Il contratto quadro per prestazioni occasionali è già in firma: la lettera di "
        "incarico occasionale parte da sola dopo la sua firma."
    ),
    "active": (
        "Il contratto quadro per prestazioni occasionali è già attivo: parte subito la "
        "lettera di incarico occasionale."
    ),
}
# How step 3 prices a fee (REB-687): an ordinary letter's is net of VAT, an occasional
# one's gross and outside VAT altogether.
VAT_EXCLUDED = "IVA esclusa"
GROSS_WITHOUT_VAT = "lordi, senza IVA"
# An occasional letter for someone whose saved tax data have a partita IVA is allowed,
# since occasional work outside their professional activity exists; step 3 says so.
HAS_VAT_NUMBER = (
    "Ha la partita IVA: la prestazione occasionale vale solo per un lavoro fuori dalla sua "
    "attività professionale."
)
# An ordinary letter for someone whose saved tax data have no partita IVA is refused, on
# `regime`, by `MatchDrafts.check`, `preview` and `MatchService.create`, and by
# `SigningService.send_match` when the partita IVA was removed after the draft.
ORDINARY_NEEDS_VAT_NUMBER = (
    'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" '
    "o salva la partita IVA."
)


@dataclass(frozen=True)
class DocumentFacts:
    """What the sentences read of a contract document, whichever read model it ends up
    in: `framework.document_facts` builds it from the row."""

    kind: str
    status: str
    number: str | None = None
    draft_text: bool = False
    sent_on: date | None = None
    signed_on: date | None = None
    notice_on: date | None = None
    cancel_reason: str | None = None
    has_pdf_signed: bool = False
    active: bool = False
    renewal: date | None = None
    last_notice_day: date | None = None


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


# The environment has no `REBASE_PIGRO_ENGAGEMENTS_TOKEN` (spec § 3.2): what the card of
# an active match says, and what the report and link routes answer with their 503.
PIGRO_NOT_CONFIGURED = "Consuntivo non configurato su questo ambiente."
# The `error`s the hub records without asking the CRM (`EngagementService.payload` and
# `_put`), each shown alone on the card, since «Pigro non ha risposto» would say a call
# was made: the door wants a name and a surname, and a CRM on HTTPS, since the bearer
# never travels in clear.
PROFILE_WITHOUT_NAME = (
    "Il freelance non ha nome e cognome sul profilo: il collegamento a Pigro riparte "
    "quando il profilo è completo."
)
HTTPS_ONLY = "Pigro è raggiungibile solo su https."
NOT_ASKED = frozenset({PROFILE_WITHOUT_NAME, HTTPS_ONLY})


def pigro_state_sentence(pigro_status: str | None, pigro_error: str | None) -> str:
    """Why an active match has no report yet, with the CRM's own words folded in
    (spec § 3.5): the empty string for `None` (the match is not active) and for
    `linked` (nothing wrong -- `match_words` says that one itself, since it also has
    good news to report). Read by `match_words`, for the card's `situation`, and by
    `EngagementService.report`, for the `InvalidState` a match not `linked` refuses
    with (the report has its own sentence for `None`). `pigro_error` is the cause the
    link stored (`engagements.CAUSE_*`, or the CRM's own sentence), wrapped here as
    «Pigro non ha risposto: HTTP 503.»; a state written with none (an old row not yet
    backfilled) still gets a sentence, «Pigro non ha risposto.» One of the hub's own
    sentences (`NOT_ASKED`) is shown as it is."""
    if pigro_status == TO_LINK:
        return "Pigro non ha ancora il deal: riprova o aspetta lo sweep."
    if pigro_status == ERROR and pigro_error is not None and pigro_error in NOT_ASKED:
        return pigro_error
    if pigro_status == ERROR:
        return _with_cause("Pigro non ha risposto", pigro_error)
    if pigro_status == REJECTED:
        return _with_cause("Pigro ha rifiutato il collegamento", pigro_error)
    return ""


def _with_cause(sentence: str, cause: str | None) -> str:
    """`sentence: cause.`, the cause as `EngagementService` stored it (a bare cause,
    «HTTP 503», or the CRM's own sentence, which already ends in a stop), or the
    sentence alone when none was stored."""
    cause = (cause or "").strip()
    if not cause:
        return f"{sentence}."
    stop = "" if cause.endswith((".", "!", "?", "…")) else "."
    return f"{sentence}: {cause}{stop}"


def document_words(document: DocumentFacts) -> Words:
    sentence, next_action, others = _document_words(document)
    if document.kind == FRAMEWORK and document.draft_text:
        sentence += DRAFT_TEXT
    return sentence, next_action, others


def _document_words(document: DocumentFacts) -> Words:
    framework = document.kind == FRAMEWORK
    status = document.status
    if status == "generated":
        if framework:
            return "Parte con «Invia per la firma» sul suo match.", None, ["cancel"]
        return "Pronta, non ancora inviata.", None, []
    if status == "pending":
        return "Parte da sola dopo la firma del contratto quadro.", None, []
    if status == "sent":
        sent = "Inviato" if framework else "Inviata"
        return (
            f"{sent}{_on(document.sent_on)}: aspetta la firma del freelance.",
            "resend_email",
            ["status_refresh", "cancel"] if framework else ["status_refresh"],
        )
    if status == "signed":
        signed = ("Firmato" if framework else "Firmata") + _on(document.signed_on)
        if not document.has_pdf_signed:
            return (
                f"{signed}; la copia firmata non è ancora arrivata.",
                "status_refresh",
                ["record_notice"] if document.active else [],
            )
        if not framework:
            return f"{signed}.", None, []
        if not document.active:
            return f"{signed}, non più attivo.", None, ["status_refresh"]
        renewal = (
            f" Si rinnova da solo{_on(document.renewal)}; disdetta entro"
            f"{_on(document.last_notice_day)}."
            if document.renewal is not None and document.last_notice_day is not None
            else ""
        )
        return f"{signed}.{renewal}", None, ["status_refresh", "record_notice"]
    if status == "cancelled":
        cancelled = "Annullato." if framework else "Annullata."
        return _sentence(document.cancel_reason) or cancelled, None, []
    if status == "notice_served":
        return f"Disdetto{_on(document.notice_on)}.", None, []
    return f"{DOCUMENT_STATE_LABELS.get(status, status)}.", None, []


def _period(start: str | None, end: str | None) -> str:
    """The letter's own `data-inizio` and `data-fine`, as it printed them."""
    return (f", dal {start}" if start else "") + (f" al {end}" if end else "")


def match_words(
    status: str,
    letter: DocumentFacts,
    framework_status: str | None,
    letter_start: str | None,
    letter_end: str | None,
    *,
    pigro_status: str | None = None,
    pigro_error: str | None = None,
    pigro_configured: bool = True,
) -> Words:
    """`framework_status` is where the freelancer's framework agreement stands
    (`framework.framework_states`): `signed` for an active one, else the pending one's
    `sent` or `generated`, `None` when there is none. A waiting letter leaves by itself
    after one out for signature; otherwise «Invia per la firma» sends it (an active one,
    its release missed), sends the framework agreement first (one generated and never
    sent) or writes a new one (none, or one cancelled or refused). `pigro_status` and
    `pigro_error` are the match's own (REB-498): for an `active` match they add one
    sentence to `situation` about where its hours are, and, short of `linked`, a
    «Riprova su Pigro» among `other_actions`; irrelevant, and left `None`, for every other
    state. `pigro_configured` is whether this environment has the CRM's token: without
    it the sentence is `PIGRO_NOT_CONFIGURED` whatever the state, and there is no
    «Riprova», which would only answer 503 (spec § 3.2)."""
    number = letter.number
    if status == "draft":
        return (
            f"La lettera n. {number} è pronta: il freelance non ha ancora ricevuto nulla.",
            "send",
            ["cancel"],
        )
    if status == "awaiting_signature" and letter.status == "pending":
        if framework_status == "signed":
            return (
                f"La lettera n. {number} è pronta a partire: il contratto quadro è già firmato.",
                "send",
                ["cancel"],
            )
        if framework_status == "sent":
            return (
                f"La lettera n. {number} aspetta la firma del contratto quadro e parte da sola "
                "dopo.",
                None,
                ["cancel"],
            )
        if framework_status == "generated":
            return (
                f"La lettera n. {number} parte dopo il contratto quadro: «Invia per la firma» lo "
                "manda al freelance.",
                "send",
                ["cancel"],
            )
        return (
            f"La lettera n. {number} aspetta un contratto quadro: «Invia per la firma» ne genera "
            "uno nuovo.",
            "send",
            ["cancel"],
        )
    if status == "awaiting_signature" and letter.status == "sent":
        return (
            f"Lettera n. {number} inviata{_on(letter.sent_on)}: aspetta la firma del freelance.",
            "resend_email",
            ["status_refresh", "cancel"],
        )
    if status in AWAITING_COPY and letter.status == "signed" and not letter.has_pdf_signed:
        return (
            f"Lettera n. {number} firmata{_on(letter.signed_on)}; la copia firmata non è ancora "
            "arrivata.",
            "status_refresh",
            list(AWAITING_COPY[status]),
        )
    if status == "active":
        sentence = (
            f"Lettera n. {number} firmata{_on(letter.signed_on)}"
            f"{_period(letter_start, letter_end)}."
        )
        if pigro_status is not None and not pigro_configured:
            note = PIGRO_NOT_CONFIGURED
        elif pigro_status == LINKED:
            note = PIGRO_LINKED
        else:
            note = pigro_state_sentence(pigro_status, pigro_error)
        other_actions: list[Action] = ["close"]
        if pigro_configured and pigro_status in PIGRO_RETRY_STATES:
            other_actions.append("retry_pigro")
        return (f"{sentence} {note}" if note else sentence, None, other_actions)
    if status == "concluded":
        return f"Lettera n. {number}{_period(letter_start, letter_end)}.", None, []
    if status == "cancelled":
        return f"La lettera n. {number} non va più firmata.{_refusal(letter)}", None, []
    # `awaiting_signature` with a letter refused or cancelled on the signing site: the match stays
    # in
    # signature until an admin cancels it.
    if letter.status == "cancelled":
        return f"Lettera n. {number} annullata.{_refusal(letter)}", None, ["cancel"]
    return f"Lettera n. {number}.", None, ["cancel"] if status == "awaiting_signature" else []


def check_sentences(
    first_name: str,
    customer: str,
    letter: "LetterFields",
    framework: FrameworkStep,
    *,
    missing_tax_data: bool,
    regime: str = VAT_REGISTERED,
    has_vat_number: bool = False,
) -> tuple[list[str], str]:
    """`(riepilogo, cosa_succede)`: step 3 of «Crea match», what the letter says in three
    sentences and which document leaves first. An occasional letter (REB-687) prices its
    fee gross and without VAT, names the occasional documents, and adds a warning when
    the saved tax data have a partita IVA (`has_vat_number`)."""
    occasional = regime == OCCASIONAL
    where = f", {_flat(letter.location)}" if letter.location else ""
    until = f" al {italian_date(letter.end_date)}" if letter.end_date is not None else ""
    recap = [
        f"{first_name} lavorerà per {customer} come {_flat(letter.role)}{where}, dal "
        f"{italian_date(letter.start_date)}{until}."
    ]
    if letter.commitment:
        recap.append(f"Impegno: {_flat(letter.commitment).rstrip('.')}.")
    unit = letter.unit or PAY_MODE_WORDS.get(letter.mode or "")
    per = f" {_flat(unit)}" if unit else ""
    month_end = " fine mese" if letter.month_end else ""
    # The fee exactly as the letter prints it, «450,00 €», so step 3 and the PDF agree.
    fee = rendered(FEE, letter.to_fields()[FEE])
    vat = GROSS_WITHOUT_VAT if occasional else VAT_EXCLUDED
    recap.append(f"Compenso: {fee}{per}, {vat}, pagato a {letter.payment_days} giorni{month_end}.")
    if occasional and has_vat_number:
        recap.append(HAS_VAT_NUMBER)
    if missing_tax_data:
        recap.append(MISSING_TAX_DATA)
    leaves = WHAT_LEAVES_FIRST_OCCASIONAL if occasional else WHAT_LEAVES_FIRST
    return recap, leaves[framework]


def send_report_sentence(report: "SendReport") -> str:
    """What «Invia per la firma» did, in the sentence the pages show after it (REB-390)
    and `send_match_for_signature` answers: the document that left, or the letter waiting
    for a framework agreement already out for signature, and a mail that did not leave."""
    number = report.match.letter.number
    if report.sent == FRAMEWORK:
        sent = (
            f"Partito il contratto quadro: la lettera n. {number} partirà da sola dopo la sua "
            "firma."
        )
    elif report.sent == LETTER:
        sent = f"Partita la lettera di incarico n. {number}."
    else:
        sent = (
            f"La lettera n. {number} aspetta il contratto quadro già in firma e partirà da sola "
            "dopo."
        )
    if report.mail_sent is False:
        return f"{sent} La mail però non è partita: usa «Reinvia email»."
    return sent
