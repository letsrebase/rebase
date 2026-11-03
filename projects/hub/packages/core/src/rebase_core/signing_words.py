"""The sentences signing stores and refuses with, and why a document a webhook cancels is
`cancelled` (REB-387, REB-892).

Apart from `signing` so that the service's three parts (`SigningService`, `SigningRecovery`,
`SigningAdmin`) all read the same words without importing one another.
"""

from rebase_core.documenso import REJECTED, Outcome
from rebase_core.match_rows import FRAMEWORK, LETTER
from rebase_core.models import CANCEL_REASON_MAX_LENGTH

_ALLOW_DRAFT_NOTE = " Su un ambiente di prova lo permette REBASE_CONTRACTS_ALLOW_DRAFT."
DRAFT_REFUSED = {
    FRAMEWORK: (
        "Il testo del contratto quadro è ancora una bozza (status: draft): si genera e si "
        "salva, ma non parte per la firma." + _ALLOW_DRAFT_NOTE
    ),
    LETTER: (
        "Il testo della lettera di incarico è ancora una bozza (status: draft): si genera "
        "e si salva, ma non parte per la firma." + _ALLOW_DRAFT_NOTE
    ),
}
NO_DOCUMENSO = (
    "La firma elettronica non è attiva su questo ambiente: mancano l'indirizzo o il token "
    "di Documenso."
)
NO_SENDER = (
    "L'invio delle email non è attivo su questo ambiente: il link per firmare non "
    "arriverebbe a nessuno."
)
REFUSED_ON_SITE = "Rifiutato dal freelance sul sito di firma."
CANCELLED_ON_DOCUMENSO = "Annullato su Documenso."
CANCELLED_BY_REBASE = "Annullato da rebase."
CANCELLED_WITH_MATCH = "Annullato da rebase con il suo match."
CANCEL_REFUSED = (
    "Documenso non annulla questo documento: forse è già stato firmato, rifiutato o "
    "annullato. Premi «Aggiorna stato» e riprova."
)


def cancel_reason(outcome: Outcome) -> str:
    """Why a document the webhook cancels is `cancelled`, as the page shows it."""
    if outcome.kind == REJECTED:
        reason = (outcome.reason or "").strip()
        if not reason:
            return REFUSED_ON_SITE
        return f"Rifiutato dal freelance: {reason}"[:CANCEL_REASON_MAX_LENGTH]
    return CANCELLED_ON_DOCUMENSO
