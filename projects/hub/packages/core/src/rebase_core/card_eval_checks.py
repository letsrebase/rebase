"""The checks of a card that need no model (REB-842): what a rule can say about it, so the
judging call is asked only what a rule cannot.

* `anni`, recomputed from the CV's dated periods when the text allows a reliable parse, and
  left to the judge when it does not;
* the anonymity, read again by `card_guard.identifying` against every identifier the
  person's own data holds, whichever prompt wrote the card.

(The seniority is not a check of its own: `CardWriter.compose` brings it inside the years'
bands, `card_level.banded`, for every prompt version, so a card out of band never comes out.)

A parse of dates is **reliable** only when nothing in it depends on a guess:

* every year the CV writes belongs to a period that was parsed (a year in «Python (2019)»,
  in an open period «2019 -» or in «dal 2016» is a period the rule did not read, and the
  parse is then the judge's);
* a period with a year and no month may have lasted anywhere from the difference of the two
  years to that difference plus one, so the periods are counted twice more: at their
  shortest and at their longest; and the periods beside a study («laurea», «università»,
  «diploma»…) are counted out of the shortest. The two counts have to be within
  `RELIABLE_SPREAD` years of each other.

A CV with no year in it at all is a fact too: it dates nothing. Anything else is the judge's.

Nothing here reads a log, the network or the database: the CV's text, the card and the
person's identifiers come in, a verdict comes out.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from rebase_core.card_guard import KnownIdentifiers, identifying
from rebase_core.team_schemas import Card

# A card's years may differ from the recomputed ones by this many: the CV's months are
# counted by a rule, the card by a model.
ANNI_TOLERANCE = 1
# The shortest and the longest count of the CV's periods may differ by this many years for
# the parse to be called reliable.
RELIABLE_SPREAD = 1
# The prompt leaves out periods shorter than this many months (internships).
MIN_MONTHS = 3

# The first three letters name the month (`_MONTH_NUMBER`): «Sett», «Sept», «Gennaio».
_MONTH = (
    r"(?:gen|feb|mar|apr|mag|may|giu|jun|lug|jul|ago|aug|set|sep|ott|oct|nov|dic|dec|jan)"
    r"[a-z]{0,8}"
)
_YEAR = r"(?:19|20)\d{2}"
# «2019», «03/2019», «1/3/2019», «Marzo 2019».
_DATE = rf"(?:(?:(?:\d{{1,2}}\s*[/.]\s*)?\d{{1,2}}\s*[/.]\s*|{_MONTH}\.?\s+)?{_YEAR})"
_NOW = (
    r"(?:oggi|presente|present|now|current|attuale|attualmente|in\s+corso|ongoing|"
    r"ad\s+oggi|today)"
)
_SEPARATOR = r"(?:\s*[-\u2010-\u2015>]+\s*|\s+(?:a|al|fino\s+al?|to|until|till)\s+)"
_RANGE = re.compile(
    rf"(?<![\w/])(?P<start>{_DATE}){_SEPARATOR}"
    rf"(?P<end>{_DATE}|{_NOW}|\d{{2}}(?![\d/.]))(?![\w/]*\d)",
    re.IGNORECASE,
)
_ANY_YEAR = re.compile(rf"(?<!\d){_YEAR}(?!\d)")
_PARTS = re.compile(
    rf"(?:(?:\d{{1,2}}\s*[/.]\s*)?(?P<month>{_MONTH}|\d{{1,2}})\.?\s*[/.\s]?\s*)?(?P<year>{_YEAR})",
    re.IGNORECASE,
)
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
    r"\b(?:laure\w*|universit\w*|university|college|diplom\w*|master|bachelor|b\.?sc|m\.?sc|"
    r"ph\.?d|dottorato|liceo|scuol\w*|school|istituto|politecnic\w*|accademia|academy|"
    r"bootcamp|thesis|tesi|studi|education|istruzione|formazione|degree|ateneo)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Date:
    """A month as `year * 12 + month - 1`, as early and as late as the CV lets it be: the
    same for a date with a month, a bare year starting anywhere from January to its own
    July and ending from its June to December."""

    earliest: int
    latest: int


def _month(token: str, today: date, *, end: bool, start_year: int | None = None) -> _Date | None:
    """The month a date names, or `None` for text that is not one. A «present» is this
    month; a bare two-digit year (the end of «2015–19») takes `start_year`'s century."""
    now = today.year * 12 + today.month - 1
    token = token.strip()
    if re.fullmatch(_NOW, token, re.IGNORECASE):
        return _Date(now, now)
    if re.fullmatch(r"\d{2}", token):
        if start_year is None:
            return None
        year = start_year - start_year % 100 + int(token)
        year += 100 if year < start_year else 0
        return _Date(year * 12 + 5, year * 12 + 11)
    parts = _PARTS.fullmatch(token)
    if parts is None:
        return None
    year = int(parts["year"])
    month = parts["month"]
    if month is None:
        # Anywhere in the year; as a start, no later than its own July; as an end, no
        # earlier than its June.
        return _Date(year * 12 + 5, year * 12 + 11) if end else _Date(year * 12, year * 12 + 6)
    if month.isdigit():
        number = int(month)
        if not 1 <= number <= 12:
            return None
    else:
        number = _MONTH_NUMBER.get(month.lower()[:3], 0)
        if number == 0:
            return None
    exact = year * 12 + number - 1
    return _Date(exact, exact)


