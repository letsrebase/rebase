from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.errors import (
    Conflict,
    ImmutableField,
    NotFound,
    ValidationFailed,
)
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.money import line_value, sum_hours, sum_money
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.rates import RateResolver
from pigrocrm.core.timetracking.repository import TimeEntryRepository
from pigrocrm.core.timetracking.schemas import (
    DealRateUpdate,
    DealTimeSummary,
    RateDescription,
    RecalculateRatesRequest,
    TimeEntryCreate,
    TimeEntryListQuery,
    TimeEntryPage,
    TimeEntryRead,
    TimeEntryUpdate,
    UserRatesUpdate,
)

# Typed as the fields module's own EntityType (not a bare `str`), matching
# OrganizationService.ENTITY/DealService.ENTITY exactly: passing a plain `str` into
# `specs_for` fails mypy strict, which requires the narrower Literal type.
ENTITY: EntityType = "time_entry"

# §4.3's table, as data. Frozen once the entry belongs to a fiscal document;
# `internal_notes` and `custom_fields` stay mutable because they appear on no artefact.
# `description` is in this tuple and it is not obvious: it is the column the client
# reads in the timesheet attached to the invoice, so changing it after issue would
# make the delivered document and the database say two different things.
FROZEN_WHEN_BILLED: tuple[str, ...] = (
    "hours",
    "date",
    "applied_rate",
    "applied_cost",
    "description",
    "deal_id",
    "billable",
    "user_id",
)


def to_read(entry: TimeEntry) -> TimeEntryRead:
    """The only place `line_value` and `line_cost` are computed.

    Returned already summed because §6 forbids the browser from doing any economic
    arithmetic: every figure the UI shows arrives finished. Task 4A-14 (the report) and
    Task 4B-4 (the P&L) both call this rather than repeating the multiplication --
    The previous system's P&L recomputed its own totals in the browser and that is exactly how the
    printed column and the total came to disagree.
    """
    read = TimeEntryRead.model_validate(entry)
    return read.model_copy(
        update={
            "line_value": line_value(entry.hours, entry.applied_rate),
            "line_cost": line_value(entry.hours, entry.applied_cost),
        }
    )


def billed_entry_ids(session: Session, entries: Sequence[TimeEntry | TimeEntryRead]) -> set[UUID]:
    """Which of `entries` belong to a **line of an issued invoice** and are therefore
    frozen (§4.3). The rows or their read shape: only `id` and `invoice_line_id` are
    read, and the engagements report (spec 2026-09-25 § 2.4) holds the entries as
    `list` answered them.

    The rule attaches to the state of the invoice, not to the presence of the link,
    because a draft is still freely editable: while the invoice is a draft the hours stay
    modifiable and slice 3's wholesale line replacement unbinds and rebinds them without
    orphans; the moment `issue()` commits, the bound hours are frozen without `issue()`
    having had to know they exist.

    Only `issued`. An **annulled** invoice keeps its number but not its revenue (§7.1) --
    the struck-through page of a paper register -- so its hours are CRM data again and can
    be re-invoiced. A **proforma** never freezes anything: it does not touch the register
    at all (slice 3 §5), and `confirmed` is close enough to an emission in spirit that
    `type` is filtered alongside `status` rather than trusted to differ.

    A soft-deleted invoice does not freeze either, and it cannot be an issued one: slice
    3's `ck_invoices_deleted_unnumbered` allows `deleted_at` only on a row with no number.
    The filter is there so the two facts cannot drift apart.

    One query, never one per entry: `deal_summary` calls this with every entry of a deal,
    and a per-row lookup would make the P&L quadratic in a deal's hours. Slice 4A's body
    was the superset "any non-null `invoice_line_id`", which was unobservable while
    `invoice_lines` did not exist; this is the narrowing that superset was placeholding
    for, and it changes not one of the four call sites -- which is why the four go through
    one function instead of writing `is not None` four times.
    """
    line_ids = {entry.invoice_line_id for entry in entries if entry.invoice_line_id is not None}
    if not line_ids:
        return set()
    # Still one query: every line this session can see, with whether its invoice is
    # issued. A line the session cannot see at all freezes its entry as an issued one
    # would (REB-634): since the row-level policies, an entry of a deal in one legal entity
    # may be bound to an invoice of another, which a member scoped to the first cannot
    # read. Open, the guard would let them edit hours already billed; closed, the worst
    # case is a draft line of an invoice out of their sight that they cannot touch until
    # it is issued anyway (CodeRabbit's seventh adversarial pass on PR #513).
    issued_flag = and_(
        Invoice.type == "invoice", Invoice.status == "issued", Invoice.deleted_at.is_(None)
    )
    seen = session.execute(
        select(InvoiceLine.id, issued_flag)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(InvoiceLine.id.in_(line_ids))
    ).all()
    visible = {line_id for line_id, _ in seen}
    issued = {line_id for line_id, is_issued in seen if is_issued}
    frozen = issued | (line_ids - visible)
    return {entry.id for entry in entries if entry.invoice_line_id in frozen}


