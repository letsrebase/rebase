"""The two words of the team builder's caps that the day's count needs (REB-892).

A leaf module (it imports nothing of ours), so that `team_caps` and, through it, `users`
and the campaigns can count the day without importing the engine and with it the whole
model stack. `team_builder` re-exports both names, which stay importable from there.
"""

# The one sentence both caps answer (spec § 3.2, `team_caps.py`), and a task whose daily
# euro cap is spent (`LlmCapReached`): a visitor does the same thing either way.
BUSY_SENTENCE = "Troppe richieste in questo momento: riprova tra un minuto."
# `TeamProposal.model` when no model was asked: an empty catalogue costs no call, and
# nor does an attempt the caps refused or Claude did not answer (0028).
NO_CALL_MODEL = ""
