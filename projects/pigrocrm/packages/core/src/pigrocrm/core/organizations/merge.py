"""Folding one organization into another (REB-995, design `2026-11-09-suppliers-purchases-and-iva`
§ 1, «Duplicates»): every row that points at the loser points at the survivor afterwards, and the
loser is archived with a trace on both timelines.

The same walk serves the preview and the merge, so the two cannot drift: with `apply` false it
counts what each step would touch and writes nothing. The steps are `REFERENCES` (a foreign key
column), `LINKED` (a polymorphic `entity_type` and `entity_id` pair, no foreign key) and the four
that need a decision of their own: contact points, Gmail message links, open automation tasks
and pending proposals. `test_organization_merge.py` walks the metadata and fails when a table
gains a foreign key to `organizations` that none of them covers, which is how a later table (a
purchase's `supplier_id`) is made to join the list the day it appears.

One row lives outside the space's database: the registry's `rebase_engagements.customer_id`,
which the engagements door answers to the hub. With a registry (`EngagementRegistry`) the walk
points it at the survivor too, through the registry's own session (REB-1031).

This module takes no actor and commits nothing: `OrganizationService.merge` authorises, locks the
two rows and owns both transactions.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    Column,
    Table,
    and_,
    delete,
    exists,
    func,
    literal,
    literal_column,
    select,
    update,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.bank.models import OrganizationIban
from pigrocrm.core.collaborators.models import Engagement, MonthlyReport
from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.engagements.registry import EngagementRegistry
from pigrocrm.core.gmail.models import EmailDraft, GmailMessageLink
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.notes.models import Note
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.people.models import Person
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.tasks.models import Task

ENTITY = "organization"


@dataclass(frozen=True)
class Reference:
    """One foreign key column that points at `organizations.id`."""

    label: str
    column: Column[Any]


def _col(model: Any, name: str) -> Column[Any]:
    table: Table = model.__table__
    return table.c[name]


# Contact points and tasks are in their own steps below: a column that moves wholesale cannot
# carry them, since a unique index on each decides who may stay.
REFERENCES: tuple[Reference, ...] = (
    Reference("deals", _col(Deal, "customer_id")),
    Reference("contracts", _col(Contract, "customer_id")),
    Reference("invoices", _col(Invoice, "customer_id")),
    Reference("people", _col(Person, "customer_id")),
    Reference("documents", _col(Document, "customer_id")),
    Reference("due_items", _col(DueItem, "organization_id")),
    Reference("purchases", _col(Purchase, "supplier_id")),
    Reference("engagements_freelancer", _col(Engagement, "freelancer_id")),
    Reference("engagements_client", _col(Engagement, "client_organization_id")),
    Reference("monthly_reports", _col(MonthlyReport, "freelancer_id")),
)

# Not foreign keys: the row a polymorphic pair names depends on `entity_type`.
LINKED: tuple[tuple[str, Any], ...] = (
    ("notes", Note),
    ("activities", Activity),
    ("email_drafts", EmailDraft),
)

# What the steps with a decision of their own are called in the preview.
CONTACT_POINTS = "contact_points"
TASKS = "tasks"
GMAIL_LINKS = "gmail_message_links"
IBANS = "organization_ibans"
PROPOSALS = "proposals"
REGISTRY_ENGAGEMENTS = "rebase_engagements"

# Foreign keys to `organizations.id` that the steps above and below cover, by `table.column`.
COVERED_COLUMNS: frozenset[str] = frozenset(
    {f"{ref.column.table.name}.{ref.column.name}" for ref in REFERENCES}
    | {
        f"{_col(ContactPoint, 'customer_id').table.name}.customer_id",
        f"{_col(Task, 'customer_id').table.name}.customer_id",
        f"{_col(OrganizationIban, 'organization_id').table.name}.organization_id",
    }
)

# Fields copied from the loser when the survivor's are empty, one by one: a field the survivor
# has is never overwritten, and one the loser leaves empty never empties the survivor's. A group
# is a set of fields that read together (the address, the payment terms) and is listed together.
FILL_GROUPS: tuple[tuple[str, ...], ...] = (
    ("vat_number",),
    ("tax_code",),
    ("certified_email",),
    ("website",),
    ("status",),
    ("address", "postal_code", "city", "province"),
    ("payment_days",),
    ("supplier_payment_days",),
    ("supplier_kind", "supplier_area", "supplier_supplies"),
    ("customer_kind", "sdi_code"),
    ("owner_id",),
)
_TERMS_SWITCHES = (
    ("payment_days", "payment_end_of_month"),
    ("supplier_payment_days", "supplier_payment_end_of_month"),
)
# The customer pair is the exception: `customer_kind` says what `sdi_code` means, so both must
# be empty on the survivor, and a public body's six-character code never lands beside a business.
_PAIR_NEEDS_BOTH_EMPTY = ("customer_kind", "sdi_code")


@dataclass
class MergeOutcome:
    moved: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    roles: list[str] = field(default_factory=list)
    filled_fields: list[str] = field(default_factory=list)
    custom_fields_added: list[str] = field(default_factory=list)
    shares_survivor: bool = False
    moves_hub_freelancer_id: bool = False

    @property
    def total_moved(self) -> int:
        return sum(self.moved.values())


def _count(session: Session, column: Column[Any], value: UUID) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(column.table).where(column == value)
        ).scalar_one()
    )


def _note(outcome: MergeOutcome, bucket: str, label: str, count: int) -> None:
    if count:
        target = outcome.moved if bucket == "moved" else outcome.dropped
        target[label] = target.get(label, 0) + count


class OrganizationMerge:
    def __init__(self, session: Session, registry: EngagementRegistry | None = None) -> None:
        self.session = session
        self.registry = registry

    def run(self, survivor: Organization, loser: Organization, *, apply: bool) -> MergeOutcome:
        outcome = MergeOutcome()
        survivor_id, loser_id = survivor.id, loser.id
        self._framework_agreements(outcome, loser_id, survivor_id, apply)

        for reference in REFERENCES:
            self._move_column(
                outcome, reference.label, reference.column, loser_id, survivor_id, apply
            )
        self._tasks(outcome, loser_id, survivor_id, apply)
        self._contact_points(outcome, loser_id, survivor_id, apply)
        self._ibans(outcome, loser_id, survivor_id, apply)
        self._gmail_links(outcome, loser_id, survivor_id, apply)
        for label, model in LINKED:
            self._move_linked(outcome, label, model, loser_id, survivor_id, apply)
        self._proposals(outcome, loser_id, survivor_id, apply)
        self._registry_rows(outcome, loser_id, survivor_id, apply)
        self._fields(outcome, survivor, loser, apply)
        return outcome

    # -- foreign keys ----------------------------------------------------------------------

    def _framework_agreements(
        self, outcome: MergeOutcome, loser_id: UUID, survivor_id: UUID, apply: bool
    ) -> None:
        """A freelancer holds one live framework agreement per regime
        (`uq_documents_framework_agreement`): the loser's, when the survivor already has one of
        the same regime, is archived (it stays readable, and is counted as dropped in the trace of
        the merge) and then moved with the rest. The survivor's stays the live one."""
        table = cast(Table, Document.__table__)
        mine = table.alias("mine")
        collides = and_(
            table.c.customer_id == loser_id,
            table.c.type == "framework_agreement",
            table.c.deleted_at.is_(None),
            exists().where(
                mine.c.customer_id == survivor_id,
                mine.c.type == "framework_agreement",
                mine.c.letter_type == table.c.letter_type,
                mine.c.deleted_at.is_(None),
            ),
        )
        if apply:
            result = self.session.execute(
                update(table).where(collides).values(deleted_at=datetime.now(UTC))
            )
            dropped = int(result.rowcount)  # type: ignore[attr-defined]
        else:
            dropped = int(
                self.session.execute(
                    select(func.count()).select_from(table).where(collides)
                ).scalar_one()
            )
        _note(outcome, "dropped", "framework_agreements", dropped)

    def _move_column(
        self,
        outcome: MergeOutcome,
        label: str,
        column: Column[Any],
        loser_id: UUID,
        survivor_id: UUID,
        apply: bool,
    ) -> None:
        if apply:
            result = self.session.execute(
                update(column.table).where(column == loser_id).values({column.name: survivor_id})
            )
            _note(outcome, "moved", label, int(result.rowcount))  # type: ignore[attr-defined]
        else:
            _note(outcome, "moved", label, _count(self.session, column, loser_id))

    def _tasks(self, outcome: MergeOutcome, loser_id: UUID, survivor_id: UUID, apply: bool) -> None:
        """An open task an automation rule made is unique per rule and subject
        (`uq_tasks_rule_open`): the loser's, when the survivor already has one for the same
        rule, is archived and then moved with the rest, so none of them is lost silently and
        none collides."""
        table = cast(Table, Task.__table__)
        mine = table.alias("mine")
        collides = and_(
            table.c.customer_id == loser_id,
            table.c.rule.is_not(None),
            table.c.status == "open",
            table.c.deleted_at.is_(None),
            exists().where(
                mine.c.customer_id == survivor_id,
                mine.c.rule == table.c.rule,
                mine.c.status == "open",
                mine.c.deleted_at.is_(None),
            ),
        )
        if apply:
            result = self.session.execute(
                update(table).where(collides).values(deleted_at=datetime.now(UTC))
            )
            dropped = int(result.rowcount)  # type: ignore[attr-defined]
        else:
            dropped = int(
                self.session.execute(
                    select(func.count()).select_from(table).where(collides)
                ).scalar_one()
            )
        _note(outcome, "dropped", TASKS, dropped)
        self._move_column(outcome, TASKS, table.c.customer_id, loser_id, survivor_id, apply)

    # -- contact points --------------------------------------------------------------------

    def _contact_points(
        self, outcome: MergeOutcome, loser_id: UUID, survivor_id: UUID, apply: bool
    ) -> None:
        """A way to reach the survivor it already has is dropped; the others move after its own,
        a loser's primary stays primary only for a kind the survivor has no primary in."""
        theirs = list(
            self.session.scalars(
                select(ContactPoint)
                .where(ContactPoint.customer_id == survivor_id)
                .order_by(ContactPoint.position)
            )
        )
        known = {(point.kind, point.normalized) for point in theirs}
        has_primary = {point.kind for point in theirs if point.is_primary}
        has_any = {point.kind for point in theirs}
        position = max((point.position for point in theirs), default=-1)
        moving = list(
            self.session.scalars(
                select(ContactPoint)
                .where(ContactPoint.customer_id == loser_id)
                .order_by(ContactPoint.position)
            )
        )
        dropped = 0
        moved = 0
        for point in moving:
            key = (point.kind, point.normalized)
            if key in known:
                dropped += 1
                if apply:
                    self.session.delete(point)
                continue
            known.add(key)
            moved += 1
            primary = point.kind not in has_primary and (
                point.is_primary or point.kind not in has_any
            )
            has_any.add(point.kind)
            if primary:
                has_primary.add(point.kind)
            if apply:
                position += 1
                point.customer_id = survivor_id
                point.position = position
                point.is_primary = primary
        if apply:
            self.session.flush()
        _note(outcome, "moved", CONTACT_POINTS, moved)
        _note(outcome, "dropped", CONTACT_POINTS, dropped)

    def _ibans(self, outcome: MergeOutcome, loser_id: UUID, survivor_id: UUID, apply: bool) -> None:
        """An account the survivor already has is not carried (`uq_organization_ibans_
        organization_iban`); when the loser's copy was accepted and the survivor's still waits
        for a person, the survivor's becomes accepted, since a person's yes is not lost."""
        have = {
            row.iban: row
            for row in self.session.scalars(
                select(OrganizationIban).where(OrganizationIban.organization_id == survivor_id)
            )
        }
        moved = dropped = 0
        for row in self.session.scalars(
            select(OrganizationIban).where(OrganizationIban.organization_id == loser_id)
        ):
            twin = have.get(row.iban)
            if twin is None:
                moved += 1
                if apply:
                    row.organization_id = survivor_id
                continue
            dropped += 1
            if apply:
                if row.state == "accepted" and twin.state == "proposed":
                    twin.state = "accepted"
                self.session.delete(row)
        if apply:
            self.session.flush()
        _note(outcome, "moved", IBANS, moved)
        _note(outcome, "dropped", IBANS, dropped)

    # -- polymorphic rows ------------------------------------------------------------------

    def _gmail_links(
        self, outcome: MergeOutcome, loser_id: UUID, survivor_id: UUID, apply: bool
    ) -> None:
        """A mail the survivor is already linked to is linked once (`uq_gmail_message_links_
        triple`): the loser's second link is dropped."""
        table = cast(Table, GmailMessageLink.__table__)
        mine = table.alias("mine")
        duplicated = and_(
            table.c.entity_type == ENTITY,
            table.c.entity_id == loser_id,
            exists().where(
                mine.c.entity_type == ENTITY,
                mine.c.entity_id == survivor_id,
                mine.c.gmail_message_id == table.c.gmail_message_id,
            ),
        )
        if apply:
            result = self.session.execute(delete(table).where(duplicated))
            dropped = int(result.rowcount)  # type: ignore[attr-defined]
        else:
            dropped = int(
                self.session.execute(
                    select(func.count()).select_from(table).where(duplicated)
                ).scalar_one()
            )
        _note(outcome, "dropped", GMAIL_LINKS, dropped)
        self._move_linked(outcome, GMAIL_LINKS, GmailMessageLink, loser_id, survivor_id, apply)

    def _move_linked(
        self,
        outcome: MergeOutcome,
        label: str,
        model: Any,
        loser_id: UUID,
        survivor_id: UUID,
        apply: bool,
    ) -> None:
        table = cast(Table, model.__table__)
        where = and_(table.c.entity_type == ENTITY, table.c.entity_id == loser_id)
        if apply:
            result = self.session.execute(update(table).where(where).values(entity_id=survivor_id))
            _note(outcome, "moved", label, int(result.rowcount))  # type: ignore[attr-defined]
        else:
            _note(
                outcome,
                "moved",
                label,
                int(
                    self.session.execute(
                        select(func.count()).select_from(table).where(where)
                    ).scalar_one()
                ),
            )

    def _proposals(
        self, outcome: MergeOutcome, loser_id: UUID, survivor_id: UUID, apply: bool
    ) -> None:
        """A contract proposal still waiting names its customer inside `proposed_fields`; a
        decided one is history and keeps what it said."""
        named = Proposal.proposed_fields["contract"]["customer_id"].astext == str(loser_id)
        waiting = and_(Proposal.status == "pending", Proposal.target_type == "contract", named)
        if apply:
            result = self.session.execute(
                update(Proposal)
                .where(waiting)
                .values(
                    proposed_fields=func.jsonb_set(
                        Proposal.proposed_fields,
                        literal_column("'{contract,customer_id}'"),
                        sql_cast(literal(json.dumps(str(survivor_id))), JSONB),
                    )
                )
                .execution_options(synchronize_session=False)
            )
            count = int(result.rowcount)  # type: ignore[attr-defined]
        else:
            count = int(
                self.session.execute(
                    select(func.count()).select_from(Proposal).where(waiting)
                ).scalar_one()
            )
        _note(outcome, "moved", PROPOSALS, count)

    # -- outside the space's database ------------------------------------------------------

    def _registry_rows(
        self, outcome: MergeOutcome, loser_id: UUID, survivor_id: UUID, apply: bool
    ) -> None:
        """The registry's rows that name the loser as the space's customer of a hub match
        (REB-1031): written on the registry's session, which `OrganizationService.merge`
        commits before the space's own, so a failure in between leaves the registry naming a
        live organization of the same company and the merge still runnable."""
        if self.registry is None:
            return
        if apply:
            moved = self.registry.repoint(loser_id, survivor_id)
        else:
            moved = self.registry.count(loser_id)
        _note(outcome, "moved", REGISTRY_ENGAGEMENTS, moved)

    # -- the row itself --------------------------------------------------------------------

    def _fields(
        self, outcome: MergeOutcome, survivor: Organization, loser: Organization, apply: bool
    ) -> None:
        outcome.roles = list(survivor.roles) + [
            role for role in loser.roles if role not in survivor.roles
        ]
        outcome.shares_survivor = (
            survivor.legal_entity_id is not None
            and survivor.legal_entity_id != loser.legal_entity_id
        )
        outcome.moves_hub_freelancer_id = (
            loser.hub_freelancer_id is not None and survivor.hub_freelancer_id is None
        )
        outcome.custom_fields_added = sorted(
            key for key in loser.custom_fields if key not in survivor.custom_fields
        )
        for group in FILL_GROUPS:
            if group == _PAIR_NEEDS_BOTH_EMPTY:
                # `customer_kind` says what `sdi_code` means, so they travel together.
                if all(getattr(survivor, name) is None for name in group):
                    outcome.filled_fields.extend(
                        name for name in group if getattr(loser, name) is not None
                    )
            else:
                outcome.filled_fields.extend(
                    name
                    for name in group
                    if getattr(survivor, name) is None and getattr(loser, name) is not None
                )
        # The switch is never null, so it follows the days it qualifies.
        for days, end_of_month in _TERMS_SWITCHES:
            if days in outcome.filled_fields:
                outcome.filled_fields.append(end_of_month)
        if not apply:
            return
        for name in outcome.filled_fields:
            setattr(survivor, name, getattr(loser, name))
        survivor.roles = outcome.roles
        survivor.custom_fields = {**loser.custom_fields, **survivor.custom_fields}
        if outcome.shares_survivor:
            survivor.legal_entity_id = None
        if survivor.is_withholding_agent is False and loser.is_withholding_agent:
            survivor.is_withholding_agent = True
        if outcome.moves_hub_freelancer_id:
            # The unique index counts an archived row, so the loser lets go first.
            hub_id = loser.hub_freelancer_id
            loser.hub_freelancer_id = None
            self.session.flush()
            survivor.hub_freelancer_id = hub_id
        loser.deleted_at = datetime.now(UTC)
        self.session.flush()
        self.session.expire(survivor, ["contact_points"])
        self.session.expire(loser, ["contact_points"])
