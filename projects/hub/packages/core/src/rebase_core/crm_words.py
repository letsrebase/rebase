"""Where a company request's link to rebase's CRM stands, in words (REB-868).

Apart from `crm_link` so that the request's own services and the admin schemas can say it
without importing the engagements client: the states are a match's `pigro_state`
(`models.PIGRO_STATES`) and the sentence is `match_words.pigro_state_sentence`'s twin.
"""

from rebase_core.match_words import HTTPS_ONLY

DA_COLLEGARE, COLLEGATO, ERRORE, RIFIUTATO = "da_collegare", "collegato", "errore", "rifiutato"
REFERENTE_WITHOUT_NAME = "Il referente non ha un nome: completalo e riprova."
# The hub's own sentences for a request it never sent: shown alone, not wrapped.
NOT_ASKED = frozenset({HTTPS_ONLY, REFERENTE_WITHOUT_NAME})


def crm_state_sentence(stato: str | None, errore: str | None) -> str:
    """Where a request's link to the CRM stands, in the page's words, with the CRM's own
    sentence folded in: empty for no state (a request from before the link) and for
    `collegato`. One of the hub's own sentences (`NOT_ASKED`) is shown alone."""
    if stato == DA_COLLEGARE:
        return "Il CRM non ha ancora la richiesta: riprova o aspetta lo sweep."
    if stato == ERRORE and errore in NOT_ASKED:
        return errore or ""
    if stato == ERRORE:
        return _with_cause("Il CRM non ha risposto", errore)
    if stato == RIFIUTATO:
        return _with_cause("Il CRM ha rifiutato la richiesta", errore)
    return ""


def _with_cause(sentence: str, cause: str | None) -> str:
    if not cause:
        return f"{sentence}."
    return f"{sentence}: {cause}" if cause.endswith(".") else f"{sentence}: {cause}."
