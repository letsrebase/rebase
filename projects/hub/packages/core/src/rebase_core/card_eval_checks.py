"""The checks of a card that need no model (REB-842): what a rule can say about it, so the
judging call is asked only what a rule cannot.

* `anni`, recomputed from the CV's dated periods when the text allows a reliable parse,
  and left to the judge when it does not;
* the seniority, which is the band the years fall in (`card_level.banded`);
* the anonymity, read again by `card_guard.identifying` against every identifier the
  person's own data holds, whichever prompt wrote the card.

A parse of dates is reliable only when it does not matter what each period was: the CV's
periods are counted twice, once all of them and once without those that sit beside a
study («laurea», «università», «diploma»…), and the years are taken as a fact only when
the two counts are within `RELIABLE_SPREAD` years of each other. A CV with no year in it
at all is a fact too: it dates nothing. Anything between is the judge's.

Nothing here reads a log, the network or the database: the CV's text, the card and the
person's identifiers come in, a verdict comes out.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from rebase_core.card_guard import KnownIdentifiers, identifying
from rebase_core.card_level import banded
from rebase_core.team_schemas import Card

# A card's years may differ from the recomputed ones by this many: the CV's months and
# a year-only period are counted by a rule, the card by a model.
ANNI_TOLERANCE = 1
# The two counts of the CV's periods (all, and without the studies) may differ by this
# many years for the parse to be called reliable.
RELIABLE_SPREAD = 1
# The prompt leaves out periods shorter than this many months (internships).
MIN_MONTHS = 3

# The first three letters name the month (`_MONTH_NUMBER`): «Sett», «Sept», «Gennaio».
_MONTH = (
    r"(?:gen|feb|mar|apr|mag|may|giu|jun|lug|jul|ago|aug|set|sep|ott|oct|nov|dic|dec|jan)"
    r"[a-z]{0,8}"
)
_YEAR = r"(?:19|20)\d{2}"
_DATE = rf"(?:(?:{_MONTH}\.?\s+|\d{{1,2}}\s*[/.]\s*)?{_YEAR})"
_NOW = (
    r"(?:oggi|presente|present|now|current|attuale|attualmente|in\s+corso|ongoing|"
    r"ad\s+oggi|today)"
)
_SEPARATOR = r"(?:\s*[-\u2010-\u2015>]+\s*|\s+(?:a|al|fino\s+al?|to|until|till)\s+)"
_RANGE = re.compile(
    rf"(?<![\w/])(?P<start>{_DATE}){_SEPARATOR}(?P<end>{_DATE}|{_NOW})(?![\w/]*\d)",
    re.IGNORECASE,
)
_ANY_YEAR = re.compile(rf"(?<!\d){_YEAR}(?!\d)")
_PARTS = re.compile(rf"(?:(?P<month>{_MONTH}|\d{{1,2}})\.?\s*[/.\s]?\s*)?(?P<year>{_YEAR})", re.I)
_MONTH_NUMBER = {
    name: number
    for number, names in enumerate(
        (
            ("gen", "jan"),
            ("feb",),
            ("mar",),
            ("apr",),
            ("mag", "may"),
            ("giu", "jun"),
            ("lug", "jul"),
            ("ago", "aug"),
            ("set", "sep"),
            ("ott", "oct"),
            ("nov",),
            ("dic", "dec"),
        ),
        start=1,
    )
    for name in names
}
# What a period beside it says it is: a study, not a profession.
_STUDY = re.compile(
    r"\b(?:laure\w*|universit\w*|university|college|diplom\w*|master|bachelor|msc|bsc|"
    r"ph\.?d|dottorato|liceo|scuol\w*|school|istituto|politecnic\w*|accademia|academy|"
    r"bootcamp|thesis|tesi|studi|education|istruzione|formazione|degree|ateneo)\b",
    re.IGNORECASE,
)


def _index(token: str, today: date, *, end: bool) -> int | None:
    """The month a date names as `year * 12 + month - 1`; a bare year starts in January
    and ends in December; a «present» is this month. `None` for a date that is not one."""
    if re.fullmatch(_NOW, token.strip(), re.IGNORECASE):
        return today.year * 12 + today.month - 1
    parts = _PARTS.fullmatch(token.strip())
    if parts is None:
        return None
    year = int(parts["year"])
    month = parts["month"]
    if month is None:
        number = 12 if end else 1
    elif month.isdigit():
        number = int(month)
        if not 1 <= number <= 12:
            return None
    else:
        number = _MONTH_NUMBER.get(month.lower()[:3], 0)
        if number == 0:
            return None
    return year * 12 + number - 1


@dataclass(frozen=True)
class Period:
    start: int
    end: int  # inclusive
    study: bool

    @property
    def months(self) -> int:
        return self.end - self.start + 1


def periods(text: str, today: date) -> list[Period]:
    """Every dated period the CV writes, `Period.study` when the line or one beside it
    names a study. Periods that end before they start, start in the future or last under
    `MIN_MONTHS` months are left out, as the prompt leaves them out."""
    lines = text.split("\n")
    found: list[Period] = []
    offset = 0
    starts = []
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    for hit in _RANGE.finditer(text):
        begin = _index(hit["start"], today, end=False)
        finish = _index(hit["end"], today, end=True)
        if begin is None or finish is None or finish < begin:
            continue
        if begin > today.year * 12 + today.month - 1:
            continue
        finish = min(finish, today.year * 12 + today.month - 1)
        number = max(i for i, at in enumerate(starts) if at <= hit.start())
        near = " ".join(lines[max(0, number - 1) : number + 2])
        period = Period(begin, finish, study=_STUDY.search(near) is not None)
        if period.months >= MIN_MONTHS:
            found.append(period)
    return found


def years_of(found: list[Period]) -> int:
    """Whole years the periods cover, overlapping ones counted once."""
    merged: list[list[int]] = []
    for period in sorted(found, key=lambda p: (p.start, p.end)):
        if merged and period.start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], period.end)
        else:
            merged.append([period.start, period.end])
    return sum(end - start + 1 for start, end in merged) // 12


YearsMode = Literal["admin", "undated", "recomputed", "judged"]


@dataclass(frozen=True)
class YearsCheck:
    """What the rule says of a card's `anni`. `ok` is `None` when the CV does not allow a
    reliable parse (`mode` `judged`): the judging call then decides. `low` and `high` are
    the years of the CV's periods without and with the studies, `None` when no period was
    found."""

    mode: YearsMode
    ok: bool | None
    low: int | None = None
    high: int | None = None


def check_years(
    card: Card,
    cv_text: str,
    *,
    hub_completed_months: int = 0,
    anni_admin: int | None = None,
    today: date | None = None,
) -> YearsCheck:
    """`anni` against the CV's dates, the hub's completed engagements and an admin's
    word. An admin's years are a fact: the card has to be within `ANNI_TOLERANCE` of
    them. Otherwise the years may be at most what every dated period of the CV and the hub
    add up to, and, when the parse is reliable, at least what the periods without a study
    add up to; a CV that dates nothing gives `None` (the hub's years apart). A card with no
    years where the CV has professional periods is a mismatch."""
    today = today or date.today()
    got = card.anni
    if anni_admin is not None:
        return YearsCheck("admin", got is not None and abs(got - anni_admin) <= ANNI_TOLERANCE)
    hub_years = hub_completed_months // 12
    found = periods(cv_text, today)
    if not found:
        if _ANY_YEAR.search(cv_text) is None:
            return YearsCheck("undated", got is None or got <= hub_years + ANNI_TOLERANCE)
        return YearsCheck("judged", None)
    high = years_of(found)
    low = years_of([p for p in found if not p.study])
    if got is not None and got > high + hub_years + ANNI_TOLERANCE:
        return YearsCheck("recomputed", False, low, high)  # more than every period adds up to
    if high - low > RELIABLE_SPREAD:
        return YearsCheck("judged", None, low, high)
    if got is None:
        return YearsCheck("recomputed", low == 0, low, high)
    return YearsCheck("recomputed", got >= low - ANNI_TOLERANCE, low, high)


def seniority_in_band(card: Card) -> bool:
    """Whether the seniority is the one the years allow (`card_level.banded`): a card the
    writer returns always is; a card of another prompt may not."""
    return banded(card).seniority == card.seniority


def anonymity_leaks(card: Card, known: KnownIdentifiers) -> list[str]:
    """The fields of `card` that name the person or something of theirs, by every
    identifier the hub holds (`card_guard.identifying`): read for every prompt version, so
    the baseline and the candidate are held to the same rule."""
    return identifying(card, known)
