"""The Italian a reminder's detail is written in: a number with its noun in the right form
(«1 giorno», «5 giorni», «1,5 giornate»), decimals with a comma. The one place the
detectors take their plurals from, so no detail ever reads «1 giorni».

Only exactly one is singular, so `1,5 giornate` and `0 giorni` take the plural.
"""

from __future__ import annotations

from decimal import Decimal


def number(value: int | Decimal) -> str:
    """`5`, `1,5`, `12`: whole numbers without a decimal part, never a dot."""
    if isinstance(value, int):
        return str(value)
    text = f"{value.normalize():f}"
    return text.replace(".", ",")


def counted(value: int | Decimal, one: str, many: str) -> str:
    """`1 giorno`, `5 giorni`, `1,5 giornate`."""
    return f"{number(value)} {one if value == 1 else many}"


def format_days(value: int) -> str:
    return counted(value, "day", "days")


def work_units(value: int | Decimal) -> str:
    return counted(value, "work_unit", "work_units")


def format_entries(value: int) -> str:
    return counted(value, "voce", "entries")


def format_hours(value: int | Decimal) -> str:
    return counted(value, "ora", "hours")


def format_days_ago(value: int) -> str:
    """`ieri` for one day, `5 giorni fa` otherwise."""
    return "ieri" if value == 1 else f"{format_days(value)} fa"


def ago(days: int) -> str:
    """`today` for none, then as `days_fa`."""
    return "today" if days == 0 else format_days_ago(days)