@dataclass(frozen=True)
class Period:
    """One dated period, in months. `start` and `end` (inclusive) are its longest reading;
    `short_start` and `short_end` the shortest, which differ only for a period written with
    bare years."""

    start: int
    end: int
    short_start: int
    short_end: int
    study: bool

    @property
    def months(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class Parsed:
    periods: list[Period]
    # Whether every year the text writes belongs to a period that was read.
    complete: bool


def parse(text: str, today: date) -> Parsed:
    """Every dated period the CV writes, `Period.study` when the line or one beside it
    names a study. Periods that end before they start, start in the future or last under
    `MIN_MONTHS` months are left out, as the prompt leaves them out; the first two are a
    period the rule did not read."""
    lines = text.split("\n")
    found: list[Period] = []
    covered: list[tuple[int, int]] = []
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line) + 1
    now = today.year * 12 + today.month - 1
    for hit in _RANGE.finditer(text):
        begin = _month(hit["start"], today, end=False)
        started_in = _ANY_YEAR.search(hit["start"])
        if begin is None or started_in is None:
            continue
        finish = _month(hit["end"], today, end=True, start_year=int(started_in[0]))
        if finish is None or begin.earliest > now or finish.latest < begin.earliest:
            continue
        covered.append(hit.span())
        number = max(i for i, at in enumerate(offsets) if at <= hit.start())
        near = " ".join(lines[max(0, number - 1) : number + 2])
        period = Period(
            begin.earliest,
            min(finish.latest, now),
            min(begin.latest, now),
            min(finish.earliest, now),
            study=_STUDY.search(near) is not None,
        )
        if period.months >= MIN_MONTHS:
            found.append(period)
    complete = all(
        any(start <= year.start() < end for start, end in covered)
        for year in _ANY_YEAR.finditer(text)
    )
    return Parsed(found, complete)


def periods(text: str, today: date) -> list[Period]:
    return parse(text, today).periods


def _years(spans: list[tuple[int, int]]) -> int:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if end < start:
            continue
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return sum(end - start + 1 for start, end in merged) // 12


def years_of(found: list[Period]) -> int:
    """Whole years the periods cover at their longest, overlapping ones counted once."""
    return _years([(period.start, period.end) for period in found])


def _short_years(found: list[Period]) -> int:
    return _years([(period.short_start, period.short_end) for period in found if not period.study])


YearsMode = Literal["admin", "undated", "recomputed", "judged"]


@dataclass(frozen=True)
class YearsCheck:
    """What the rule says of a card's `anni`. `ok` is `None` when the CV does not allow a
    reliable parse (`mode` `judged`): the judging call then decides. `low` is the years of
    the CV's periods at their shortest and without the studies, `high` at their longest and
    with them; `None` when no period was found."""

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
    them. A CV that writes no year at all dates nothing, so the card has none (the hub's
    years apart). Otherwise, only when the parse is reliable (the module docstring): the
    years may be at most what every period adds up to, the hub's completed years included,
    and at least what the periods at their shortest and without a study add up to; a card
    with no years where the CV has professional periods is a mismatch."""
    today = today or date.today()
    got = card.anni
    if anni_admin is not None:
        return YearsCheck("admin", got is not None and abs(got - anni_admin) <= ANNI_TOLERANCE)
    hub_years = hub_completed_months // 12
    parsed = parse(cv_text, today)
    if not parsed.periods and _ANY_YEAR.search(cv_text) is None:
        return YearsCheck("undated", got is None or got <= hub_years + ANNI_TOLERANCE)
    if not parsed.periods or not parsed.complete:
        return YearsCheck("judged", None)
    high = years_of(parsed.periods)
    low = _short_years(parsed.periods)
    if high - low > RELIABLE_SPREAD:
        return YearsCheck("judged", None, low, high)
    if got is None:
        return YearsCheck("recomputed", low == 0, low, high)
    within = low - ANNI_TOLERANCE <= got <= high + hub_years + ANNI_TOLERANCE
    return YearsCheck("recomputed", within, low, high)


def anonymity_leaks(card: Card, known: KnownIdentifiers) -> list[str]:
    """The fields of `card` that name the person or something of theirs, by every
    identifier the hub holds (`card_guard.identifying`): read for every prompt version, so
    the baseline and the candidate are held to the same rule."""
    return identifying(card, known)
