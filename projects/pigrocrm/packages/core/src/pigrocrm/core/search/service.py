"""The one public entry point of the global search.

It is a fan-out and nothing else: term normalisation, five repository calls, and a fixed
group order. There is deliberately no cross-entity ranking -- the palette shows five per
class with each class's own count (§8.5), so a global order across classes would be a
figure nobody looks at, computed on every keystroke.

**No authorisation check.** Not an omission: search reads the same rows the five list
endpoints already return to every role, slice 4 §11 gives every read to every role, and
spec §13 states that this slice adds no role and no authorisation rule. Adding a check
here would put a security rule on a read-only surface, which is the place nobody looks for
one. `actor` is still taken, because every service method in this project takes it and a
signature that differs invites a call site that forgets it.

**No transaction.** Five branches' worth of `SELECT`s with no isolation requirement
between them: a search is
not a reconciliation, and a hit that vanishes between the palette and the click lands on a
404 the palette already handles. The dashboards are the surface that needs one instant
(§7.1); this one does not, and pretending otherwise would put `REPEATABLE READ` on the
hottest read path in the product for no property gained.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.search.repository import SearchRepository
from pigrocrm.core.search.schemas import SearchQuery, SearchResults


class SearchService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = SearchRepository(session)

    def search_everything(self, query: SearchQuery, actor: Actor) -> SearchResults:
        """Every group is present even when empty.

        A missing group and an empty group render identically in a palette, so the client
        would have to guess which it was -- and the whole point of §8.6 is that the client
        never guesses what it is looking at.

        The group order is fixed, not sorted by count: a palette whose sections move
        between keystrokes cannot be used with the keyboard, which is the only way a
        palette is used.
        """
        # `strip()` and nothing else. No lowercasing here: the scoring expression
        # lowercases on both sides where it needs to, and `similarity()` normalises
        # internally, so a second normalisation would only make the returned `termine`
        # differ from what the user typed.
        term = query.termine.strip()
        limit = query.limite
        azienda = query.azienda_id
        return SearchResults(
            termine=term,
            gruppi=[
                self.repo.customers(term, limit, azienda),
                self.repo.people(term, limit, azienda),
                self.repo.deals(term, limit, azienda),
                self.repo.documents(term, limit, azienda),
                # Fifth and last. Added by Task C12, which is also what made `SearchEntity`
                # true: it declared five entities from the start and four were searched, so
                # an invoice number answered "nothing found" when it had never been looked
                # for.
                self.repo.invoices(term, limit, azienda),
            ],
        )
