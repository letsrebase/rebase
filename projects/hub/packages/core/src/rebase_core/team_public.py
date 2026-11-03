"""What a public read of a team proposal may say about a place (REB-598; REB-892 split it
out of `team_builder.py`).

A card's `location` is withheld from the public page, and so is anything that names it: the
prompt forbids a place in a motivazione and lets the summary name only the place the
visitor wrote, but a model writing against its rules, or a card written before the rule,
must not give the place back. These pure functions are the check, applied when a proposal
is read (`TeamBuilder._read`), never when it is stored: the row, the admin's and the
cloud's reads keep the model's words.
"""

import logging
import re
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from rebase_core.team_schemas import Card, CardRead

logger = logging.getLogger(__name__)

# What a public read says of a member in place of a motivazione that names the place on
# their card, which the same read withholds (`public_reason`).
PLACE_WITHHELD_REASON = "Profilo adatto al ruolo."
# And in place of a card's summary that names that place (`public_card`).
PLACE_WITHHELD_SUMMARY = "La sintesi di questo profilo non è pubblica."
# And in place of a proposal's summary that names the place on a member's card when the
# description does not (`public_summary`); what the summary explained about how the
# people work is on each member's card, so the sentence says where to look.
PLACE_WITHHELD_SUMMARY = (
    "Il riassunto di questa proposta non è pubblico: la modalità di lavoro di ogni persona "
    "proposta è sulla sua scheda."
)
# Words of a card's `location` that say what kind of place it is rather than which one:
# «provincia di Bergamo» is Bergamo, and a reason that says «in provincia» names nothing
# (a word under four letters is never read: «sud», «est»).
_PLACE_GENERIC = frozenset(
    [
        "area",
        "centro",
        "città",
        "dintorni",
        "italia",
        "nord",
        "ovest",
        "provincia",
        "regione",
        "zona",
    ]
)


def _names_place(text: str, location: str | None) -> bool:
    """Whether `text` names the place of `location`: any word of four letters or more of it,
    but the words for a kind of place (`_PLACE_GENERIC`), as a whole word written with a
    capital, in any case but all lower case, as the card writer reads a surname
    (`card_guard._names`): «Alto Adige» and «BERGAMO» are the place, «di alto livello» is
    not."""
    return _names_any(text, _place_words(location))


def _place_words(location: str | None, *, at_least: int = 4) -> set[str]:
    """The words of a card's `location` that name the place, case folded: those of
    `at_least` letters, four by default, so «sud» and «est» are never read."""
    if not location:
        return set()
    return {
        word.casefold()
        for word in re.findall(rf"[^\W\d_]{{{at_least},}}", location)
        if word.casefold() not in _PLACE_GENERIC
    }


def _written(dove: str | None, description: str) -> set[str]:
    """The words of `dove`, the place the model read as the client's, that the visitor
    wrote in the description themselves, in any case: the only place words a public
    read may give back, since the visitor already holds them."""
    return {
        word
        for word in _place_words(dove, at_least=2)
        if re.search(rf"\b{re.escape(word)}\b", description, re.IGNORECASE)
    }


def public_place(location: dict[str, Any], description: str) -> dict[str, Any]:
    """The `location` a public page reads: `dove` only when every place word of it is the
    visitor's own, from the description; a `dove` the model wrote on its own (a member's
    card city, say) reads as `None`, and `locale` is kept as it is."""
    dove = location.get("dove")
    words = _place_words(dove, at_least=2)
    if not words or words != _written(dove, description):
        return {**location, "dove": None}
    return location


def _names_any(text: str, words: set[str], *, in_any_case: bool = False) -> bool:
    """Whether `text` holds any of `words` as a whole word: written with a capital, as
    `_names_place` reads a card's fields, or in any case when `in_any_case` is set, as the
    summary is read, where «vive a verona» is the place all the same and a match on «di
    alto livello» costs one hub sentence rather than a leak."""
    return any(
        in_any_case or not found.group().islower()
        for word in words
        for found in re.finditer(rf"\b{re.escape(word)}\b", text, re.IGNORECASE)
    )


def public_summary(
    proposal_id: UUID,
    summary: str,
    dove: str | None,
    description: str,
    cards: Sequence[Card],
) -> str:
    """The summary a public page reads. The prompt lets it name the place the visitor
    wrote and no place of a person; one that names the place on a member's card anyway
    would give back what the public read withholds, so it reads as the hub's own sentence
    instead (REB-598, the same test as `public_reason`). The place the description
    names as the client's is the visitor's own word and no secret to them, so a member
    who is there does not withhold the summary. That pass needs both signals: the word is
    in `location.dove`, the place the model read as the client's, and it is in the
    description the visitor wrote. Either alone is not enough: a word of the description
    that only looks like a place («all'alba» for a card in Alba) is not in `dove`, and
    `dove` is the model's own text, which a model writing against its rules could fill
    with a member's card city, so a word there that the visitor never wrote is no pass
    either. Every other place is read in any case, since prose writes a city in lower
    case as readily as not, and a match on a common word costs one sentence while a miss
    costs a card's place; a town shorter than four letters is read as a capitalised
    word only. The row, the admin's and the cloud's reads keep the model's words."""
    written = _written(dove, description)
    long_words = {word for card in cards for word in _place_words(card.location)}
    # A town shorter than four letters («Rho») is read too, as a capitalised word only:
    # in any case, a member from Ora would withhold every summary that says «ora».
    short_words = {word for card in cards for word in _place_words(card.location, at_least=2)}
    if _names_any(summary, long_words - written, in_any_case=True) or _names_any(
        summary, short_words - long_words - written
    ):
        logger.warning("team proposal %s: the summary names a member's place", proposal_id)
        return PLACE_WITHHELD_SUMMARY
    return summary


def public_reason(reasoning: str, card: CardRead) -> str:
    """The motivazione a public page reads. The prompt forbids a place in it, and the
    public read withholds the card's `location`; a reason that names that place anyway
    would give it back, so it reads as the hub's own sentence instead, and the row, the
    admin's and the cloud's reads keep the model's words."""
    return PLACE_WITHHELD_REASON if _names_place(reasoning, card.location) else reasoning


def public_card(card: CardRead, role: str) -> CardRead:
    """The card a public page reads: no `location`, and nothing else that names it. The
    card's prompt keeps the place in `location` alone, and a card written before that rule,
    or against it, still must not give the place back: a role that names it reads as
    the role in this team, a summary as `PLACE_WITHHELD_SUMMARY`, and a skill or a
    sector that names it is left out. The stored card is not touched."""
    place = card.location
    return card.model_copy(
        update={
            "location": None,
            "role": role if _names_place(card.role, place) else card.role,
            "profile_summary": (
                PLACE_WITHHELD_SUMMARY
                if _names_place(card.profile_summary, place)
                else card.profile_summary
            ),
            "skills": [skill for skill in card.skills if not _names_place(skill, place)],
            "industries": [sector for sector in card.industries if not _names_place(sector, place)],
        }
    )
