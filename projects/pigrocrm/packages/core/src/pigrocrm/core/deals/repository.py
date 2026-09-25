from collections.abc import Collection
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Numeric, and_, case, func, literal, select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import ClosedInPeriod, PipelineStageSummary
from pigrocrm.core.db import decode_cursor, escape_like, keyset_predicate, order_by
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.schemas import DEAL_SORTS, DealListQuery
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.repository import invoiced_not_won_predicate
from pigrocrm.core.money import percentage_of, round_money
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.repository import won_with_unbilled_hours_predicate


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

    def customer_names(self, customer_ids: Collection[UUID]) -> dict[UUID, str]:
        """The `ragione_sociale` of each given customer, in one query.

        The same method, for the same reason, as `PersonRepository.customer_names`: one
        statement for a whole page rather than one per row, so a 50-row deal list costs
        two queries and never fifty-one. A label lookup for rows already chosen, run
        after the limit.

        No `deleted_at` filter, deliberately -- and here it costs nothing, because a live
        deal's customer is always live: `CustomerService.soft_delete` refuses while the
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
            select(Customer.id, Customer.ragione_sociale).where(Customer.id.in_(customer_ids))
        ).all()
        return {row[0]: row[1] for row in rows}

    def names(self, deal_ids: Collection[UUID]) -> dict[UUID, str]:
        """The `nome` of each given deal, in one query.

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
        rows = self.session.execute(select(Deal.id, Deal.nome).where(Deal.id.in_(deal_ids))).all()
        return {row[0]: row[1] for row in rows}

    def add(self, deal: Deal) -> Deal:
        self.session.add(deal)
        self.session.flush()
        return deal

    def find_by_marker(self, customer_id: UUID, marker: str) -> Deal | None:
        """The live deal of that customer whose `note` contains `marker`, as an exact
        substring: how the engagements door finds again a deal it created and failed to
        record (`rebase:match=<id>`, spec 2026-09-25 § 2.3 step 5). Not `list`'s search,
        which is a trigram `ilike` answered a page at a time: a first page is not the set,
        and a wildcard or a case fold is not the same marker.

        `strpos` rather than `LIKE`: no character of the marker is a pattern. At most one
        by construction (one marker per match, written once); oldest first all the same,
        so the answer never depends on the plan."""
        return self.session.scalars(
            select(Deal)
            .where(
                Deal.customer_id == customer_id,
                Deal.deleted_at.is_(None),
                func.strpos(Deal.note, marker) > 0,
            )
            .order_by(Deal.created_at, Deal.id)
            .limit(1)
        ).first()

    def pipeline_summary(self) -> list[PipelineStageSummary]:
        """Deals per stage: count, `Σ valore_previsto`, count without a value, and the
        weighted estimate.

        Lives in the repository rather than on `DealService` on purpose (spec §3): these
        aggregates have no business rule beyond `deleted_at IS NULL`, and putting them on
        the service would create **two paths an agent can reach the same number by** --
        the deal domain tool and the dashboard tool -- which is exactly the duplication
        this slice exists not to introduce.

        `valore_ponderato` is the first of §3's two declared exceptions: a product of two
        columns of `deals`, rounded per row and then summed, HALF_UP. Allowed because both
        operands are columns of this one table and the result is not money received. It is
        labelled *stima* in every rendering and never added to revenue.

        A LEFT JOIN from `pipeline_stages`, so a stage with no deals comes back with
        zeroes: a missing stage and an empty stage render identically in a bar chart and
        the reader cannot tell which they are looking at.

        **Every** configured stage, not only the open ones (2026-09-09): a card that stops
        at the last open stage never says where the work ended up, and «Vinto» and «Perso»
        are the two places a pipeline exists to reach. Each row carries its `stage_tipo` so
        the renderer groups the closed ones by that rather than by `nome`, which the user
        may rename. The closed rows count what sits in the stage *today* and are not
        filtered by any period -- nothing here ever was; the period question is answered by
        `closed_in_period`, above the same card, and one card with two meanings is how two
        figures come to disagree.
        """
        rounded_weight = func.round(
            func.cast(Deal.valore_previsto, Numeric(20, 6))
            * func.cast(Deal.probabilita, Numeric(20, 6))
            / literal(100),
            2,
        )
        stmt = (
            select(
                PipelineStage.id,
                PipelineStage.code,
                PipelineStage.nome,
                PipelineStage.tipo,
                PipelineStage.posizione,
                func.count(Deal.id).label("numero"),
                func.coalesce(func.sum(Deal.valore_previsto), literal(0)).label("valore"),
                # `Deal.id IS NOT NULL` is load-bearing and not defensive: under the outer
                # join a stage with no deals still yields one row whose `deals` columns are
                # all NULL, so a bare `valore_previsto IS NULL` would count that phantom
                # row and report one value-less deal in a stage that has none.
                func.count(
                    case((and_(Deal.id.is_not(None), Deal.valore_previsto.is_(None)), 1))
                ).label("senza"),
                func.coalesce(func.sum(rounded_weight), literal(0)).label("ponderato"),
            )
            .select_from(PipelineStage)
            .outerjoin(
                Deal,
                (Deal.pipeline_stage_id == PipelineStage.id) & Deal.deleted_at.is_(None),
            )
            .group_by(
                PipelineStage.id,
                PipelineStage.code,
                PipelineStage.nome,
                PipelineStage.tipo,
                PipelineStage.posizione,
            )
            .order_by(PipelineStage.posizione, PipelineStage.id)
        )
        return [
            PipelineStageSummary(
                stage_id=str(row.id),
                stage_code=row.code,
                stage_nome=row.nome,
                stage_tipo=row.tipo,
                posizione=row.posizione,
                numero=row.numero,
                valore_totale=round_money(Decimal(row.valore)),
                senza_valore=row.senza,
                valore_ponderato=round_money(Decimal(row.ponderato)),
            )
            for row in self.session.execute(stmt).all()
        ]

    def closed_in_period(self, da: date, a: date) -> ClosedInPeriod:
        """Deals won and lost in the period, by `chiuso_il`.

        `chiuso_il` and not the timeline: `move_stage` records the stage *names*, which a
        user may rename (residuo R15), so deducing a historical closure would mean matching
        a mutable string. Rows with `chiuso_il IS NULL` are excluded here and counted by
        `unattributable_closures` so the dashboard can declare them.

        `tasso_conversione` is §3's second declared exception: a ratio of two counts of the
        same rows. It goes through `money.percentage_of` rather than a local division for
        the reason the spec gives -- it is the *same* rule as slice 4 §7.1's margin
        percentage, `None` and never `0.00` on a zero denominator, so it is one rule with
        one rounding mode and not two that agree today. A local `quantize` would silently
        be HALF_EVEN, the context default this project never sets.
        """
        stmt = (
            select(
                PipelineStage.tipo,
                func.count(Deal.id).label("numero"),
                func.coalesce(func.sum(Deal.valore_previsto), literal(0)).label("valore"),
            )
            .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
            .where(
                Deal.deleted_at.is_(None),
                Deal.chiuso_il.is_not(None),
                Deal.chiuso_il >= da,
                Deal.chiuso_il <= a,
                PipelineStage.tipo.in_(("won", "lost")),
            )
            .group_by(PipelineStage.tipo)
        )
        by_tipo = {row.tipo: row for row in self.session.execute(stmt).all()}
        won = by_tipo.get("won")
        lost = by_tipo.get("lost")
        vinti = won.numero if won is not None else 0
        persi = lost.numero if lost is not None else 0
        return ClosedInPeriod(
            vinti=vinti,
            persi=persi,
            valore_vinto=(round_money(Decimal(won.valore)) if won is not None else Decimal("0.00")),
            tasso_conversione=percentage_of(Decimal(vinti), Decimal(vinti + persi)),
        )

    def expected_closures(self, da: date, a: date) -> int:
        """Open deals whose `data_chiusura_prevista` falls in the window.

        `tipo='open'` only: a deal already won with a future expected date is not an
        expected closure, it is a stale field on a finished deal.
        """
        return (
            self.session.scalar(
                select(func.count(Deal.id))
                .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
                .where(
                    Deal.deleted_at.is_(None),
                    PipelineStage.tipo == "open",
                    Deal.data_chiusura_prevista.is_not(None),
                    Deal.data_chiusura_prevista >= da,
                    Deal.data_chiusura_prevista <= a,
                )
            )
            or 0
        )

    def unattributable_closures(self) -> int:
        """Deals in a terminal stage with no `chiuso_il`.

        §4.1: `chiuso_il` is deliberately not backfilled, so every deal closed before
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
                    Deal.chiuso_il.is_(None),
                    PipelineStage.tipo.in_(("won", "lost")),
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
            # on ILIKE's default. Mirrors CustomerRepository.list/PersonRepository.list
            # exactly.
            like = f"%{escape_like(query.search.lower())}%"
            stmt = stmt.where(Deal.nome.ilike(like, escape="\\"))
        if query.customer_id:
            stmt = stmt.where(Deal.customer_id == query.customer_id)
        if query.stage_id:
            stmt = stmt.where(Deal.pipeline_stage_id == query.stage_id)
        if query.custom:
            # JSONB containment, served by the GIN index.
            stmt = stmt.where(Deal.custom_fields.contains(query.custom))
        if query.fatturato_non_vinto:
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
        if query.da_fatturare:
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

        # Residuo R9 -- see `CustomerRepository.list` for the reasoning.
        spec = DEAL_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )
