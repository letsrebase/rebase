"""Which staff member a payslip names (REB-1045): the person a reader (the space's model) found
on the document, matched to the people of the legal entities the caller sees.

A payslip carries a name and no id, so the match is the name and nothing cleverer: the same
words in any order, accents, case and punctuation aside («Rossi, Mario» and «MARIO ROSSI» are one
name), among the people in service in the month the payslip pays. Exactly one person is a match;
none or several is no match, and the reader's proposal waits for a person to say who it is
(a person types the figures on that person's page, or the user's agent proposes with the id).
The match runs under the caller's row policy, so only people of the legal entities in the
caller's scope can be found.
"""

import datetime as dt
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.staff.estimate import first_of
from pigrocrm.core.staff.models import StaffMember

_NOT_A_WORD = re.compile(r"[^a-z0-9]+")


def name_words(name: str) -> tuple[str, ...]:
    """The words of a name, lower case, without accents or punctuation, in alphabetical order."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return tuple(sorted(word for word in _NOT_A_WORD.split(plain) if word))


def match_staff_member(
    session: Session, name: str | None, month: dt.date | None
) -> StaffMember | None:
    """The one person in service in `month` (any day of it; any month when `None`) whose name
    is `name`, or `None` when there is none or more than one."""
    words = None if name is None else name_words(name)
    if not words:
        return None
    wanted = None if month is None else first_of(month)
    found: list[StaffMember] = []
    for member in session.execute(select(StaffMember)).scalars():
        if name_words(member.name) != words:
            continue
        if wanted is not None and (
            wanted < first_of(member.start_date)
            or (member.end_date is not None and wanted > first_of(member.end_date))
        ):
            continue
        found.append(member)
    return found[0] if len(found) == 1 else None
