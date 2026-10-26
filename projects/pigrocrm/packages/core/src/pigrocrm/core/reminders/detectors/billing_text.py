"""The Italian a billing reminder's detail is written in: a number with its noun in the
right form («1 giorno», «5 giorni», «1,5 giornate»), decimals with a comma.

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


def giorni(value: int) -> str:
    return counted(value, "giorno", "giorni")


def giornate(value: int | Decimal) -> str:
    return counted(value, "giornata", "giornate")


def voci(value: int) -> str:
    return counted(value, "voce", "voci")


def ore(value: int | Decimal) -> str:
    return counted(value, "ora", "ore")


def giorni_fa(value: int) -> str:
    """`ieri` for one day, `5 giorni fa` otherwise."""
    return "ieri" if value == 1 else f"{giorni(value)} fa"
