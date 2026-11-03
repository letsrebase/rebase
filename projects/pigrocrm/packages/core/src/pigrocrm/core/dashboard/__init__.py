"""The dashboards' shapes, and nothing that would close an import cycle.

`DashboardService` is deliberately **not** re-exported here, and this is the one place the
reason is written down. `deals/repository.py` and `documents/repository.py` import this
package's `schemas` module -- that is where the aggregate row shapes live (§3) -- and
importing any submodule runs this `__init__` first. Re-exporting the service would make
that `__init__` import `dashboard.service`, which imports those same two repositories, so
whichever of `deals` and `dashboard` a process happened to import first would decide
whether the product started at all: `from pigrocrm.core.deals...` first gives
`ImportError: cannot import name 'DealRepository' from partially initialized module`.

Import it from its own module instead -- `from pigrocrm.core.dashboard.service import
DashboardService` -- which is what `apps/api` and the tests do. `analytics/__init__.py` and
`invoices/__init__.py` already keep their services out of their package `__init__` for a
milder version of the same reason.
"""

from pigrocrm.core.dashboard.schemas import (
    MAX_PERIOD_DAYS,
    ClosedInPeriod,
    CommercialDashboard,
    PendingOffer,
    Period,
    PeriodQuery,
    PipelineStageSummary,
)

__all__ = [
    "MAX_PERIOD_DAYS",
    "ClosedInPeriod",
    "CommercialDashboard",
    "PendingOffer",
    "Period",
    "PeriodQuery",
    "PipelineStageSummary",
]
