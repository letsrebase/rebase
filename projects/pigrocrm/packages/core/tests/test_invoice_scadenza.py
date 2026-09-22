"""The due date as a function of the agreed terms (REB-326).

Pure arithmetic, tested on its own: the service test proves the terms are read from the
customer, this file proves what «30 giorni data fattura fine mese» means once they are.
"""

from datetime import date

import pytest

from pigrocrm.core.invoices.scadenza import scadenza_da_termini


@pytest.mark.parametrize(
    ("emissione", "giorni", "fine_mese", "attesa"),
    [
        # Plain days: the profile's rule until now, unchanged.
        (date(2026, 9, 22), 25, False, date(2026, 10, 17)),
        (date(2026, 9, 22), 0, False, date(2026, 9, 22)),
        # DFFM: the days first, then the end of the month they land in. 22/09 + 30 =
        # 22/10, so the last day of October, which is what Emisfera's terms mean.
        (date(2026, 9, 22), 30, True, date(2026, 10, 31)),
        # Landing on the last day already changes nothing.
        (date(2026, 10, 1), 30, True, date(2026, 10, 31)),
        # February, leap and not: the month's own last day, never "the 30th".
        (date(2028, 1, 31), 15, True, date(2028, 2, 29)),
        (date(2027, 1, 31), 15, True, date(2027, 2, 28)),
        # Zero days with end of month is "end of this month".
        (date(2026, 12, 3), 0, True, date(2026, 12, 31)),
        # Across the year.
        (date(2026, 12, 15), 30, True, date(2027, 1, 31)),
    ],
)
def test_the_due_date_follows_the_terms(
    emissione: date, giorni: int, fine_mese: bool, attesa: date
) -> None:
    assert scadenza_da_termini(emissione, giorni, fine_mese=fine_mese) == attesa
