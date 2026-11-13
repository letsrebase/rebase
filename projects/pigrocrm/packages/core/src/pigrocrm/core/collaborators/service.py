"""Engagements with freelancers (design § 1 and § 3): create, change, move through the states,
read, and answer when a freelancer is free.

Every read and write is an administrator's (the hub's matches were admin-only and so is this).
What an agent may do is `agent_actions.py`'s table: reading, updating a draft and availability
with the administrator's token, and creating, cancelling, closing or setting tax data only with
`full_access`. Activating an engagement is a person's act.
"""

import datetime as dt
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import rules
from pigrocrm.core.collaborators.forecast import EngagementForecastService
from pigrocrm.core.collaborators.models import (
    LIVE_STATES,
    REPORTING_STATES,
    Engagement,
    MonthlyReport,
)
from pigrocrm.core.collaborators.outbox import NOTICE_COLUMNS, NoticeOutbox
from pigrocrm.core.collaborators.repository import EngagementRepository
from pigrocrm.core.collaborators.rules import ENTITY, Commitment
from pigrocrm.core.collaborators.schemas import (
    AVAILABILITY_DEFAULT_WEEKS,
    AVAILABILITY_MAX_WEEKS,
    AvailabilityStatus,
    AvailabilityWeek,
    EngagementCounts,
    EngagementCreate,
    EngagementListQuery,
    EngagementPage,
    EngagementRead,
    EngagementUpdate,
    EngagementWaiting,
    EngagementWarning,
    FreelancerAvailability,
    FreelancerTaxData,
)
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.db import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.errors import Conflict, NotFound, SigningUnavailable, ValidationFailed
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.schemas import OrganizationRead, OrganizationUpdate
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.schemas import changed_keys, reject_cleared_columns, supplied_changes

# What can still change on an engagement that is out for signature or signed: none of it is
# printed on the letter. A draft changes freely; a final one not at all.
SIGNED_EDITABLE = frozenset({"days_per_week", "client_organization_id", "deal_id", "contract_id"})
PAYMENT_FIELDS = ("payment_rule", "payment_days", "payment_end_of_month", "collection_days")

LABELS = {"free": "libera", "committed": "impegnata", "unknown": "sconosciuta"}