class TimeEntryService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = TimeEntryRepository(session)
        self.deals = DealRepository(session)
        self.users = UserRepository(session)
        self.pipeline = PipelineService(session)
        self.rates = RateResolver(session)
        self.locks = PeriodLockService(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    # ---- validation helpers ------------------------------------------------

    def _require_deal(self, deal_id: UUID) -> Deal:
        deal = self.deals.get(deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)
        return deal

    def _require_active_user(self, user_id: UUID) -> None:
        """Residual R3's answer for this slice, made concrete: an existing assignment
        survives a deactivation, a new one is refused. The existence check and the
        activity check are both done against the same fetched row rather than through
        `UserRepository.get_active` -- that method exists for the login/refresh flows
        and folds "does not exist" and "is not active" into the same generic
        `ValidationFailed("user", "id", ...)`, which would not let this method name
        `user_id` as the offending field the way the problem document needs to."""
        user = self.users.get(user_id)
        if user is None:
            raise NotFound("user", user_id)
        if not user.active:
            raise ValidationFailed(
                ENTITY, "user_id", "l'utente non è attivo", expected="un utente attivo"
            )

    def _check_not_future(self, day: date) -> None:
        """A future hour is not data, it is a forecast, and this slice makes no
        forecasts (§13). Back-dating has no floor at all, unlike slice 3's
        `issue_date`: there you write into a progressive fiscal register, where
        inserting into a closed year is wrong regardless; here you declare when work
        was done, and forbidding it would produce hours dated the day somebody
        remembered to write them -- an archive that lies about its only temporal
        field.

        The comparison is against `today_local()` and never a bare `date.today()`
        (see `db/clock.py`): the day this refuses to go past is the day the person doing
        the work is living in, not the day the process's own timezone happens to be
        in. The API image runs in UTC, where `date.today()` is still yesterday between
        midnight and 01:00 CET (02:00 CEST), so logging this morning's hours would be
        refused as `data futura` -- a legal entry rejected with a message that reads as
        a product defect. Run the process east of Italy and the same check silently
        accepts tomorrow, which is the forecast this slice says it does not make."""
        if day > today_local():
            raise ValidationFailed(
                ENTITY, "date", "data futura", expected="una data non successiva a oggi"
            )

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _require(self, entry_id: UUID, *, include_deleted: bool = False) -> TimeEntry:
        entry = self.repo.get(entry_id, include_deleted=include_deleted)
        if entry is None:
            raise NotFound(ENTITY, entry_id)
        return entry

    # ---- writes -----------------------------------------------------------

    def create(self, data: TimeEntryCreate, actor: Actor, *, commit: bool = True) -> TimeEntryRead:
        """`commit=False` leaves the transaction to a caller that writes more than the entry
        and needs the writes to stand or fall together (`TimerService.stop`); the entry is
        flushed either way."""
        actor.require_write("log_time")
        deal = self._require_deal(data.deal_id)
        self._require_active_user(data.user_id)
        self._check_not_future(data.date)
        self.locks.assert_writable(ENTITY, "date", data.date)

        resolved = self.rates.resolve(
            deal_id=data.deal_id,
            user_id=data.user_id,
            explicit_rate=data.applied_rate,
            cost_explicit=data.applied_cost,
        )
        entry = self.repo.add(
            TimeEntry(
                deal_id=data.deal_id,
                user_id=data.user_id,
                date=data.date,
                hours=data.hours,
                # Stored raw: multi-line, unescaped, exactly as typed. `escape_for`
                # prepares it at render, once, for the context it lands in. The previous system
                # escaped at write time and the value then reached the XLSX escaped and
                # the PDF double-escaped.
                description=data.description,
                billable=data.billable,
                applied_rate=resolved.rate,
                applied_cost=resolved.cost,
                rate_source=resolved.rate_source,
                cost_source=resolved.cost_source,
                internal_notes=data.internal_notes,
                custom_fields=self._validated_custom(data.custom_fields or {}),
            )
        )
        self.activities.record(
            ENTITY,
            entry.id,
            "created",
            actor,
            {
                "deal_id": str(entry.deal_id),
                "hours": str(entry.hours),
                "applied_rate": str(entry.applied_rate),
                "rate_source": entry.rate_source,
            },
        )
        stage = self.pipeline.get(deal.pipeline_stage_id)
        if stage.type != "open":
            # Closing a deal blocks nothing (§4.3). On a won deal the work *begins* at
            # that moment; on a lost one the pre-sales hours are a real cost. Refusing
            # would force reopening the deal to tell the truth -- corrupting the
            # pipeline to save the actuals. The UI warns, this records, neither refuses.
            self.activities.record(
                ENTITY, entry.id, "time_logged_on_closed_deal", actor, {"stage": stage.name}
            )
        if commit:
            self.session.commit()
        return to_read(entry)

    def update(self, entry_id: UUID, data: TimeEntryUpdate, actor: Actor) -> TimeEntryRead:
        actor.require_write("update_time_entry")
        entry = self._require(entry_id)
        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, TimeEntry, changes)

        if billed_entry_ids(self.session, [entry]):
            for field in FROZEN_WHEN_BILLED:
                if field in changes:
                    raise ImmutableField(
                        ENTITY,
                        field,
                        "la voce appartiene a una fattura emessa e non è più un dato di CRM",
                    )

        # `.get(...) is not None` on the value, not `in changes` on the key: all three
        # columns are `NOT NULL`, so `reject_cleared_columns` has already refused a
        # `null` above -- but reading the value keeps these lookups from ever being
        # handed a `None` to resolve, which is what turns a clear into a bogus
        # `NotFound(..., None)` on the nullable foreign keys elsewhere.
        if changes.get("deal_id") is not None:
            self._require_deal(changes["deal_id"])
        if changes.get("user_id") is not None:
            self._require_active_user(changes["user_id"])
        if changes.get("date") is not None:
            self._check_not_future(changes["date"])
        # Both the stored date and the new one: moving a row out of a closed month is
        # still a write into it, and checking only the destination would let somebody
        # empty a reported month one row at a time.
        self.locks.assert_writable(ENTITY, "date", entry.date, changes.get("date"))

        # A rate supplied on an update is an explicit override and is re-frozen with
        # `origine = "manuale"`; a rate NOT supplied is never re-resolved, because
        # re-resolving would be exactly the "a report re-reads a rate column" failure
        # §5 exists to prevent, wearing an update's clothes.
        #
        # `in changes` on the key, not `is not None` on the value: since A14 was closed
        # a rate is clearable, and clearing one is a deliberate act ("this hour has no
        # price") that must be recorded as such -- `absent`, frozen, chosen by
        # somebody. A cleared rate that fell through to the `else` of nothing would keep
        # the stale `manual`/`deal` origin, and the next reader would believe a price
        # that is no longer there.
        if "applied_rate" in changes:
            changes["rate_source"] = "manual" if changes["applied_rate"] is not None else "absent"
        if "applied_cost" in changes:
            changes["cost_source"] = "manual" if changes["applied_cost"] is not None else "absent"

        if data.custom_fields is not None:
            changes["custom_fields"] = merge_custom(
                ENTITY, self.fields.specs_for(ENTITY), entry.custom_fields, data.custom_fields
            )
        for key, value in changes.items():
            setattr(entry, key, value)

        self.activities.record(ENTITY, entry.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return to_read(entry)

    def soft_delete(self, entry_id: UUID, actor: Actor) -> None:
        """Reversible, like everything else in this product. The previous system's only way to void
        a wrong entry was a physical `DELETE` that rewrote the whole archive file;
        `ore > 0` stays the rule and the correction has a path that is not destructive
        (§2.2, last row)."""
        actor.require_write("delete_time_entry")
        entry = self._require(entry_id)
        # `invoice_line_id is not None`, and deliberately **not** `billed_entry_ids`.
        # That helper answers "bound to a line of an *issued* invoice", which is the
        # right question for freezing a rate and the wrong one here: the database's own
        # `ck_time_entries_billed_not_deleted` is `deleted_at IS NULL OR invoice_line_id
        # IS NULL`, which does not care what state the invoice is in.
        #
        # With the narrower guard, an entry bound to a *draft* line — which is what
        # `bind_time_to_invoice` produces, and the ordinary case between choosing the
        # hours and issuing — sailed past this check and hit the CHECK at commit. The
        # user got a 500 carrying a `CheckViolation`, instead of the sentence written
        # immediately below, which was already the correct thing to say to them.
        if entry.invoice_line_id is not None:
            raise Conflict(
                ENTITY,
                "la voce è legata a una riga di fattura: scollegala prima di cancellarla",
                invoice_line_id=str(entry.invoice_line_id),
            )
        self.locks.assert_writable(ENTITY, "date", entry.date)
        entry.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, entry.id, "deleted", actor)
        self.session.commit()

    def restore(self, entry_id: UUID, actor: Actor) -> TimeEntryRead:
        """Refuses when the entry's deal is archived.

        `DealService.soft_delete` refuses while a deal still has live hours, so that no
        aggregate over `time_entries` can report work against a deal the deal list cannot
        show. That invariant has a back door if this method does not check it too --
        archive the hours, archive the now-empty deal, restore the hours -- and the exact
        state the guard exists to prevent is back. It is the same back door
        `DealService.restore` closes for the customer/deal pair, one level down.

        Checked unconditionally, not only when the entry really was archived: the invariant
        is about the entry's state *after* this call, not about what changed.
        """
        actor.require_write("restore_time_entry")
        entry = self._require(entry_id, include_deleted=True)

        deal = self.deals.get(entry.deal_id, include_deleted=True)
        if deal is not None and deal.deleted_at is not None:
            raise Conflict(
                ENTITY,
                "il deal è archiviato: ripristina prima il deal",
                deal_id=str(deal.id),
            )

        self.locks.assert_writable(ENTITY, "date", entry.date)
        was_deleted = entry.deleted_at is not None
        entry.deleted_at = None
        if was_deleted:
            # Recorded only when it really was deleted: logging "restored" for an entry
            # that was never archived would claim a recovery that never happened --
            # the same guard `DealService.restore` already carries.
            self.activities.record(ENTITY, entry.id, "restored", actor)
        self.session.commit()
        return to_read(entry)

    def update_user_rates(self, user_id: UUID, data: UserRatesUpdate, actor: Actor) -> None:
        """On `TimeEntryService` and not on a service of its own, deliberately -- see
        "Contradictions" item 4: §11 fixes the audited surface to three classes and the
        exclusion list to ten literal names, two of which are this and
        `update_deal_rate`. A separate `RateService` would put two excluded names
        outside the audited set, which is the "the ban degrades into an oversight"
        failure §11 exists to prevent.

        `admin`, not `collaborator`: changing what an hour is worth is closer to
        configuration than to writing an entity (slice 1 §6.3). Writes an activity,
        because the timeline is what reconstructs when a rate changed -- and is the
        reason §5 can afford not to historicise it (§4.5).

        `exclude_unset`, not `exclude_none`: clearing a rate back to `NULL` has to be
        expressible, and here it is not a convenience -- an unclearable rate is a
        number nobody chose staying in force forever. This method was written this way
        from the start, ahead of the rest; task 4B-1 converted every other service to
        the same contract via `supplied_changes` (residual A14).
        """
        actor.require_unscoped_admin("update_user_rates")
        user = self.users.get(user_id)
        if user is None:
            raise NotFound("user", user_id)
        changes = data.model_dump(exclude_unset=True)
        previous = {key: str(getattr(user, key)) for key in changes}
        for key, value in changes.items():
            setattr(user, key, value)
        self.activities.record(
            "user",
            user_id,
            "rates_updated",
            actor,
            {"before": previous, "after": {k: str(v) for k, v in changes.items()}},
        )
        self.session.commit()

    def update_deal_rate(self, deal_id: UUID, data: DealRateUpdate, actor: Actor) -> None:
        actor.require_admin("update_deal_rate")
        deal = self._require_deal(deal_id)
        changes = data.model_dump(exclude_unset=True)
        previous = {key: str(getattr(deal, key)) for key in changes}
        for key, value in changes.items():
            setattr(deal, key, value)
        self.activities.record(
            "deal",
            deal_id,
            "rate_updated",
            actor,
            {"before": previous, "after": {k: str(v) for k, v in changes.items()}},
        )
        self.session.commit()

    def recalculate_rates(self, deal_id: UUID, data: RecalculateRatesRequest, actor: Actor) -> int:
        """The only way to touch the past, and it is visible.

        `admin`, never `collaborator`, and **not exposed over MCP** (§11's exclusion
        list, first name): rewriting what already-done work was worth is closer to
        configuration than to writing an entity -- slice 1 §6.3's reading, which slice
        3 §11 applied to issuing an invoice.

        All-or-nothing. If the interval contains even one entry belonging to an issued
        invoice the whole call refuses, naming how many and the first line involved,
        because that number has already been handed to a client. A partial rewrite
        would leave the interval in a state nobody chose, so the refusal happens before
        any assignment.

        An entry whose rate (or cost) was written on the entry itself (`manual`) keeps it:
        that figure was chosen, not resolved, and is corrected on the entry.

        Returns how many entries were rewritten, so a caller can say "12 voci aggiornate"
        (the Italian copy) instead of "done".
        """
        actor.require_unscoped_admin("recalculate_rates")
        if data.date_to < data.date_from:
            raise ValidationFailed(
                ENTITY, "to", "intervallo invertito", expected="una data non anteriore a 'from'"
            )
        self._require_deal(deal_id)
        entries = self.repo.in_range(deal_id, data.date_from, data.date_to)

        billed = billed_entry_ids(self.session, entries)
        if billed:
            first = next(e for e in entries if e.id in billed)
            raise Conflict(
                ENTITY,
                "l'intervallo contiene voci già fatturate: quei valori sono stati "
                "consegnati a un cliente e non si riscrivono",
                entries_invoiced=len(billed),
                before_line=str(first.invoice_line_id),
                before_entry=str(first.id),
            )

        # A closed month is closed to this too: the lock and the recalculation are the
        # two ways the past can move, and leaving a gap between them would make the
        # lock decorative.
        self.locks.assert_writable(ENTITY, "date", *[e.date for e in entries])

        touched = 0
        for entry in entries:
            resolved = self.rates.resolve(deal_id=entry.deal_id, user_id=entry.user_id)
            # A rate or a cost somebody wrote on the entry (`manual`) is a decision, like an
            # explicit zero (`rates.py`): the recalculation re-resolves only what the rate
            # levels gave it, each of the two figures on its own.
            if entry.rate_source == "manual":
                rate, rate_source = entry.applied_rate, entry.rate_source
            else:
                rate, rate_source = resolved.rate, resolved.rate_source
            if entry.cost_source == "manual":
                cost, cost_source = entry.applied_cost, entry.cost_source
            else:
                cost, cost_source = resolved.cost, resolved.cost_source
            if rate == entry.applied_rate and cost == entry.applied_cost:
                # Nothing changed for this row: skip it rather than writing an activity
                # claiming a change that did not happen -- the same honesty guard
                # `restore` applies to its own "restored" entry.
                continue
            self.activities.record(
                ENTITY,
                entry.id,
                "rates_recalculated",
                actor,
                {
                    "rate_before": str(entry.applied_rate),
                    "rate_after": str(rate),
                    "cost_before": str(entry.applied_cost),
                    "cost_after": str(cost),
                },
            )
            entry.applied_rate = rate
            entry.rate_source = rate_source
            entry.applied_cost = cost
            entry.cost_source = cost_source
            touched += 1

        self.session.commit()
        return touched

    # ---- reads ------------------------------------------------------------

    def get(self, entry_id: UUID, actor: Actor) -> TimeEntryRead:
        return to_read(self._require(entry_id))

    def describe_rates(self, deal_id: UUID, user_id: UUID, actor: Actor) -> RateDescription:
        """What a new entry would freeze right now. Reading before acting (slice 1
        §8.4), and the reason an agent never has to guess which of the three levels
        answers."""
        self._require_deal(deal_id)
        if self.users.get(user_id) is None:
            raise NotFound("user", user_id)
        return self.rates.describe(deal_id=deal_id, user_id=user_id)

    def deal_summary(self, deal_id: UUID, actor: Actor) -> DealTimeSummary:
        """The hours half of a deal's economics, readable with no invoices present.

        Deliberately carries no `revenue` and no `accrued_value`: revenue is the
        invoice (§3, decision 2) and there is no second notion of it. 4B's `DealPnl`
        adds `revenue` and defines `valore_maturato = ricavi +
        valore_ore_non_fatturate` on top of this, instead of 4A shipping a zero that
        would read as a real figure.
        """
        deal = self._require_deal(deal_id)
        entries = self.repo.for_deal(deal_id)
        billed = billed_entry_ids(self.session, entries)
        unbilled_billable = [e for e in entries if e.billable and e.id not in billed]
        stage = self.pipeline.get(deal.pipeline_stage_id)
        status: Literal["in_progress", "to_invoice", "closed"]
        if stage.type == "open":
            status = "in_progress"
        elif unbilled_billable:
            status = "to_invoice"
        else:
            status = "closed"
        return DealTimeSummary(
            deal_id=deal_id,
            status=status,
            total_hours=sum_hours([e.hours for e in entries]),
            billable_uninvoiced_hours=sum_hours([e.hours for e in unbilled_billable]),
            # Only priced hours contribute; an unpriced one is counted separately and
            # never valued at zero, which would say the work was free.
            unbilled_hours_value=sum_money(
                [line_value(e.hours, e.applied_rate) for e in unbilled_billable]
            ),
            # Includes the NON-billable hours: an internal meeting costs exactly what
            # it would cost if it were billed, and excluding it would make the deal
            # that demanded more of them look more profitable (§7.1).
            work_cost=sum_money([line_value(e.hours, e.applied_cost) for e in entries]),
            hours_without_rate=sum(1 for e in entries if e.applied_rate is None),
            entries=len(entries),
        )

    # `list` stays the last method in this class -- the unconditional project rule.
    def list(self, query: TimeEntryListQuery, actor: Actor) -> TimeEntryPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        return TimeEntryPage(
            items=[to_read(entry) for entry in items],
            next_cursor=items[-1].id if has_more and items else None,
        )
