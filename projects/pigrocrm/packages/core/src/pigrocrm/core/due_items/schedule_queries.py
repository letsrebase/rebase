"""The reads behind the cash schedule, the payables mirror and the phone summary (spec
2026-11-09, due items and the cash schedule, § 4), for `CashScheduleService` and `payables.py`.

Every figure is the **remaining** amount of an open item (`due_items.sales.remaining_amount`), in
the schedule's currency, so a part-paid item counts for what is left and the totals agree with
the receivables the dashboard already prints. What counts as open here:

* `expected` items, whatever their date (a late one is late, an undated one is «senza scadenza»);
* `forecast` items dated today or later. A forecast whose date passed is an estimate nothing
  replaced and no longer money to come (the daily refresh drops it): it is in no figure.

`restricted` says whether the caller may read the fiscal source kinds; without it they are left
out of every figure, the rule `DueItemRepository.list_rows` applies to the list. `modules` is
what the space has on: a source kind of a module that is off (`SOURCE_KIND_MODULES`) is in no
figure either.
"""

import datetime as dt
from collections.abc import Set as AbstractSet
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import ColumnElement, Select, case, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import OPEN_STATES, RESTRICTED_SOURCE_KINDS, DueItem
from pigrocrm.core.due_items.sales import remaining_amount
from pigrocrm.core.money import round_money
from pigrocrm.core.organizations.models import Organization

# The kinds of a solo space's own taxes: the restricted kinds that are not what it pays its
# people (payroll) or a freelancer (engagement).
TAX_SOURCE_KINDS: frozenset[str] = RESTRICTED_SOURCE_KINDS - {
    "payroll",
    "engagement",
    "expense_note",
}

# The module each source kind belongs to, read from the code that writes it and the routes that
# guard it: a kind whose module is off is absent from every figure of the schedule, the
# payables and the summary. A kind not named here is the core's (`sales_invoice`, `sales_draft`).
# The IVA and withholding kinds are the purchases design's (`iva_settlements` is a `purchases`
# route; withholdings a company pays for others are due items it writes), `tax_instalment` the
# taxes design's, `payroll` the staff module's, `engagement` the collaborators' and
# `contract_occurrence` the contracts'.
SOURCE_KIND_MODULES: dict[str, str] = {
    "purchase": "purchases",
    "expense_note": "purchases",
    "recurring_expense": "purchases",
    "integration_vat": "purchases",
    "iva_settlement": "purchases",
    "withholding": "purchases",
    "cash_basis_vat": "purchases",
    "tax_instalment": "taxes",
    "payroll": "staff",
    "engagement": "collaborators",
    "contract_occurrence": "contracts",
}


def kinds_of_modules_off(modules: AbstractSet[str] | None) -> frozenset[str]:
    """The source kinds whose module is not in `modules` (`None`: every module is on)."""
    if modules is None:
        return frozenset()
    return frozenset(kind for kind, module in SOURCE_KIND_MODULES.items() if module not in modules)


class DayRow(NamedTuple):
    """What the open items of one due date, one direction and one state add up to."""

    due_date: dt.date
    direction: str
    state: str
    amount: Decimal
    number: int
    estimated: bool


class Owed(NamedTuple):
    """What the `expected` items of one direction add up to, and the late part of it."""

    total: Decimal
    late_total: Decimal
    late_count: int
    oldest_due: dt.date | None


class TaxRow(NamedTuple):
    due_item_id: UUID
    tax: str | None
    description: str | None
    due_date: dt.date | None
    state: str
    amount: Decimal


class BandAmount(NamedTuple):
    amount: Decimal
    number: int


class OverdueItem(NamedTuple):
    due_item_id: UUID
    counterparty: str | None
    description: str | None
    due_date: dt.date
    amount: Decimal


def _money(value: object) -> Decimal:
    """A summed amount as two decimals: `coalesce(sum(...), 0)` answers an integer literal on
    an empty set."""
    return round_money(Decimal(str(value)))