def _days(value: Decimal) -> str:
    """`2.5` as `2,5` and `3` as `3`, as a sentence says it."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


# The notice each state a transition reaches tells the hub (`NoticeOutbox`): out for signature is
# `sent`, active is `signed` (or `updated` when no letter was signed, see `transition`).
TRANSITION_NOTICES = {
    "awaiting_signature": "sent",
    "active": "signed",
    "concluded": "closed",
    "cancelled": "cancelled",
}

# What a cancellation does about the engagement's letter, handed in by whoever can reach Documenso
# (`SigningService.cancel_letters_of`): it runs inside the cancellation's transaction and answers
# what is left to do after the commit (the freelancer's mail).
CancelHook = Callable[["Engagement", Actor], Callable[[], None]]


class EngagementService:
    def __init__(self, session: Session, on_cancel: CancelHook | None = None) -> None:
        """`on_cancel` is how an adapter that can reach Documenso cancels the engagement's letter
        with it. Without one, a letter that was never sent is cancelled here, and one that is out
        for signature refuses the cancellation: its link would stay alive."""
        self.session = session
        self.repo = EngagementRepository(session)
        self.activities = ActivityService(session)
        self.on_cancel = on_cancel

    # ---- reads ----------------------------------------------------------------------------

    def get(self, engagement_id: UUID, actor: Actor) -> EngagementRead:
        actor.require_admin("read_engagements")
        return self._read(self._found(engagement_id))

    # ---- writes ---------------------------------------------------------------------------

    def create(self, data: EngagementCreate, actor: Actor) -> EngagementRead:
        """A draft. The freelancer must be an organization with the role `freelancer`; an
        overlap with another live engagement of theirs is saved with a warning."""
        actor.require_admin("create_engagement")
        freelancer = self._freelancer(data.freelancer_id)
        if data.days_per_week is None:
            raise ValidationFailed(
                ENTITY,
                "days_per_week",
                "servono i giorni a settimana: da 0,5 a 5, a passi di 0,5",
                expected="0.5 | 1 | ... | 5",
            )
        values = data.model_dump(exclude={"freelancer_id", "hub_match_id"})
        values["letter_type"] = data.letter_type or rules.default_letter_type(
            freelancer.supplier_kind
        )
        self._validate(values, freelancer, None)
        values["client_organization_id"] = self._client(values, freelancer.id)
        if data.hub_match_id is not None and self.repo.by_hub_match(data.hub_match_id):
            raise self._match_taken(data.hub_match_id)
        engagement = Engagement(
            freelancer_id=freelancer.id,
            hub_match_id=data.hub_match_id,
            state="draft",
            letter_number=self.repo.next_letter_number(today_local().year),
            **values,
        )
        try:
            with self.session.begin_nested():
                self.repo.add(engagement)
        except IntegrityError as exc:
            if "uq_engagements_hub_match_id" in str(exc.orig) and data.hub_match_id is not None:
                raise self._match_taken(data.hub_match_id) from exc
            raise
        self.activities.record(
            ENTITY, engagement.id, "created", actor, {"freelancer_id": str(freelancer.id)}
        )
        NoticeOutbox(self.session).emit(engagement, "created")
        self.session.commit()
        return self._read(engagement)

    def update(self, engagement_id: UUID, data: EngagementUpdate, actor: Actor) -> EngagementRead:
        """A draft changes freely. Out for signature or signed, only `days_per_week` and the
        client links change (what an imported engagement lacks, never printed on the letter).
        Naming `payment_rule` restates the whole rule: the fields it does not use are cleared."""
        actor.require_admin("update_engagement")
        engagement = self._found(engagement_id, lock=True)
        changes = supplied_changes(data)
        reject_cleared_columns(ENTITY, Engagement, changes)
        if engagement.state in ("concluded", "cancelled"):
            raise Conflict(
                ENTITY,
                "l'incarico e' chiuso: non si modifica piu'",
                state=engagement.state,
            )
        if engagement.state != "draft":
            frozen = sorted(set(changes) - SIGNED_EDITABLE)
            if frozen:
                raise Conflict(
                    ENTITY,
                    "la lettera e' gia' partita: si cambiano solo i giorni a settimana e il "
                    "cliente. Annulla l'incarico e rifallo per cambiare altro",
                    fields=frozen,
                )
        # Only what the request touches is re-checked: an archived client or freelancer must not
        # stop an admin filling in `days_per_week` of an imported engagement.
        freelancer = self.repo.visible_organization(engagement.freelancer_id)
        if freelancer is None:
            raise NotFound("organization", engagement.freelancer_id)
        values = {
            column: changes.get(column, getattr(engagement, column)) for column in _VALUE_COLUMNS
        }
        if "payment_rule" in changes:
            for column in PAYMENT_FIELDS[1:]:
                values[column] = changes.get(column, False if column.endswith("month") else None)
        # A deal or contract named without the client: the client follows it.
        if ("deal_id" in changes or "contract_id" in changes) and (
            "client_organization_id" not in changes
        ):
            values["client_organization_id"] = None
        self._validate(values, freelancer, set(changes))
        if {"client_organization_id", "deal_id", "contract_id"} & set(changes):
            client = self._client(values, freelancer.id)
            if client != engagement.client_organization_id:
                changes["client_organization_id"] = client
        # A rule restated clears what it does not use, even when the request did not say so.
        if "payment_rule" in changes:
            for column in PAYMENT_FIELDS:
                changes[column] = values[column]
        changed = changed_keys(engagement, changes)
        if not changed:
            return self._read(engagement)
        for key in changed:
            setattr(engagement, key, changes[key])
        self.session.flush()
        # What the freelancer is forecast to be paid follows the days and the client links.
        EngagementForecastService(self.session).sync(engagement)
        self.activities.record(ENTITY, engagement.id, "updated", actor, {"changed": changed})
        if NOTICE_COLUMNS.intersection(changed):
            NoticeOutbox(self.session).emit(engagement, "updated")
        self.session.commit()
        return self._read(engagement)

    def transition(
        self,
        engagement_id: UUID,
        state: str,
        actor: Actor,
        *,
        ended_on: dt.date | None = None,
    ) -> EngagementRead:
        """Moves an engagement to `state` if the table allows it (`rules.TRANSITIONS`). Closing
        ends it on `ended_on` (today when omitted) when it would run past that day or has no
        end, so the weeks after it stop counting as committed."""
        rules.check_state(state)
        # The action each target state asks for, spelled out: `agent_actions.py` classifies them.
        if state == "awaiting_signature":
            actor.require_admin("send_engagement_for_signature")
        elif state == "active":
            actor.require_admin("activate_engagement")
        elif state == "concluded":
            actor.require_admin("close_engagement")
        else:
            actor.require_admin("cancel_engagement")
        engagement = self._found(engagement_id, lock=True)
        rules.check_transition(engagement.state, state)
        previous = engagement.state
        if state == "cancelled" and previous == "active" and engagement.starts_on <= today_local():
            raise ValidationFailed(
                ENTITY,
                "state",
                "un incarico gia' iniziato si chiude, non si annulla",
                expected="close_engagement",
            )
        if state == "concluded":
            last = ended_on or today_local()
            if last < engagement.starts_on:
                raise ValidationFailed(
                    ENTITY,
                    "ended_on",
                    "l'incarico non puo' finire prima di essere iniziato",
                    expected=f">= {engagement.starts_on.isoformat()}",
                )
            if engagement.ends_on is None or engagement.ends_on > last:
                engagement.ends_on = last
        elif ended_on is not None:
            raise ValidationFailed(
                ENTITY, "ended_on", "la data di fine si da' solo chiudendo un incarico attivo"
            )
        after = self._cancel_letter(engagement, actor) if state == "cancelled" else None
        engagement.state = state
        self.session.flush()
        # Signed or closed, the engagement owes the freelancer its forecasts; cancelled, none.
        EngagementForecastService(self.session).sync(engagement)
        self.activities.record(
            ENTITY, engagement.id, "state_changed", actor, {"from": previous, "to": state}
        )
        outbox = NoticeOutbox(self.session)
        notice = outbox.emit(engagement, TRANSITION_NOTICES[state])
        if notice is not None and state == "active" and notice.payload["signed_at"] is None:
            # Activated by a person with no signed letter behind it: nothing was signed, and the
            # hub's reward waits for a signature, so the notice says only that the row changed.
            notice.kind = "updated"
        self.session.commit()
        if after is not None:
            after()
        return self._read(engagement)

    def cancel(self, engagement_id: UUID, actor: Actor) -> EngagementRead:
        return self.transition(engagement_id, "cancelled", actor)

    def _cancel_letter(self, engagement: Engagement, actor: Actor) -> Callable[[], None] | None:
        """The engagement's letter goes with it (collaborators design § 2: a cancelled document is
        cancelled on Documenso too). With the hook, it does all of that; without one, a letter that
        never left is cancelled here and one that is out for signature refuses the cancellation."""
        if self.on_cancel is not None:
            return self.on_cancel(engagement, actor)
        letters = list(
            self.session.scalars(
                select(Document).where(
                    Document.engagement_id == engagement.id,
                    Document.deleted_at.is_(None),
                    Document.signature_state.in_(("generated", "pending", "sent")),
                )
            )
        )
        if any(letter.signature_state == "sent" for letter in letters):
            raise SigningUnavailable(
                "La lettera di questo incarico è già partita per la firma: va annullata dal "
                "servizio di firma, che qui non è raggiungibile."
            )
        for letter in letters:
            letter.signature_state = "cancelled"
            letter.cancel_reason = "Annullato con il suo incarico."
        return None

    def close(
        self, engagement_id: UUID, actor: Actor, ended_on: dt.date | None = None
    ) -> EngagementRead:
        return self.transition(engagement_id, "concluded", actor, ended_on=ended_on)

    # ---- internals ------------------------------------------------------------------------

    def _found(self, engagement_id: UUID, *, lock: bool = False) -> Engagement:
        found = self.repo.get(engagement_id, lock=lock)
        if found is None:
            raise NotFound(ENTITY, engagement_id)
        return found

    def _freelancer(self, organization_id: UUID) -> Organization:
        found = self.repo.organization(organization_id)
        if found is None:
            raise NotFound("organization", organization_id)
        if "freelancer" not in found.roles:
            raise ValidationFailed(
                ENTITY,
                "freelancer_id",
                "l'anagrafica non ha il ruolo di freelance: aggiungilo prima",
                expected="roles con freelancer",
            )
        return found

    @staticmethod
    def _match_taken(hub_match_id: UUID) -> Conflict:
        return Conflict(
            ENTITY,
            "esiste gia' un incarico per questo match dell'hub",
            field="hub_match_id",
            hub_match_id=str(hub_match_id),
        )

    def _validate(
        self, values: dict[str, Any], freelancer: Organization, touched: set[str] | None
    ) -> None:
        """The rules of the engagement `values` describe. `touched` names the columns an update
        sends: only the rules about them run, so filling `days_per_week` of an imported engagement
        never trips over a letter type or a term the import brought. `None` runs them all."""

        def hit(*columns: str) -> bool:
            return touched is None or bool(touched.intersection(columns))

        if hit("starts_on", "ends_on"):
            rules.check_dates(values["starts_on"], values["ends_on"])
        if hit("fee", "fee_unit"):
            rules.check_fee(values["fee"], values["fee_unit"])
        if hit("expected_days"):
            rules.check_expected_days(values["expected_days"])
        if hit("days_per_week"):
            rules.check_days_per_week(values["days_per_week"])
        if hit("letter_type"):
            rules.check_letter_type(values["letter_type"], freelancer.supplier_kind)
        if hit(*PAYMENT_FIELDS):
            rules.check_payment_rule(
                values["payment_rule"],
                values["payment_days"],
                values["payment_end_of_month"],
                values["collection_days"],
            )

    def _client(self, values: dict[str, Any], freelancer_id: UUID) -> UUID | None:
        """The client organization the links agree on: the deal's or the contract's customer
        when none is named, and a refusal when they point at different companies."""
        client_id = values["client_organization_id"]
        named: set[UUID] = set()
        if client_id is not None:
            if self.repo.organization(client_id) is None:
                raise NotFound("organization", client_id)
            named.add(client_id)
        if values["deal_id"] is not None:
            deal = self.session.get(Deal, values["deal_id"])
            if deal is None or deal.deleted_at is not None:
                raise NotFound("deal", values["deal_id"])
            named.add(deal.customer_id)
        if values["contract_id"] is not None:
            contract = self.session.get(Contract, values["contract_id"])
            if contract is None or contract.deleted_at is not None:
                raise NotFound("contract", values["contract_id"])
            named.add(contract.customer_id)
        if len(named) > 1:
            raise ValidationFailed(
                ENTITY,
                "client_organization_id",
                "il cliente, il deal e il contratto indicati non sono della stessa azienda",
            )
        if freelancer_id in named:
            raise ValidationFailed(
                ENTITY,
                "client_organization_id",
                "il cliente non puo' essere il freelance stesso",
            )
        return next(iter(named), None)

    def _read(self, engagement: Engagement, *, with_warnings: bool = True) -> EngagementRead:
        return self._reads([engagement], with_warnings=with_warnings)[0]

    def _reads(
        self, engagements: Sequence[Engagement], *, with_warnings: bool = True
    ) -> list[EngagementRead]:
        """The engagements as the API says them, with the names, the stored letter and what waits
        for a person read in one batch for the whole page, never once per row."""
        ids = [engagement.id for engagement in engagements]
        today = today_local()
        wanted = {engagement.freelancer_id for engagement in engagements}
        wanted.update(
            engagement.client_organization_id
            for engagement in engagements
            if engagement.client_organization_id is not None
        )
        names = self.repo.organization_names(wanted)
        letter_of = self.repo.letter_documents(ids)
        undecided = self.repo.undecided_months(ids, today)
        out: list[EngagementRead] = []
        for engagement in engagements:
            read = EngagementRead.model_validate(engagement)
            read.freelancer_name = names.get(engagement.freelancer_id)
            read.letter_document_id = letter_of.get(engagement.id)
            read.today = today
            client = engagement.client_organization_id
            if client is not None and client not in names:
                # A freelancer shared across legal entities, an engagement for a client this
                # admin's scope does not show: the days and the state stay, what is the
                # client's goes.
                read.redacted = True
                read.role = read.fee = None
                read.client_organization_id = read.deal_id = read.contract_id = None
                # The letter prints the client and the fee: it is as hidden as they are.
                read.letter_document_id = None
            elif client is not None:
                read.client_name = names[client]
            if with_warnings and engagement.state in LIVE_STATES:
                read.warnings = self._warnings(engagement)
            read.waiting = self._waiting(
                engagement, engagement.id in letter_of, undecided.get(engagement.id, [])
            )
            out.append(read)
        return out

    @staticmethod
    def _waiting(
        engagement: Engagement, has_letter: bool, undecided: Sequence[MonthlyReport]
    ) -> list[EngagementWaiting]:
        """What the engagement waits for a person to do, in the words of `repository.
        waiting_for_a_person`, which the list's «Da fare» tab and its count are built from."""
        out: list[EngagementWaiting] = []
        if engagement.state == "draft":
            out.append(
                EngagementWaiting(code="letter_not_sent" if has_letter else "letter_not_created")
            )
        if engagement.state in LIVE_STATES:
            if engagement.days_per_week is None:
                out.append(EngagementWaiting(code="missing_days_per_week"))
            if engagement.deal_id is None and engagement.contract_id is None:
                out.append(EngagementWaiting(code="missing_client"))
        if engagement.state in REPORTING_STATES:
            out.extend(
                EngagementWaiting(code="report_undecided", month=row.month, tacit_on=row.tacit_on)
                for row in undecided
            )
        return out

    def _warnings(self, engagement: Engagement) -> list[EngagementWarning]:
        """What the freelancer's other live engagements say about this one. Not a refusal: two
        engagements in one week are what a freelancer sometimes does, and the admin decides.

        `overlap`: another live engagement shares a day with this one; it says the most days a
        week they are committed to in the overlap, this one included, or that it cannot say
        because one of them has no `days_per_week`. `over_capacity`: that peak is above the five
        days a week a freelancer has.
        """
        others = [
            Commitment(o.id, o.starts_on, o.ends_on, o.days_per_week)
            for o in self.repo.live_of_freelancer(engagement.freelancer_id, excluding=engagement.id)
        ]
        peak, ids = rules.peak_commitment(
            engagement.starts_on, engagement.ends_on, engagement.days_per_week, others
        )
        if not ids:
            return []
        many = len(ids) > 1
        subject = "altri incarichi" if many else "un altro incarico"
        if peak is None:
            message = (
                f"Si sovrappone a {subject} dello stesso freelance, ma non si sa quanti "
                "giorni a settimana: la disponibilita' e' sconosciuta."
            )
        else:
            message = (
                f"Si sovrappone a {subject} dello stesso freelance: nelle settimane in comune "
                f"e' impegnato fino a {_days(peak)} giorni a settimana."
            )
        warnings = [
            EngagementWarning(
                code="overlap", message=message, engagement_ids=ids, peak_days_per_week=peak
            )
        ]
        if peak is not None and peak > rules.WEEK_CAPACITY:
            warnings.append(
                EngagementWarning(
                    code="over_capacity",
                    message=(
                        f"{_days(peak)} giorni a settimana sono piu' dei "
                        f"{_days(rules.WEEK_CAPACITY)} che un freelance ha."
                    ),
                    engagement_ids=ids,
                    peak_days_per_week=peak,
                )
            )
        return warnings

    # ---- the list --------------------------------------------------------------------------

    def counts(self, actor: Actor) -> EngagementCounts:
        """How many engagements wait for a person, are under way and are closed: the count on
        the «Incarichi» entry and on the list's tabs."""
        actor.require_admin("read_engagements")
        found = self.repo.bucket_counts(today_local())
        return EngagementCounts(
            todo=found.get("todo", 0),
            ongoing=found.get("ongoing", 0),
            closed=found.get("closed", 0),
        )

    # Last in the class: a method named `list` would shadow the builtin for every annotation
    # that follows it.
    def list(self, query: EngagementListQuery, actor: Actor) -> EngagementPage:
        actor.require_admin("read_engagements")
        if query.state is not None:
            rules.check_state(query.state)
        rows = self.repo.page(query, today_local())
        page = rows[: query.limit]
        return EngagementPage(
            items=self._reads(page, with_warnings=False),
            next_cursor=page[-1].id if len(rows) > query.limit and page else None,
        )


