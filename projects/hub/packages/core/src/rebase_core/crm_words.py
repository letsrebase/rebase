"""Where a company request's link to rebase's CRM stands, in words (REB-868).

Apart from `crm_link` so that the request's own services and the admin schemas can say it
without importing the engagements client: the states are a match's `pigro_state`
(`models.PIGRO_STATES`) and the sentence is `match_words.pigro_state_sentence`'s twin.
"""

from rebase_core.match_words import HTTPS_ONLY

TO_LINK, LINKED, ERROR, REJECTED = "to_link", "linked", "error", "rejected"
CONTACT_PERSON_WITHOUT_NAME = "Il referente non ha un nome: completalo e riprova."
# The hub's own sentences for a request it never sent: shown alone, not wrapped.
NOT_ASKED = frozenset({HTTPS_ONLY, CONTACT_PERSON_WITHOUT_NAME})


def crm_state_sentence(status: str | None, error: str | None) -> str:
    """Where a request's link to the CRM stands, in the page's words, with the CRM's own
    sentence folded in: empty for no state (a request from before the link) and for
    `linked`. One of the hub's own sentences (`NOT_ASKED`) is shown alone."""
    if status == TO_LINK:
        return "Il CRM non ha ancora la richiesta: riprova o aspetta lo sweep."
    if status == ERROR and error in NOT_ASKED:
        return error or ""
    if status == ERROR:
        return _with_cause("Il CRM non ha risposto", error)
    if status == REJECTED:
        return _with_cause("Il CRM ha rifiutato la richiesta", error)
    return ""


def _with_cause(sentence: str, cause: str | None) -> str:
    if not cause:
        return f"{sentence}."
    return f"{sentence}: {cause}" if cause.endswith(".") else f"{sentence}: {cause}."
