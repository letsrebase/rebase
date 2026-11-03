"""The one window the P&L fixtures and the reports that read them both name.

`invoices.checks.check_issue_date` refuses a `issue_date` that is in the future, and
refuses one before 1 January of the current year. Both refusals are right: an invoice
belongs to a fiscal year, and a year that has ended is closed to new entries. So a
fixture issuing on a literal `2026-03-20` was a suite with an expiry date on it -- every
P&L test would have failed at once on 1 January 2027, with a message about a closed year
that reads like a product defect and is not one.

Deriving the year is necessary but not sufficient, and that gap is why this lives in a
module of its own instead of a bare `ANNO = today_local().year` at the top of each
file. "This year, March" satisfies the closed-year limit and fails the other one from 1
January to 28 February, when March has not happened yet and the same check refuses the
date as `data futura`. Trading a failure every January for a failure every winter is not
a fix. The three days below are the only shape that holds on *every* possible run day:

  * `TODAY` is inside the current year and is not in the future, by definition.
  * `FIRST_OF_MONTH` is inside the current year -- a calendar month never straddles a
    year end -- and is never after `TODAY`. On 1 January the two are the same day and the
    window is one day long, which is still a window; there is no longer one available on
    that date, because 31 December belongs to the year the invoice may no longer enter.
  * `DAY_BEFORE` is the day before the window opens: always in the past, so a
    `time_entry` may carry it (`TimeEntryService` refuses only days after today), and
    never inside the window. It is the "just outside the period" the boundary tests need.
    The month *after* the window would read more naturally and cannot be used: it is in
    the future on every run day, and a future day is refused for hours as firmly as for
    invoices.

`today_local()` and never `date.today()`, for the reason `db/clock.py` sets out at
length: these constants must be the same "today" that `issue()` will compare them
against, and `date.today()` reads the process's own timezone -- UTC in the API image, and
anything at all on a developer's machine.
"""

from datetime import date, timedelta

from pigrocrm.core.db.clock import today_local

TODAY: date = today_local()
FIRST_OF_MONTH: date = TODAY.replace(day=1)
DAY_BEFORE: date = FIRST_OF_MONTH - timedelta(days=1)

__all__ = ["DAY_BEFORE", "TODAY", "FIRST_OF_MONTH"]
