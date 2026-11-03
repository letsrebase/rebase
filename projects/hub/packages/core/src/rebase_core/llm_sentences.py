"""The one Italian sentence a page shows when the model did not answer, per task (REB-892).

A leaf module (it imports nothing of ours) so that both the seam (`llm.py`) and the prompt
helper (`llm_prompt.py`) can use it without a cycle. The sentence names what the visitor was
waiting for: a team proposal's outage must not say «team» on a card, an anonymous CV or a CRM
draft. The team builder's sentence is the original one, word for word.
"""

# Keys are `AI_TASKS` (`models/ai.py`).
UNAVAILABLE_SENTENCES: dict[str, str] = {
    "team_proposal": "Non riesco a proporre un team adesso: riprova tra poco.",
    "card": "Non riesco a scrivere la scheda adesso: riprova tra poco.",
    "anonymous_cv": "Non riesco a scrivere il CV anonimo adesso: riprova tra poco.",
    "crm_lead": "Non riesco a preparare la bozza della richiesta adesso: riprova tra poco.",
}

# A task this table does not know (a future one, a test's): says nothing about what failed.
GENERIC_SENTENCE = "Il modello non risponde in questo momento: riprova tra poco."


def unavailable_sentence(task: str) -> str:
    """The sentence for `task`'s outage, a refusal, a cut answer or a body that is not the
    shape: ours, never the model's words."""
    return UNAVAILABLE_SENTENCES.get(task, GENERIC_SENTENCE)
