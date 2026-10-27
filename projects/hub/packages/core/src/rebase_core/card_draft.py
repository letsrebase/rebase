"""What the model answers when it writes a card from more than the CV (REB-839, prompt
version 2): the `Card`, and beside it two things that are the writer's and never the
card's.

* `fonti`: for each field of the card, the sources the model says supported it. It is the
  model's claim: `verify_fonti` keeps only the sources that exist for this person, and the
  admin reads what is left as «secondo il modello».
* `employers`: every employer, client or organisation the CV names, whether or not the
  card mentions it. The anonymity guard reads them (`card_guard.identifying`), and that
  is all they are for: they are held in memory for the one write, never stored on the
  row, in a usage record, a log line or a conflict, so a leak cannot start there.

`Card` itself does not change: the public shape, the cloud's and the talent list's read
only it. The conflicts the code finds between the CV and another source are computed here
too (`conflicts`), never asked of the model.
"""

from typing import Annotated, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from rebase_core.team_schemas import Card
from rebase_core.validation import SafeStr

# The fields of a card the model names sources for, in the card's own order.
FONTI_FIELDS = (
    "ruolo",
    "anni",
    "seniority",
    "competenze",
    "settori",
    "lingue",
    "luogo",
    "sintesi",
)
CV = "cv"
HUB = "hub"
GITHUB = "github"

# `cv`, `github`, `hub` or `link:<n>`: short on purpose, so a model that writes a sentence
# there is a shape failure, not a stored paragraph.
SourceKey = Annotated[str, StringConstraints(max_length=16)]
SourceKeys = Annotated[list[SourceKey], Field(max_length=10)]


class CardFonti(BaseModel):
    """The sources that supported each field, one list per field of the card. A fixed
    object rather than a free map: every provider's native structured output takes it,
    and a field the model forgot is a shape failure."""

    model_config = ConfigDict(extra="forbid")

    ruolo: SourceKeys
    anni: SourceKeys
    seniority: SourceKeys
    competenze: SourceKeys
    settori: SourceKeys
    lingue: SourceKeys
    luogo: SourceKeys
    sintesi: SourceKeys

    def as_dict(self) -> dict[str, list[str]]:
        return {name: list(getattr(self, name)) for name in FONTI_FIELDS}


class CardDraft(BaseModel):
    """The model's whole answer under prompt version 2: `card`, then the private `fonti`
    and `employers`. Only `card` ever leaves the writer as the card."""

    model_config = ConfigDict(extra="forbid")

    card: Card
    fonti: CardFonti
    employers: list[Annotated[SafeStr, Field(max_length=120)]] = Field(max_length=60)


def verify_fonti(fonti: dict[str, list[str]], available: set[str]) -> dict[str, list[str]]:
    """`fonti` with every source that does not exist for this person dropped (`link:9`
    for a person with two links, `github` for one with no account read), each source once,
    in the order the model gave. Every field is present in the answer."""
    return {
        name: list(dict.fromkeys(key for key in fonti.get(name, []) if key in available))
        for name in FONTI_FIELDS
    }


class Conflict(NamedTuple):
    """A disagreement the code found, stored on the card row as a plain dict.
    `anni_diversi`: the hub's completed engagements add up to more years than the card's
    `anni_scheda` (the CV's dates) by more than one; `anni_hub` is the hub's months in
    whole years. `anni_scartata`: the card gave `anni_scheda` years but no source that
    dates a profession supports them, so the code dropped them; `anni_hub` is `None`."""

    kind: str
    anni_scheda: int | None
    anni_hub: int | None


# One year of slack: the CV's dates and the hub's months are counted differently (a
# month's rounding, a role that ended in the month it started).
YEARS_TOLERANCE = 1


def conflicts(cv_years: int | None, hub_completed_months: int) -> list[Conflict]:
    """What the CV's years and the hub's completed engagements disagree on. The card
    follows the CV's dates, so the only disagreement that says something is the hub
    showing more real work than the card counts: a short engagement against a long career
    is not one. `cv_years` is `None` when the CV dates no role: the hub may then give the
    years, and there is nothing to disagree with."""
    if cv_years is None:
        return []
    hub_years = hub_completed_months // 12
    if hub_years - cv_years > YEARS_TOLERANCE:
        return [Conflict("anni_diversi", cv_years, hub_years)]
    return []


def dated_by(fonti: dict[str, list[str]]) -> bool:
    """Whether the sources named for `anni` include one that dates a profession: the CV
    or the hub's own engagements. GitHub, a page and the wizard never set the years."""
    return bool({CV, HUB} & set(fonti.get("anni", [])))
