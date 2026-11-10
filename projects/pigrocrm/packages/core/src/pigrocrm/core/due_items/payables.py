"""The payables mirror (spec 2026-11-09, due items and the cash schedule, § 4): what we owe,
when it falls due, and what is late, in the shape of `ReceivablesDashboard`.

The same six bands as the receivables, with the same codes, labels and bounds, so the page
draws them with one component: «Scaduto» is strictly before today, «Entro 30 giorni» starts on
it, and a due date that is absent is its own band. Only the `expected` items are owed: a
forecast is not (the receivables count issued invoices, not drafts). The bands add up to
`total` by construction, since each is one `CASE` over the same rows.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pigrocrm.core.dashboard.schemas import BandDueDate
from pigrocrm.core.due_items.schedule_queries import BandAmount, ScheduleQueries
from pigrocrm.core.due_items.schedule_schemas import PayableRow, PayablesDashboard
from pigrocrm.core.invoices.receivables import BAND_LABELS, BANDS_RECEIVABLES
from pigrocrm.core.money import ZERO_MONEY, share

OVERDUE_SHOWN = 50
# The three dated bands, in days from today: first and last day offset.
_DATED_BANDS: dict[str, tuple[int, int]] = {
    "entro_30": (0, 30),
    "da_31_a_60": (31, 60),
    "da_61_a_90": (61, 90),
}


def band_bounds(today: dt.date) -> dict[str, tuple[dt.date, dt.date]]:
    """First and last day of the three dated bands."""
    return {
        code: (today + dt.timedelta(days=first), today + dt.timedelta(days=last))
        for code, (first, last) in _DATED_BANDS.items()
    }


def _bands(today: dt.date, found: dict[str, BandAmount]) -> tuple[list[BandDueDate], Decimal]:
    """The six bands in the receivables' order, an empty one a row at zero, and their sum."""
    bounds = band_bounds(today)
    largest = max((figure.amount for figure in found.values()), default=ZERO_MONEY)
    bands: list[BandDueDate] = []
    total = ZERO_MONEY
    for code in BANDS_RECEIVABLES:
        figure = found.get(code, BandAmount(ZERO_MONEY, 0))
        total += figure.amount
        date_from, date_to = bounds.get(code, (None, None))
        if code == "oltre_90":
            date_from = bounds["da_61_a_90"][1] + dt.timedelta(days=1)
        bands.append(
            BandDueDate(
                code=code,
                label=BAND_LABELS[code],
                date_from=date_from,
                date_to=date_to,
                amount=figure.amount,
                number=figure.number,
                share=share(figure.amount, largest),
                link=None,
            )
        )
    return bands, total


def build_payables(
    queries: ScheduleQueries, *, legal_entity_id: UUID | None, calculated_at: datetime
) -> PayablesDashboard:
    """The open `expected` outgoing items in six bands, with the oldest overdue ones listed."""
    today = queries.today
    bands, total = _bands(today, queries.band_amounts("out", band_bounds(today)))
    rows, overdue_count = queries.overdue_items("out", OVERDUE_SHOWN)
    return PayablesDashboard(
        calculated_at=calculated_at,
        today=today,
        legal_entity_id=legal_entity_id,
        total=total,
        bands=bands,
        overdue=[
            PayableRow(
                due_item_id=row.due_item_id,
                counterparty=row.counterparty,
                description=row.description,
                due_date=row.due_date,
                days_of_delay=(today - row.due_date).days,
                amount=row.amount,
            )
            for row in rows
        ],
        total_overdue=overdue_count,
    )


def empty_payables(
    *, today: dt.date, legal_entity_id: UUID | None, calculated_at: datetime
) -> PayablesDashboard:
    """The mirror of a space that shows no outgoing money (solo): six bands at zero."""
    bands, total = _bands(today, {})
    return PayablesDashboard(
        calculated_at=calculated_at,
        today=today,
        legal_entity_id=legal_entity_id,
        total=total,
        bands=bands,
        overdue=[],
        total_overdue=0,
    )
