"""The names `anonymous_cv/writer.py` still imports from the old `cards.py` (REB-862).

`cards.py` was split into `card_*.py` (REB-892). The anonymous CV's writer belongs to REB-862,
which is in flight and may not be touched here, so these three names stay importable from this
module until that card moves its one import line: `NO_TEXT` (`card_types`), `_block` (now
`card_prompt.data_block`) and `stored_persona` (`card_persona`). Nothing else imports this
module; delete it with that change.
"""

from rebase_core.card_persona import stored_persona
from rebase_core.card_prompt import data_block as _block
from rebase_core.card_types import NO_TEXT

__all__ = ["NO_TEXT", "_block", "stored_persona"]