# The columns an engagement's own rules read, in the order `_validate` needs them.
_VALUE_COLUMNS = (
    "client_organization_id",
    "deal_id",
    "contract_id",
    "role",
    "starts_on",
    "ends_on",
    "days_per_week",
    "fee",
    "fee_unit",
    "expected_days",
    "letter_type",
    "payment_rule",
    "payment_days",
    "payment_end_of_month",
    "collection_days",
)


class FreelancerService:
    """What the CRM says about a freelancer: when they are free, and the tax data their letters
    print."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = EngagementRepository(session)

    def availability(
        self,
        actor: Actor,
        *,
        freelancer_id: UUID | None = None,
        hub_freelancer_id: UUID | None = None,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        excluding_engagement_id: UUID | None = None,
    ) -> FreelancerAvailability:
        """The days a week a freelancer is committed to, by ISO week, from their live
        engagements: the draft, out for signature and active ones, the same three states the
        overlap warning counts, so a strip and its warning never disagree. A week touched by a
        live engagement with no `days_per_week` is `unknown` («sconosciuta») and never `free`.
        `excluding_engagement_id` leaves one out, so the page of a draft is not warned against
        itself. The freelancer is named by the organization id or by the hub's freelancer id,
        one of the two. With no dates: from this week's Monday for twelve weeks."""
        actor.require_admin("get_freelancer_availability")
        if (freelancer_id is None) == (hub_freelancer_id is None):
            raise ValidationFailed(
                ENTITY,
                "freelancer_id",
                "indica il freelance con l'id dell'anagrafica o con quello dell'hub, uno dei due",
            )
        if freelancer_id is not None:
            freelancer = self.repo.organization(freelancer_id)
            missing: UUID = freelancer_id
        else:
            assert hub_freelancer_id is not None
            freelancer = self.repo.organization_by_hub_id(hub_freelancer_id)
            missing = hub_freelancer_id
        if freelancer is None or "freelancer" not in freelancer.roles:
            raise NotFound("organization", missing)

        start, end, weeks = self.weeks_of(
            freelancer.id, date_from, date_to, excluding=excluding_engagement_id
        )
        return FreelancerAvailability(
            freelancer_id=freelancer.id,
            hub_freelancer_id=freelancer.hub_freelancer_id,
            date_from=start,
            date_to=end,
            weeks=weeks,
        )

    def weeks_of(
        self,
        freelancer_id: UUID | None,
        date_from: dt.date | None,
        date_to: dt.date | None,
        *,
        excluding: UUID | None = None,
    ) -> tuple[dt.date, dt.date, list[AvailabilityWeek]]:
        """The window and its weeks, with no permission check: the caller's own (`availability`
        asks an administrator, the hub's door is the token's). `None` for a freelancer the CRM
        holds no organization for, whose weeks are all free: nothing is committed that this
        installation knows of."""
        start = rules.monday(date_from or today_local())
        end = date_to or start + dt.timedelta(weeks=AVAILABILITY_DEFAULT_WEEKS, days=-1)
        if end < start:
            raise ValidationFailed(
                ENTITY,
                "date_to",
                "la fine non puo' essere prima dell'inizio",
                expected=">= date_from",
            )
        if (end - start).days >= AVAILABILITY_MAX_WEEKS * 7:
            raise ValidationFailed(
                ENTITY,
                "date_to",
                f"al massimo {AVAILABILITY_MAX_WEEKS} settimane per volta",
                expected=f"<= {AVAILABILITY_MAX_WEEKS} settimane",
            )
        commitments = (
            [
                Commitment(e.id, e.starts_on, e.ends_on, e.days_per_week)
                for e in self.repo.of_freelancer(
                    freelancer_id,
                    LIVE_STATES,
                    date_from=start,
                    excluding=excluding,
                    # To the Sunday of the last week shown: an engagement that starts on its
                    # Thursday still commits that week.
                    date_to=rules.monday(end) + dt.timedelta(days=6),
                )
            ]
            if freelancer_id is not None
            else []
        )
        out: list[AvailabilityWeek] = []
        for week_start, week_end in rules.weeks(start, end):
            committed, unknown, ids = rules.committed_in(week_start, week_end, commitments)
            status: AvailabilityStatus = (
                "unknown" if unknown else "committed" if committed > 0 else "free"
            )
            out.append(
                AvailabilityWeek(
                    week=rules.iso_week(week_start),
                    starts_on=week_start,
                    ends_on=week_end,
                    status=status,
                    label=LABELS[status],
                    committed_days=committed,
                    free_days=None if unknown else max(Decimal(0), rules.WEEK_CAPACITY - committed),
                    engagement_ids=ids,
                )
            )
        return start, end, out

    def set_tax_data(
        self, freelancer_id: UUID, data: FreelancerTaxData, actor: Actor
    ) -> OrganizationRead:
        """Writes the tax data the letters print on the freelancer's organization (the hub's
        `set_freelancer_tax_data`): tax code, VAT number, address, certified email and the fiscal
        kind. It goes through `OrganizationService.update`, so the VAT number is checked and
        refused when another company holds it. The kind of a flat-rate freelancer still needs
        the written declaration kept as a document (a later card)."""
        actor.require_admin("set_freelancer_tax_data")
        freelancer = self.repo.organization(freelancer_id)
        if freelancer is None:
            raise NotFound("organization", freelancer_id)
        if "freelancer" not in freelancer.roles:
            raise ValidationFailed(
                ENTITY,
                "freelancer_id",
                "l'anagrafica non ha il ruolo di freelance: aggiungilo prima",
                expected="roles con freelancer",
            )
        said = data.model_dump(exclude_unset=True)
        if not said:
            raise ValidationFailed(ENTITY, "tax_code", "nessun dato fiscale da salvare")
        mapping = {"domicile": "address", "fiscal_kind": "supplier_kind"}
        changes = {mapping.get(key, key): value for key, value in said.items()}
        return OrganizationService(self.session).update(
            freelancer_id, OrganizationUpdate.model_validate(changes), actor
        )
