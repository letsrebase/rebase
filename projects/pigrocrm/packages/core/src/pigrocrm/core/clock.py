"""The fiscal half of the product's single clock.

`date.today()` and `datetime.now()` (no argument) both read the *process's* system
timezone, and this project does not control that end to end: `Dockerfile.api` pins no
`TZ`, so the `python:3.13-slim-trixie` image it starts from runs in UTC by default,
and a developer's own machine can be anywhere at all. The previous system's own defect
(`formatIsoDate` calling `toISOString()`, which moved an invoice issued at 23:30 CET
on 31 December into 1 January -- the wrong fiscal year on an immutable document) was a
UTC projection of an instant. A bare `date.today()` on a host running in UTC -- or in
any zone other than Italy's -- reproduces that exact defect through the standard
library's own default rather than through an explicit conversion, which is precisely
why it is not safe to treat as "no instant here to mis-project".

**Where the mechanism now lives.** It used to live here, as `datetime.now(ITALY_TZ)`.
Slice 6 §4.1 needed the same computation for `deals.chiuso_il` and `documents.stato_dal`
and asked for it to reuse "lo stesso meccanismo di fuso, non un secondo", so the
implementation moved to `pigrocrm.core.db.clock` -- `today_local(settings)` -- where the
zone comes from `Settings.timezone` (default `Europe/Rome`) and is validated against the
IANA database at construction. `oggi_in_italia()` stays as a name because eight fiscal
call sites read well with it and because renaming them is not what slice 6 is for, but
it is now a *delegation*, not a second implementation: freeze `db.clock._now` and both
move together. Two functions each calling `datetime.now(...)` themselves would be two
clocks that agree only until somebody sets `PIGROCRM_TIMEZONE`, and they would disagree
on the fiscal half, silently, on 31 December.

`ITALY_TZ` stays here and stays pinned to Europe/Rome, because it has one remaining use
that is genuinely about Italy and not about "today": converting an invoice's stored
`created_at` instant to the civil date it was created on. That is a projection of a
known instant, not a reading of the clock.

The `tzdata` package is a declared dependency specifically so none of this depends on
the host shipping the IANA database itself (a base image is not guaranteed to; Python's
own documentation recommends `tzdata` for exactly that reason).
"""

from datetime import date
from zoneinfo import ZoneInfo

from pigrocrm.core.db.clock import today_local

ITALY_TZ = ZoneInfo("Europe/Rome")


def oggi_in_italia() -> date:
    """Today's date in the issuer's own calendar.

    This is what spec 6.2 means by "today" when `data_emissione` is omitted, and it is
    the reference every "not in the future" / "not before this year" check in
    `InvoiceService.issue` compares against. There is no instant to mis-project here
    *only* because the conversion is explicit and always targets the emitter's zone, not
    because `datetime.now()` was avoided.

    Delegates to `today_local()` and holds no clock of its own -- see this module's
    docstring for why that is the whole point rather than an implementation detail.
    """
    return today_local()


__all__ = ["ITALY_TZ", "oggi_in_italia"]
