"""Engagements with freelancers (design § 1 and § 3): create, change, move through the states,
read, and answer when a freelancer is free.

Every read and write is an administrator's (the hub's matches were admin-only and so is this).
What an agent may do is `agent_actions.py`'s table: reading, updating a draft and availability
with the administrator's token, and creating, cancelling, closing or setting tax data only with
`full_access`. Activating an engagement is a person's act.
"""

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import rules
from pigrocrm.core.collaborators.forecast import EngagementForecastService
from pigrocrm.core.collaborators.models import LIVE_STATES, Engagement
from pigrocrm.core.collaborators.repository import EngagementRepository
from pigrocrm.core.collaborators.rules import ENTITY, Commitment
from pigrocrm.core.collaborators.schemas import (
    AVAILABILITY_DEFAULT_WEEKS,
    AVAILABILITY_MAX_WEEKS,
    AvailabilityStatus,
    AvailabilityWeek,
    EngagementCreate,
    EngagementListQuery,
    EngagementPage,
    EngagementRead,
    EngagementUpdate,
    EngagementWarning,
    FreelancerAvailability,
    FreelancerTaxData,
)
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.db import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
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


class EngagementService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = EngagementRepository(session)
        self.activities = ActivityService(session)

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
        engagement.state = state
        self.session.flush()
        # Signed or closed, the engagement owes the freelancer its forecasts; cancelled, none.
        EngagementForecastService(self.session).sync(engagement)
        self.activities.record(
            ENTITY, engagement.id, "state_changed", actor, {"from": previous, "to": state}
        )
        self.session.commit()
        return self._read(engagement)

    def cancel(self, engagement_id: UUID, actor: Actor) -> EngagementRead:
        return self.transition(engagement_id, "cancelled", actor)

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
        out = EngagementRead.model_validate(engagement)
        client = engagement.client_organization_id
        if client is not None and self.repo.visible_organization(client) is None:
            # A freelancer shared across legal entities, an engagement for a client this admin's
            # scope does not show: the days and the state stay, what is the client's goes.
            out.redacted = True
            out.role = out.fee = None
            out.client_organization_id = out.deal_id = out.contract_id = None
        if with_warnings and engagement.state in LIVE_STATES:
            out.warnings = self._warnings(engagement)
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

    # Last in the class: a method named `list` would shadow the builtin for every annotation
    # that follows it.
    def list(self, query: EngagementListQuery, actor: Actor) -> EngagementPage:
        actor.require_admin("read_engagements")
        if query.state is not None:
            rules.check_state(query.state)
        rows = self.repo.page(query)
        page = rows[: query.limit]
        return EngagementPage(
            items=[self._read(row, with_warnings=False) for row in page],
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
    ) -> FreelancerAvailability:
        """The days a week a freelancer is committed to, by ISO week, from their active
        engagements. A week touched by an active engagement with no `days_per_week` is
        `unknown` («sconosciuta») and never `free`. The freelancer is named by the organization
        id or by the hub's freelancer id, one of the two. With no dates: from this week's Monday
        for twelve weeks."""
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
        commitments = [
            Commitment(e.id, e.starts_on, e.ends_on, e.days_per_week)
            for e in self.repo.of_freelancer(
                freelancer.id,
                ("active",),
                date_from=start,
                # To the Sunday of the last week shown: an engagement that starts on its Thursday
                # still commits that week.
                date_to=rules.monday(end) + dt.timedelta(days=6),
            )
        ]
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
        return FreelancerAvailability(
            freelancer_id=freelancer.id,
            hub_freelancer_id=freelancer.hub_freelancer_id,
            date_from=start,
            date_to=end,
            weeks=out,
        )

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
