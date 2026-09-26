"""The client's price band (REB-511, spec § 3.3): what a company is told a talent costs,
never the talent's own rate.

The client pays the freelancer's `tariffa_giornaliera` plus 40% (Ivan: «tariffa del
cliente che di base sarà quella del freelancer +40%»), shown as a band rather than a
figure («meglio fascia»): one of six bands of euro per day, each closed at its bottom
and open at its top, the last with no top at all. A team's band per day is the sum of
its people's bounds, and per month the same times 22 working days («mensile a 22
giorni»). The arithmetic is the hub's, never the model's: Claude reads a band in the
catalogue and never the rate it came from.

Money is `Decimal` until it is placed; a band is whole euro.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

CLIENT_MARKUP = Decimal("1.4")
DAYS_PER_MONTH = 22
# `[low, high)` euro per day; `None` is «oltre», the band with no top.
BANDS: tuple[tuple[int, int | None], ...] = (
    (0, 300),
    (300, 400),
    (400, 500),
    (500, 650),
    (650, 800),
    (800, None),
)


def _euro(amount: int) -> str:
    """Whole euro written the Italian way: `17.600`, not `17,600` or `17600`."""
    return f"{amount:,}".replace(",", ".")


class Band(BaseModel):
    """A band of euro: one of `BANDS` for a person per day, or a team's sum of them per
    day or per month. `max` is `None` for a band with no top («oltre»)."""

    min: int
    max: int | None

    def bounds(self) -> str:
        """«400–500», «oltre 800»: the band as the catalogue gives it to the engine."""
        if self.max is None:
            return f"oltre {_euro(self.min)}"
        return f"{_euro(self.min)}–{_euro(self.max)}"

    def label(self, per: Literal["giorno", "mese"] = "giorno") -> str:
        """«400–500 € al giorno», «oltre 800 € al giorno», «17.600–23.100 € al mese»."""
        return f"{self.bounds()} € al {per}"


def band_containing(price: Decimal) -> Band:
    """The band a client's price per day falls in: a bound belongs to the band it opens,
    so 300 exactly is «300–400»."""
    for low, high in BANDS:
        if high is None or price < high:
            return Band(min=low, max=high)
    raise AssertionError("the last band has no top")  # pragma: no cover


def band_for(rate: Decimal | None) -> Band | None:
    """The client's band for a freelancer's own daily rate; `None` without a rate, which
    the page shows as «tariffa da definire»."""
    if rate is None:
        return None
    return band_containing(rate * CLIENT_MARKUP)


def team_bands(members: Sequence[Band | None]) -> tuple[Band | None, Band | None]:
    """The team's band per day and per month (22 days): the sum of every member's
    bounds, with no top if any member's band has none. `(None, None)` when a member has
    no band, since a sum that leaves somebody out would be a price nobody quoted, and
    for an empty team, which has no price at all."""
    if not members:
        return None, None
    bands = [band for band in members if band is not None]
    if len(bands) < len(members):
        return None, None
    low = sum(band.min for band in bands)
    tops = [band.max for band in bands]
    high = None if None in tops else sum(top for top in tops if top is not None)
    day = Band(min=low, max=high)
    month = Band(min=low * DAYS_PER_MONTH, max=None if high is None else high * DAYS_PER_MONTH)
    return day, month
