"""The sentences signing stores and refuses with, and why a document a webhook cancels is
`cancelled` (REB-1019, moved from the hub's `signing_words`).

Apart from `signing` so that the service's two parts (`SigningService` and `SigningRecovery`) read
the same words without importing one another. Italian is the product copy.
"""

from pigrocrm.core.documenso.client import REJECTED, Outcome

NO_DOCUMENSO = (
    "La firma elettronica non è attiva su questo ambiente: mancano l'indirizzo o il token di "
    "Documenso. Il documento si scarica, si firma a mano e si carica firmato."
)
NO_SENDER = (
    "L'invio delle email non è attivo su questo ambiente: il link per firmare non arriverebbe a "
    "nessuno."
)
NO_SIGNER_EMAIL = "Il freelance non ha un indirizzo email: il link per firmare non arriverebbe."
REFUSED_ON_SITE = "Rifiutato dal freelance sul sito di firma."
CANCELLED_ON_DOCUMENSO = "Annullato su Documenso."
CANCELLED_BY_US = "Annullato da noi."
CANCELLED_WITH_ENGAGEMENT = "Annullato con il suo incarico."
CANCEL_REFUSED = (
    "Documenso non annulla questo documento: forse è già stato firmato, rifiutato o annullato. "
    "Aggiorna lo stato e riprova."
)
HAND_SIGNED = "Firmato a mano e caricato."
# The longest a `cancel_reason` may be (`documents.cancel_reason`).
CANCEL_REASON_MAX_LENGTH = 200


def cancel_reason(outcome: Outcome) -> str:
    """Why a document the webhook cancels is `cancelled`, as the page shows it."""
    if outcome.kind == REJECTED:
        reason = (outcome.reason or "").strip()
        if not reason:
            return REFUSED_ON_SITE
        return f"Rifiutato dal freelance: {reason}"[:CANCEL_REASON_MAX_LENGTH]
    return CANCELLED_ON_DOCUMENSO