class ScheduleQueries:
    def __init__(
        self,
        session: Session,
        *,
        today: dt.date,
        legal_entity_id: UUID | None,
        restricted: bool,
        currency: str,
        modules: AbstractSet[str] | None = None,
    ) -> None:
        self.session = session
        self.today = today
        self.legal_entity_id = legal_entity_id
        self.restricted = restricted
        self.currency = currency
        self.modules_off = kinds_of_modules_off(modules)

    # ---- the shared predicate ------------------------------------------------------------

    def _live(self) -> list[ColumnElement[bool]]:
        """The open items the caller may see, in any currency and any direction."""
        clauses: list[ColumnElement[bool]] = [
            DueItem.state.in_(OPEN_STATES),
            or_(
                DueItem.state == "expected",
                DueItem.due_date.is_(None),
                DueItem.due_date >= self.today,
            ),
        ]
        if self.legal_entity_id is not None:
            clauses.append(DueItem.legal_entity_id == self.legal_entity_id)
        hidden = set(self.modules_off)
        if not self.restricted:
            hidden |= RESTRICTED_SOURCE_KINDS
        if hidden:
            clauses.append(DueItem.source_kind.not_in(sorted(hidden)))
        return clauses

    def _in_currency[*Ts](self, stmt: Select[*Ts]) -> Select[*Ts]:
        return stmt.where(*self._live(), DueItem.currency == self.currency)

    # ---- the weeks -----------------------------------------------------------------------

    def day_rows(self, first: dt.date, last: dt.date, directions: tuple[str, ...]) -> list[DayRow]:
        """The open items due from `first` through `last`, one row per due date, direction and
        state, for the weeks and the next seven days."""
        stmt = (
            select(
                DueItem.due_date,
                DueItem.direction,
                DueItem.state,
                func.coalesce(func.sum(remaining_amount()), 0),
                func.count(DueItem.id),
                func.bool_or(DueItem.due_date_estimated),
            )
            .where(
                *self._live(),
                DueItem.currency == self.currency,
                DueItem.direction.in_(directions),
                DueItem.due_date >= first,
                DueItem.due_date <= last,
            )
            .group_by(DueItem.due_date, DueItem.direction, DueItem.state)
        )
        return [
            DayRow(row[0], row[1], row[2], _money(row[3]), int(row[4]), bool(row[5]))
            for row in self.session.execute(stmt).all()
        ]

    def owed(self, direction: str) -> Owed:
        """The `expected` items of one direction: the total, and the part past its due date
        (strictly before today, as the receivables read «scaduto»)."""
        late = DueItem.due_date < self.today
        remaining = remaining_amount()
        row = self.session.execute(
            self._in_currency(
                select(
                    func.coalesce(func.sum(remaining), 0),
                    func.coalesce(func.sum(case((late, remaining), else_=0)), 0),
                    func.count(case((late, DueItem.id))),
                    func.min(case((late, DueItem.due_date))),
                )
            ).where(DueItem.direction == direction, DueItem.state == "expected")
        ).one()
        return Owed(_money(row[0]), _money(row[1]), int(row[2]), row[3])

    def has_open_items(self, *, outgoing: bool) -> bool:
        """Whether anything is open at all, in any currency. Without `outgoing` (a solo space
        has no outgoing column) only money in counts, and its own taxes."""
        wanted: ColumnElement[bool] = (
            DueItem.direction.in_(("in", "out"))
            if outgoing
            else or_(DueItem.direction == "in", DueItem.source_kind.in_(sorted(TAX_SOURCE_KINDS)))
        )
        return (
            self.session.execute(select(DueItem.id).where(*self._live(), wanted).limit(1)).first()
            is not None
        )

    def tax_rows(self) -> list[TaxRow]:
        """A solo space's own taxes, in date order with the undated last."""
        remaining = remaining_amount()
        rows = self.session.execute(
            self._in_currency(
                select(
                    DueItem.id,
                    DueItem.tax,
                    DueItem.description,
                    DueItem.due_date,
                    DueItem.state,
                    remaining,
                )
            )
            .where(DueItem.source_kind.in_(sorted(TAX_SOURCE_KINDS)))
            .order_by(DueItem.due_date.asc().nulls_last(), DueItem.id)
        ).all()
        return [TaxRow(r[0], r[1], r[2], r[3], r[4], _money(r[5])) for r in rows]

    # ---- the payables bands --------------------------------------------------------------

    def band_amounts(
        self, direction: str, bounds: dict[str, tuple[dt.date, dt.date]]
    ) -> dict[str, BandAmount]:
        """The `expected` items of one direction in the six ageing bands, keyed by band code
        (an empty band is absent). `bounds` holds the three dated bands' first and last day,
        `entro_30` starting today; before today is «expired», after the last bound
        `oltre_90` and no date `without_due_date`."""
        band = case(
            (DueItem.due_date.is_(None), "without_due_date"),
            (DueItem.due_date < self.today, "expired"),
            (DueItem.due_date <= bounds["entro_30"][1], "entro_30"),
            (DueItem.due_date <= bounds["da_31_a_60"][1], "da_31_a_60"),
            (DueItem.due_date <= bounds["da_61_a_90"][1], "da_61_a_90"),
            else_="oltre_90",
        ).label("band")
        rows = self.session.execute(
            self._in_currency(
                select(band, func.coalesce(func.sum(remaining_amount()), 0), func.count(DueItem.id))
            )
            .where(DueItem.direction == direction, DueItem.state == "expected")
            .group_by(band)
        ).all()
        return {row[0]: BandAmount(_money(row[1]), int(row[2])) for row in rows}

    def overdue_items(self, direction: str, limit: int) -> tuple[list[OverdueItem], int]:
        """The late `expected` items of one direction, the oldest first, and how many there are
        in all."""
        late = [
            DueItem.direction == direction,
            DueItem.state == "expected",
            DueItem.due_date < self.today,
        ]
        counterparty = case(
            (DueItem.organization_id.is_not(None), Organization.legal_name),
            (DueItem.tax.is_not(None), DueItem.tax),
        )
        rows = self.session.execute(
            self._in_currency(
                select(
                    DueItem.id,
                    counterparty,
                    DueItem.description,
                    DueItem.due_date,
                    remaining_amount(),
                )
                .select_from(DueItem)
                .outerjoin(Organization, Organization.id == DueItem.organization_id)
            )
            .where(*late)
            .order_by(DueItem.due_date, DueItem.id)
            .limit(limit)
        ).all()
        total = self.session.execute(
            self._in_currency(select(func.count(DueItem.id))).where(*late)
        ).scalar_one()
        return (
            [OverdueItem(r[0], r[1], r[2], r[3], _money(r[4])) for r in rows],
            int(total),
        )
