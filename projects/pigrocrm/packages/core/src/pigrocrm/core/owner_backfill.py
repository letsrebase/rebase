"""The one-off that gives a space's customers and deals the owner their creation names (REB-797).

Customers and deals created before owners existed (REB-773 for customers, REB-780 for deals)
carry none. The rule is the card's: a live record without an owner takes the user who
created it, read from the record's earliest `created` entry in the activity log. Nothing is
guessed, so a record is *listed* and left alone when:

- the creation entry names no user (`system`, `rebase`, a job, or no actor id at all);
- the creator is one of the accounts the operator named as generic (`--generic-account`): an
  account several people or agents act through says nothing about who owns the record;
- the creator's account is deactivated, because `CustomerService` refuses an inactive owner
  and this would put one where the product does not;
- the record has no creation entry (a row imported without a timeline).

**Idempotent.** Only a record whose `owner_id` is still empty is ever written, so a second
run finds nothing to set, and an owner a person chose afterwards is never overwritten.

**Dry run first.** `plan_owner_backfill` only reads; `apply_owner_backfill` writes one
`updated` entry per record, under the system actor, with the reason in the payload, so the
timeline says the owner came from this backfill and not from a person.
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal

SOURCE = "REB-797"

Entity = Literal["customer", "deal"]
Reason = Literal["generic_account", "no_user", "inactive_creator", "no_creation_entry"]

# The creation entry is read for these actor types only: a `user` acts as itself and an
# `mcp` actor carries the id of the token's owner (`Actor` docs, slice 1 §9).
USER_ACTOR_TYPES = ("user", "mcp")


@dataclass(frozen=True)
class Assignment:
    entity: Entity
    entity_id: UUID
    label: str
    owner_id: UUID
    owner_name: str


@dataclass(frozen=True)
class Listed:
    entity: Entity
    entity_id: UUID
    label: str
    reason: Reason
    # The creator's display name when there is one to name (the generic and the inactive
    # cases), so the list tells Lorenzo whose account it was.
    creator_name: str | None = None


@dataclass
class BackfillPlan:
    assignments: list[Assignment] = field(default_factory=list)
    listed: list[Listed] = field(default_factory=list)
    # Live records that already had an owner and are not touched.
    already_owned: dict[Entity, int] = field(default_factory=lambda: {"customer": 0, "deal": 0})

    def assigned(self, entity: Entity) -> int:
        return sum(1 for item in self.assignments if item.entity == entity)

    def left(self, entity: Entity) -> int:
        return sum(1 for item in self.listed if item.entity == entity)


def _creators(session: Session, entity: Entity) -> dict[UUID, tuple[str, UUID | None]]:
    """`entity_id -> (actor_type, actor_id)` of each record's earliest `created` entry."""
    first = (
        select(
            Activity.entity_id,
            Activity.actor_type,
            Activity.actor_id,
            func.row_number()
            .over(
                partition_by=Activity.entity_id,
                order_by=(Activity.occurred_at, Activity.id),
            )
            .label("position"),
        )
        .where(Activity.entity_type == entity, Activity.kind == "created")
        .subquery()
    )
    rows = session.execute(
        select(first.c.entity_id, first.c.actor_type, first.c.actor_id).where(first.c.position == 1)
    ).all()
    return {row.entity_id: (row.actor_type, row.actor_id) for row in rows}


def plan_owner_backfill(session: Session, *, generic_emails: Collection[str] = ()) -> BackfillPlan:
    """What the backfill would do to the space `session` is open on. Reads only."""
    generic = {email.strip().lower() for email in generic_emails}
    users = {user.id: user for user in session.scalars(select(User)).all()}
    plan = BackfillPlan()

    def decide(
        entity: Entity,
        entity_id: UUID,
        label: str,
        creators: dict[UUID, tuple[str, UUID | None]],
    ) -> None:
        creation = creators.get(entity_id)
        if creation is None:
            plan.listed.append(Listed(entity, entity_id, label, "no_creation_entry"))
            return
        actor_type, actor_id = creation
        creator = users.get(actor_id) if actor_id is not None else None
        if actor_type not in USER_ACTOR_TYPES or creator is None:
            plan.listed.append(Listed(entity, entity_id, label, "no_user"))
        elif creator.email.lower() in generic:
            plan.listed.append(Listed(entity, entity_id, label, "generic_account", creator.nome))
        elif not creator.attivo:
            plan.listed.append(Listed(entity, entity_id, label, "inactive_creator", creator.nome))
        else:
            plan.assignments.append(Assignment(entity, entity_id, label, creator.id, creator.nome))

    customer_creators = _creators(session, "customer")
    for customer in session.scalars(
        select(Customer).where(Customer.deleted_at.is_(None)).order_by(Customer.created_at)
    ).all():
        if customer.owner_id is not None:
            plan.already_owned["customer"] += 1
        else:
            decide("customer", customer.id, customer.ragione_sociale, customer_creators)

    deal_creators = _creators(session, "deal")
    for deal in session.scalars(
        select(Deal).where(Deal.deleted_at.is_(None)).order_by(Deal.created_at)
    ).all():
        if deal.owner_id is not None:
            plan.already_owned["deal"] += 1
        else:
            decide("deal", deal.id, deal.nome, deal_creators)
    return plan


def apply_owner_backfill(session: Session, plan: BackfillPlan) -> int:
    """Writes `plan`'s assignments and commits once; returns how many records changed.

    Each row is locked and read again (`populate_existing`: the plan already loaded it, and
    a locked read alone would hand back that stale copy) and written only while its owner is
    still empty and the record still live, so a plan computed a moment ago cannot overwrite
    an owner somebody set, or touch a record somebody archived, in between."""
    activities = ActivityService(session)
    system = Actor.system()
    changed = 0
    for item in plan.assignments:
        record: Customer | Deal | None = (
            session.get(Customer, item.entity_id, with_for_update=True, populate_existing=True)
            if item.entity == "customer"
            else session.get(Deal, item.entity_id, with_for_update=True, populate_existing=True)
        )
        if record is None or record.owner_id is not None or record.deleted_at is not None:
            continue
        record.owner_id = item.owner_id
        activities.record(
            item.entity,
            item.entity_id,
            "updated",
            system,
            {"changed": ["owner_id"], "source": SOURCE},
        )
        changed += 1
    session.commit()
    return changed
