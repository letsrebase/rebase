"""How a mail prints a date and an amount: the Italian way, whatever the host's locale."""

from datetime import date
from decimal import Decimal

from pigrocrm.core.money import round_money

DAYS_SHORT = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
MONTHS_SHORT = (
    "gen",
    "feb",
    "mar",
    "apr",
    "mag",
    "giu",
    "lug",
    "ago",
    "set",
    "ott",
    "nov",
    "dic",
)


def day_short(day: date) -> str:
    """`lun 8 set`. Hard-coded Italian abbreviations, never `strftime`'s `%a`/`%b` or the
    `locale` module: core runs wherever the process happens to run, and a date printed in
    the host's locale is a date that reads wrong the day somebody moves the container."""
    return f"{DAYS_SHORT[day.isoweekday() - 1]} {day.day} {MONTHS_SHORT[day.month - 1]}"


def euro(value: Decimal) -> str:
    """`1.800,00 €`. Italian grouping and decimal separators, the same recipe
    `gmail/solleciti.py`'s own `_euro` uses -- copied rather than imported, because a
    mail is not a reminder and the two are free to diverge without either breaking."""
    quantized = round_money(value)
    grouped = f"{quantized:,.2f}"
    return grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".") + " €"
