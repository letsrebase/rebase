from collections.abc import Collection
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.money import round_hours
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.work_units.models import WorkUnit


class CalendarRepository:
    """The four reads a month needs, and nothing else.

    Its own repository rather than four methods added to four existing ones: the
    grouping is by day, which is a question no other screen asks -- the week grid groups
    by day *and* week, the register by row, the invoice list by state.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def hours_by_day_and_deal(
        self,
        date_from: date,
        date_to: date,
        *,
        user_id: UUID | None = None,
        legal_entity_id: UUID | None = None,
    ) -> list[tuple[date, UUID, Decimal]]:
        """`(day, deal, hours)` for every day of the window that has any, in one query.

        Rounded through `money.round_hours` and not `Decimal.quantize`: that module is
        this project's single rounding authority and rounds HALF_UP where `quantize`
        would default to half-even. A second copy of the rule is how two totals of the
        same hours start to disagree.

        `user_id` filters to one person's hours. `None` means everybody's, which is what
        an admin looking at the space's calendar asks for; the caller decides, because
        «whose calendar is this» is a question about the actor and not about SQL.
        """
        stmt = (
            select(TimeEntry.date, TimeEntry.deal_id, func.sum(TimeEntry.hours))
            .where(
                TimeEntry.deleted_at.is_(None),
                TimeEntry.date >= date_from,
                TimeEntry.date <= date_to,
            )
            .group_by(TimeEntry.date, TimeEntry.deal_id)
            .order_by(TimeEntry.date)
        )
        if user_id is not None:
            stmt = stmt.where(TimeEntry.user_id == user_id)
        if legal_entity_id is not None:
            # Through the deal, as `TimeEntryRepository.list` does (REB-623).
            stmt = stmt.where(
                TimeEntry.deal_id.in_(
                    select(Deal.id).where(Deal.legal_entity_id == legal_entity_id)
                )
            )
        return [
            # `func.sum` over a group is never null: a group exists because it has a
            # row. No `coalesce`, for the reason `TimeEntryRepository.week` records --
            # the prudent-looking default would hide a column becoming nullable.
            (row[0], row[1], round_hours(Decimal(row[2])))
            for row in self.session.execute(stmt).all()
        ]

    def deal_labels(self, deal_ids: Collection[UUID]) -> dict[UUID, tuple[str, str | None]]:
        """`{deal_id: (deal name, customer name)}`, in one query for the whole month.

        A left outer join, and no `deleted_at` filter on either side: a soft-deleted deal
        is still the deal those hours were logged against, and blanking its name would
        make an archived deal read like a deal that never existed. The same reasoning
        `PersonRepository.customer_names` records for an archived customer.
        """
        if not deal_ids:
            return {}
        rows = self.session.execute(
            select(Deal.id, Deal.name, Organization.legal_name)
            .join(Organization, Organization.id == Deal.customer_id, isouter=True)
            .where(Deal.id.in_(deal_ids))
        ).all()
        return {row[0]: (row[1], row[2]) for row in rows}

    def invoices_due(
        self, date_from: date, date_to: date, *, legal_entity_id: UUID | None = None
    ) -> list[tuple[Invoice, str | None]]:
        """Issued, unpaid invoices whose due date falls in the window, with the client's
        name.

        The filter is `_receivable_filter`'s own definition spelled out rather than
        imported: that helper is private to `invoices/repository.py`, and what this
        needs is the same three facts (a live `invoice`, `issued`, `unpaid`) plus a
        window. If the two ever diverge it must be because somebody decided they should.

        Ordered by `(data_scadenza, numero)` so a day with two invoices is stable
        between reads.
        """
        rows = self.session.execute(
            select(Invoice, Organization.legal_name)
            .join(Organization, Organization.id == Invoice.customer_id, isouter=True)
            .where(
                Invoice.deleted_at.is_(None),
                Invoice.type == "invoice",
                Invoice.status == "issued",
                Invoice.payment_status == "unpaid",
                Invoice.due_date.is_not(None),
                Invoice.due_date >= date_from,
                Invoice.due_date <= date_to,
                *(
                    [Invoice.legal_entity_id == legal_entity_id]
                    if legal_entity_id is not None
                    else []
                ),
            )
            .order_by(Invoice.due_date, Invoice.number)
        ).all()
        return [(row[0], row[1]) for row in rows]

    def work_units_in_window(
        self, date_from: date, date_to: date, *, legal_entity_id: UUID | None = None
    ) -> list[tuple[WorkUnit, str, str]]:
        """`(day, contract title, customer name)` for every contract day of the window that
        still counts, oldest first.

        `rejected` and `revoked` are left out: those two are exactly the outcomes that
        free the date for a fresh day (`uq_work_units_contract_date_live`), so a cell that
        drew them would show a date as taken that nothing holds. They stay in the register
        (`WorkUnitService.list`). The customer is joined with no `deleted_at` filter, for
        the reason `deal_labels` records. Through the contract for the legal entity, the way
        every contract read does (REB-623).
        """
        stmt = (
            select(WorkUnit, Contract.title, Organization.legal_name)
            .join(Contract, Contract.id == WorkUnit.contract_id)
            .join(Organization, Organization.id == Contract.customer_id)
            .where(
                WorkUnit.date >= date_from,
                WorkUnit.date <= date_to,
                WorkUnit.status.not_in(("rejected", "revoked")),
            )
            .order_by(WorkUnit.date, Contract.title, WorkUnit.id)
        )
        if legal_entity_id is not None:
            stmt = stmt.where(Contract.legal_entity_id == legal_entity_id)
        return [(row[0], row[1], row[2]) for row in self.session.execute(stmt).all()]
