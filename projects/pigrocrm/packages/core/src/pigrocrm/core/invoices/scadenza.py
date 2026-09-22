"""The due date of an invoice from the terms agreed with its customer (REB-326).

Two numbers describe every term this product knows: the days after the invoice date,
and whether the result then slides to the end of its month. «30 giorni data fattura fine
mese», the most common Italian spelling, is `(30, True)`; the fiscal profile's own
`giorni_scadenza` is `(n, False)`. The service reads the pair from the customer, falling
back to the profile's days, and calls this; nothing else in the product computes a due
date, so the digest, the reminders, the calendar and the XML all read one arithmetic.
"""

import calendar
from datetime import date, timedelta


def scadenza_da_termini(data_emissione: date, giorni: int, *, fine_mese: bool) -> date:
    """`data_emissione` plus `giorni`, then the last day of that month when `fine_mese`.

    The days come first and the month is the one they land in: 22 September plus thirty
    is 22 October, so end of month is 31 October. Applying the slide before the days
    (end of September, then thirty days) would answer 30 October, which is not what the
    customer signed. `calendar.monthrange` and not "day 30/31": February.
    """
    scadenza = data_emissione + timedelta(days=giorni)
    if not fine_mese:
        return scadenza
    ultimo = calendar.monthrange(scadenza.year, scadenza.month)[1]
    return scadenza.replace(day=ultimo)
