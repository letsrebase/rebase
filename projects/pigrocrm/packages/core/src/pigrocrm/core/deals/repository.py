from collections.abc import Collection
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, Numeric, and_, case, delete, func, literal, select, true
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.dashboard.schemas import ClosedInPeriod, PipelineStageSummary
from pigrocrm.core.db import decode_cursor, escape_like, keyset_predicate, order_by, today_local
from pigrocrm.core.deals.models import Deal, DealValueLine
from pigrocrm.core.deals.schemas import DEAL_SORTS, DealListQuery
from pigrocrm.core.deals.value import line_term, summarize
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.receivables import invoiced_not_won_predicate
from pigrocrm.core.money import percentage_of, round_money
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.repository import won_with_unbilled_hours_predicate


def _legal_entity_filter(legal_entity_id: UUID | None) -> tuple[ColumnElement[bool], ...]:
    """One legal entity's deals, or every legal entity's (REB-630, spec 2026-10-03 §1.9): the
    deal's own column, which a deal keeps when its customer moves (§1.7)."""
    return () if legal_entity_id is None else (Deal.legal_entity_id == legal_entity_id,)


class DealRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, deal_id: UUID, *, include_deleted: bool = False) -> Deal | None:
        deal = self.session.get(Deal, deal_id)
        if deal is None:
            return None
        if deal.deleted_at is not None and not include_deleted:
            return None
        return deal

    def get_locked(self, deal_id: UUID) -> Deal | None:
        """`get`, holding a row lock until the transaction ends: a write to the deal's value
        lines and its conversion into a contract run one after the other, so neither reads
        what the other is about to change."""
        deal = self.session.execute(
            select(Deal).where(Deal.id == deal_id).with_for_update()
        ).scalar_one_or_none()
        if deal is None or deal.deleted_at is not None:
            return None
        return deal

    def linked_contract(self, deal_id: UUID) -> tuple[UUID, str] | None:
        """The id and title of the contract this deal was converted into, if it was."""
        row = self.session.execute(
            select(Contract.id, Contract.title).where(Contract.deal_id == deal_id)
        ).first()
        return None if row is None else (row[0], row[1])

    def get_many(self, deal_ids: Collection[UUID]) -> list[Deal]:
        """The live deals among `deal_ids`, in one query. An id with no live deal is absent."""
        if not deal_ids:
            return []
        return list(
            self.session.execute(
                select(Deal).where(Deal.id.in_(deal_ids), Deal.deleted_at.is_(None))
            ).scalars()
        )

    def customer_names(self, customer_ids: Collection[UUID]) -> dict[UUID, str]:
        """The `legal_name` of each given customer, in one query.

        The same method, for the same reason, as `PersonRepository.customer_names`: one
        statement for a whole page rather than one per row, so a 50-row deal list costs
        two queries and never fifty-one. A label lookup for rows already chosen, run
        after the limit.

        No `deleted_at` filter, deliberately -- and here it costs nothing, because a live
        deal's customer is always live: `OrganizationService.soft_delete` refuses while the
        customer has active deals, `DealService.create` rejects an archived customer, and
        `DealService.restore` raises a conflict ("il cliente e' archiviato") rather than
        handing back a deal whose customer is gone. Filtering would therefore only add a
        way for the name to come back empty on a row that has one, and
        `deals.customer_id` is NOT NULL, so an empty name there reads as a data error
        rather than as "no customer". Mirrors `PersonRepository.customer_names`, which
        omits the filter for the neighbouring reason.

        An empty input short-circuits: `IN ()` is a query with no possible rows.
        """
        if not customer_ids:
            return {}
        rows = self.session.execute(
            select(Organization.id, Organization.legal_name).where(
                Organization.id.in_(customer_ids)
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    def value_lines(self, deal_ids: Collection[UUID]) -> dict[UUID, list[DealValueLine]]:
        """The value lines of each given deal, in order, in one query: what a page of deals
        needs to carry its figures. A deal with no lines is absent from the answer."""
        if not deal_ids:
            return {}
        rows = self.session.execute(
            select(DealValueLine)
            .where(DealValueLine.deal_id.in_(deal_ids))
            .order_by(DealValueLine.deal_id, DealValueLine.position)
        ).scalars()
        lines: dict[UUID, list[DealValueLine]] = {}
        for row in rows:
            lines.setdefault(row.deal_id, []).append(row)
        return lines

    def replace_value_lines(self, deal_id: UUID, lines: list[DealValueLine]) -> None:
        """Replaces the whole list. The old rows are deleted and flushed before the new ones
        go in, so `(deal_id, position)` stays unique. The deal row is locked first: two
        writers replacing one deal's lines take turns instead of colliding on that key."""
        self.session.execute(select(Deal.id).where(Deal.id == deal_id).with_for_update())
        self.session.execute(delete(DealValueLine).where(DealValueLine.deal_id == deal_id))
        self.session.flush()
        self.session.add_all(lines)
        self.session.flush()

    def names(self, deal_ids: Collection[UUID]) -> dict[UUID, str]:
        """The `name` of each given deal, in one query.

        The sibling of `customer_names` above, for rows that arrive naming a deal by id
        and nothing else. Its caller is the weekly report (spec 2026-09-16 §3.1, section
        6): `activities.stage_changed` carries the two stage names in its payload but only
        `entity_id` for the deal, so a report listing ten movements would otherwise cost
        ten `get()` calls -- the N+1 this repository already refuses for a page of labels.

        No `deleted_at` filter, like `customer_names`: an activity outlives the row it
        describes, and a deal archived after it moved still moved. A caller that wants to
        drop those rows can do it on the missing key -- a hard-deleted deal is simply
        absent from the answer -- which is a decision about the *list*, not about the
        lookup.

        An empty input short-circuits: `IN ()` is a query with no possible rows.
        """
        if not deal_ids:
            return {}
        rows = self.session.execute(select(Deal.id, Deal.name).where(Deal.id.in_(deal_ids))).all()
        return {row[0]: row[1] for row in rows}

    def add(self, deal: Deal) -> Deal:
        self.session.add(deal)
        self.session.flush()
        return deal

    def find_by_rebase_ref(self, ref: str) -> Deal | None:
        """The live deal carrying `ref` among its `rebase_refs`, an exact element, under
        whichever customer it sits now: how the engagements door finds again a deal it
        created and failed to record (`rebase:match=<id>`, spec 2026-09-25 § 2.3 step
        5), and with it the customer that call used, even after the freelancer renamed
        that customer or moved the deal. Not `list`'s search, which is a trigram `ilike`
        answered a page at a time: a first page is not the set, and a wildcard or a
        case fold is not the same reference.

        Array containment, answered by the GIN index on the column. At most one by
        construction (one reference per request or match, written once); oldest first all
        the same, so the answer never depends on the plan."""
        return self.session.scalars(
            select(Deal)
            .where(
                Deal.deleted_at.is_(None),
                Deal.rebase_refs.contains([ref]),
            )
            .order_by(Deal.created_at, Deal.id)
            .limit(1)
        ).first()

    def pipeline_summary(self, legal_entity_id: UUID | None = None) -> list[PipelineStageSummary]:
        """Deals per stage: count, `Σ valore_previsto`, count without a value, and the
        weighted estimate.

        Lives in the repository rather than on `DealService` on purpose (spec §3): these
        aggregates have no business rule beyond `deleted_at IS NULL`, and putting them on
        the service would create **two paths an agent can reach the same number by** --
        the deal domain tool and the dashboard tool -- which is exactly the duplication
        this slice exists not to introduce.

        `weighted_value` is the first of §3's three declared exceptions: a product of two
        columns of `deals`, rounded per row and then summed, HALF_UP. Allowed because both
        operands are columns of this one table and the result is not money received. It is
        labelled *stima* in every rendering and never added to revenue.

        A LEFT JOIN from `pipeline_stages`, so a stage with no deals comes back with
        zeroes: a missing stage and an empty stage render identically in a bar chart and
        the reader cannot tell which they are looking at.

        **Every** configured stage, not only the open ones (2026-09-09): a card that stops
        at the last open stage never says where the work ended up, and «Vinto» and «Perso»
        are the two places a pipeline exists to reach. Each row carries its `stage_type` so
        the renderer groups the closed ones by that rather than by `name`, which the user
        may rename. The closed rows count what sits in the stage *today* and are not
        filtered by any period -- nothing here ever was; the period question is answered by
        `closed_in_period`, above the same card, and one card with two meanings is how two
        figures come to disagree.
        """
        rounded_weight = func.round(
            func.cast(Deal.expected_value, Numeric(20, 6))
            * func.cast(Deal.probability, Numeric(20, 6))
            / literal(100),
            2,
        )
        stmt = (
            select(
                PipelineStage.id,
                PipelineStage.code,
                PipelineStage.name,
                PipelineStage.type,
                PipelineStage.position,
                func.count(Deal.id).label("number"),
                func.coalesce(func.sum(Deal.expected_value), literal(0)).label("value"),
                # `Deal.id IS NOT NULL` is load-bearing and not defensive: under the outer
                # join a stage with no deals still yields one row whose `deals` columns are
                # all NULL, so a bare `valore_previsto IS NULL` would count that phantom
                # row and report one value-less deal in a stage that has none.
                func.count(
                    case((and_(Deal.id.is_not(None), Deal.expected_value.is_(None)), 1))
                ).label("without"),
                func.coalesce(func.sum(rounded_weight), literal(0)).label("weighted"),
            )
            .select_from(PipelineStage)
            .outerjoin(
                Deal,
                # The legal entity goes in the join condition, never in a `where` (REB-630):
                # a `where` on a column of the outer side turns the join inner, and a
                # stage with none of this legal entity's deals would vanish instead of
                # reading zero, which is the one thing the outer join is here for.
                (Deal.pipeline_stage_id == PipelineStage.id)
                & Deal.deleted_at.is_(None)
                & (true() if legal_entity_id is None else Deal.legal_entity_id == legal_entity_id),
            )
            .group_by(
                PipelineStage.id,
                PipelineStage.code,
                PipelineStage.name,
                PipelineStage.type,
                PipelineStage.position,
            )
            .order_by(PipelineStage.position, PipelineStage.id)
        )
        return [
            PipelineStageSummary(
                stage_id=str(row.id),
                stage_code=row.code,
                stage_name=row.name,
                stage_type=row.type,
                position=row.position,
                number=row.number,
                total_value=round_money(Decimal(row.value)),
                without_value=row.without,
                weighted_value=round_money(Decimal(row.weighted)),
            )
            for row in self.session.execute(stmt).all()
        ]

    def closed_in_period(
        self, date_from: date, date_to: date, legal_entity_id: UUID | None = None
    ) -> ClosedInPeriod:
        """Deals won and lost in the period, by `closed_at`.

        `closed_at` and not the timeline: `move_stage` records the stage *names*, which a
        user may rename (residuo R15), so deducing a historical closure would mean matching
        a mutable string. Rows with `chiuso_il IS NULL` are excluded here and counted by
        `unattributable_closures` so the dashboard can declare them.

        `conversion_rate` is §3's second declared exception: a ratio of two counts of the
        same rows. It goes through `money.percentage_of` rather than a local division for
        the reason the spec gives -- it is the *same* rule as slice 4 §7.1's margin
        percentage, `None` and never `0.00` on a zero denominator, so it is one rule with
        one rounding mode and not two that agree today. A local `quantize` would silently
        be HALF_EVEN, the context default this project never sets.

        `won_monthly_recurring` is the third exception, declared on `monthly_recurring_of`.
        """
        stmt = (
            select(
                PipelineStage.type,
                func.count(Deal.id).label("number"),
                func.coalesce(func.sum(Deal.expected_value), literal(0)).label("value"),
            )
            .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
            .where(
                Deal.deleted_at.is_(None),
                *_legal_entity_filter(legal_entity_id),
                Deal.closed_at.is_not(None),
                Deal.closed_at >= date_from,
                Deal.closed_at <= date_to,
                PipelineStage.type.in_(("won", "lost")),
            )
            .group_by(PipelineStage.type)
        )
        by_type = {row.type: row for row in self.session.execute(stmt).all()}
        won = by_type.get("won")
        lost = by_type.get("lost")
        won_count = won.number if won is not None else 0
        lost_count = lost.number if lost is not None else 0
        return ClosedInPeriod(
            won=won_count,
            lost=lost_count,
            won_value=(round_money(Decimal(won.value)) if won is not None else Decimal("0.00")),
            won_monthly_recurring=self.monthly_recurring_of(
                PipelineStage.type == "won",
                Deal.closed_at.is_not(None),
                Deal.closed_at >= date_from,
                Deal.closed_at <= date_to,
                *_legal_entity_filter(legal_entity_id),
            ),
            conversion_rate=percentage_of(Decimal(won_count), Decimal(won_count + lost_count)),
        )

    def open_monthly_recurring(self, legal_entity_id: UUID | None = None) -> Decimal:
        """The monthly recurring value of the deals sitting in an open stage today."""
        return self.monthly_recurring_of(
            PipelineStage.type == "open", *_legal_entity_filter(legal_entity_id)
        )

    def monthly_recurring_of(self, *conditions: ColumnElement[bool]) -> Decimal:
        """**§3's third declared exception** (REB-931, spec 2026-11-03 §6): the monthly
        recurring value of the live deals `conditions` pick out, summed over their value
        lines. Unlike the other two it is not one SQL expression: a line's monthly figure
        depends on its period and unit, so the lines are read and priced by
        `deals.value.summarize`, the one function that prices a deal anywhere, and what
        comes back is a figure per month, never added to `total_value` or `won_value`
        and labelled as monthly in every rendering. A deal with no lines contributes
        nothing, as it always counted nothing here.

        The caller's conditions are over `Deal` and `PipelineStage`, both joined here.
        """
        rows = self.session.execute(
            select(DealValueLine, Deal.expected_close_date)
            .join(Deal, Deal.id == DealValueLine.deal_id)
            .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
            .where(Deal.deleted_at.is_(None), *conditions)
        ).all()
        today = today_local()
        return summarize(
            line_term(line, close_date or today) for line, close_date in rows
        ).monthly_recurring

    def expected_closures(
        self, date_from: date, date_to: date, legal_entity_id: UUID | None = None
    ) -> int:
        """Open deals whose `expected_close_date` falls in the window.

        `type='open'` only: a deal already won with a future expected date is not an
        expected closure, it is a stale field on a finished deal.
        """
        return (
            self.session.scalar(
                select(func.count(Deal.id))
                .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
                .where(
                    Deal.deleted_at.is_(None),
                    *_legal_entity_filter(legal_entity_id),
                    PipelineStage.type == "open",
                    Deal.expected_close_date.is_not(None),
                    Deal.expected_close_date >= date_from,
                    Deal.expected_close_date <= date_to,
                )
            )
            or 0
        )

    def unattributable_closures(self, legal_entity_id: UUID | None = None) -> int:
        """Deals in a terminal stage with no `closed_at`.

        §4.1: `closed_at` is deliberately not backfilled, so every deal closed before
        migration 0023 is unattributable to a period. This count is what lets the dashboard
        say "N deal chiusi prima dell'introduzione di questa misura non sono attribuibili a
        un periodo" instead of quietly reporting a conversion rate computed on a subset.
        """
        return (
            self.session.scalar(
                select(func.count(Deal.id))
                .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
                .where(
                    Deal.deleted_at.is_(None),
                    *_legal_entity_filter(legal_entity_id),
                    Deal.closed_at.is_(None),
                    PipelineStage.type.in_(("won", "lost")),
                )
            )
            or 0
        )

    def count_active_time_entries(self, deal_id: UUID) -> int:
        """Live `time_entries` rows on this deal -- the guard `soft_delete` reads.

        Every entry, not only the billable unbilled ones, and that width is the whole
        point. The defect this closes was `unbilled_backlog` counting hours whose deal had
        been archived, but `week_hours`, `hours_in_range` and `labour_cost_in_range` read
        the same table with the same `deleted_at IS NULL` and no join to `deals` either. A
        guard shaped like one aggregate's predicate would leave the others exactly as they
        were; a guard on "is there any live hour here at all" gives all of them the single
        invariant they need -- no live time entry hangs off an archived deal -- with none
        of them learning about `deals`.

        Counted rather than fetched: the caller needs to know whether to refuse and how
        many rows to name, and a deal can carry hundreds.
        """
        return (
            self.session.scalar(
                select(func.count(TimeEntry.id)).where(
                    TimeEntry.deal_id == deal_id, TimeEntry.deleted_at.is_(None)
                )
            )
            or 0
        )

    # `list` is defined LAST in this class on purpose: `def list(...)` rebinds `list` in
    # the class namespace, and Python 3.13 evaluates annotations eagerly, so a later
    # `-> list[...]` would raise `TypeError` at import time.
    def list(self, query: DealListQuery) -> list[Deal]:
        stmt = select(Deal).where(Deal.deleted_at.is_(None))

        if query.search:
            # escape_like neutralizes "%"/"_"/"\" in the *user's* term before it is
            # wrapped in the wildcard "%...%" this method builds -- otherwise a
            # literal "_" in the search box matches "any one character" and a
            # trailing "\" combines with the wildcard just after it into an
            # accidental escape sequence that swallows the match entirely. escape="\\"
            # states explicitly which character escape_like used, rather than relying
            # on ILIKE's default. Mirrors OrganizationRepository.list/PersonRepository.list
            # exactly.
            like = f"%{escape_like(query.search.lower())}%"
            stmt = stmt.where(Deal.name.ilike(like, escape="\\"))
        if query.customer_id:
            stmt = stmt.where(Deal.customer_id == query.customer_id)
        if query.legal_entity_id:
            stmt = stmt.where(Deal.legal_entity_id == query.legal_entity_id)
        if query.stage_id:
            stmt = stmt.where(Deal.pipeline_stage_id == query.stage_id)
        if query.custom:
            # JSONB containment, served by the GIN index.
            stmt = stmt.where(Deal.custom_fields.contains(query.custom))
        if query.invoiced_unwon:
            # The drill-through of §6.2's "fatturato ma non vinto" card. Written as
            # `id IN (subquery)` rather than as joins on the outer statement, unlike
            # `DocumentRepository.list`: a deal has *many* invoices, so joining here would
            # return a deal once per invoice and the list would be longer than the card
            # while describing the same set. The count says `COUNT(DISTINCT deal.id)` for
            # the same reason; a semi-join is the spelling that needs no `distinct` at all,
            # and it leaves the outer statement's own filters, sort and keyset untouched.
            #
            # An **additional** predicate, never a replacement for the statement above: a
            # drill-through that silently dropped the caller's own filters would be
            # answering a different question from the one asked.
            stmt = stmt.where(
                Deal.id.in_(
                    select(Deal.id)
                    .join(Invoice, Invoice.deal_id == Deal.id)
                    .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
                    .where(*invoiced_not_won_predicate())
                )
            )
        if query.to_invoice:
            # §6.2's "vinto ma da fatturare", same shape and for the same reason: a deal
            # has many time entries. The predicate is the one `count_won_deals_to_invoice`
            # calls, imported rather than restated.
            stmt = stmt.where(
                Deal.id.in_(
                    select(Deal.id)
                    .join(TimeEntry, TimeEntry.deal_id == Deal.id)
                    .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
                    .where(*won_with_unbilled_hours_predicate())
                )
            )

        # Residuo R9 -- see `OrganizationRepository.list` for the reasoning.
        spec = DEAL_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )
