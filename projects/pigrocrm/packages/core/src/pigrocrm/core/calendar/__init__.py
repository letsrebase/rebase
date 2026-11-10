"""Il calendario: one month of what was worked and what falls due, in one read.

No model of its own. Everything here is a *view* over three tables that already exist --
`time_entries`, `attivita`, `invoices` -- which is the whole design of slice 10: the CRM
already knew all of this, in three places, and nobody could ask it by day.
"""

from pigrocrm.core.calendar.repository import CalendarRepository
from pigrocrm.core.calendar.schemas import (
    MONTH_PATTERN,
    CalendarDay,
    CalendarMonth,
    DayDealHours,
    DueInvoice,
)
from pigrocrm.core.calendar.service import CalendarService, month_bounds

__all__ = [
    "MONTH_PATTERN",
    "CalendarDay",
    "CalendarMonth",
    "CalendarRepository",
    "CalendarService",
    "DayDealHours",
    "DueInvoice",
    "month_bounds",
]
