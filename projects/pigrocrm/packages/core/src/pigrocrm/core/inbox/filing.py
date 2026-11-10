"""What a decision about an inbox item does to the proposals and the records around it.

Two things live here because more than one door needs them: `InboxService` (a person discards
an item, marks it a duplicate, sets its kind) and `PurchaseService` (a person registers a
purchase with an item as its justification). Neither commits: both run inside the caller's
transaction, so the item and the record it became change together or not at all.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict
from pigrocrm.core.inbox.access import ENTITY, visible_to
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.proposals.schemas import ProposalReject
from pigrocrm.core.proposals.service import ProposalService

WAITING = ("new", "proposed")
"""The states an item that nobody has decided on is in."""

_WITHDRAWN = "Il documento e' stato rivisto da una persona"
_FILED_AS_PURCHASE = "Registrato come acquisto"


def withdraw_proposals(session: Session, item: InboxItem, actor: Actor) -> None:
    """What a person says about an item makes a proposal waiting on it moot: it is
    rejected, as the person would have rejected it. An agent never decides a proposal
    (REB-806), so it is told to leave the item to a person instead."""
    pending = list(
        session.execute(
            select(Proposal.id).where(
                Proposal.inbox_item_id == item.id, Proposal.status == "pending"
            )
        )
        .scalars()
        .all()
    )
    if not pending:
        return
    if actor.type == "mcp":
        raise Conflict(
            ENTITY,
            "ha una proposta in attesa: la decide una persona in Proposte",
            item_id=str(item.id),
        )
    for proposal_id in pending:
        ProposalService(session).reject_without_commit(
            proposal_id, ProposalReject(reason=_WITHDRAWN), actor
        )
    session.refresh(item)


_MAX_CHAIN = 10
"""How many `duplicate_of_id` hops are followed: a longer chain is taken for a loop."""


def resolve_original(session: Session, inbox_item_id: UUID, actor: Actor) -> UUID:
    """The item that holds the file: a `duplicate` holds no bytes, so a purchase linking one
    is linked to the original it points at (the chain followed to its root, bounded).

    Only to an original the actor sees (`inbox.access`): when a hop is not visible, or the
    chain does not end, the id given is kept, so nothing about an item they cannot open is
    revealed. An item that is not a duplicate is answered as it is."""
    current = session.get(InboxItem, inbox_item_id)
    for _ in range(_MAX_CHAIN + 1):
        if current is None or current.state != "duplicate" or current.duplicate_of_id is None:
            break
        stmt = select(InboxItem).where(InboxItem.id == current.duplicate_of_id)
        clause = visible_to(actor)
        if clause is not None:
            stmt = stmt.where(clause)
        current = session.execute(stmt).scalars().first()
    else:
        return inbox_item_id
    if current is None:
        return inbox_item_id
    return current.id


def file_as_purchase(
    session: Session,
    inbox_item_id: UUID,
    purchase_id: UUID,
    actor: Actor,
    legal_entity_id: UUID | None = None,
) -> bool:
    """A purchase was registered with this item as its justification. Answers whether the item
    was filed by it.

    The item an unclassified file is (no legal entity) takes the purchase's legal entity, so the
    other members of that entity can open the purchase's file: an unclassified item is visible
    only to its uploader and an unscoped administrator (`inbox.access`). A purchase with no
    legal entity leaves the item as it is. This holds for whoever links, an agent included.

    Only a person's act files an item: an agent linking one only links it (accepting is never
    an agent's act, `Actor.require_person`). An item that is not waiting (already `filed`, a
    `duplicate`, `discarded`) or that the person does not see (`inbox.access`) is only linked.
    The bytes stay where they are: they are the purchase's file."""
    stmt = select(InboxItem).where(InboxItem.id == inbox_item_id)
    clause = visible_to(actor)
    if clause is not None:
        stmt = stmt.where(clause)
    item = (
        session.execute(stmt.with_for_update().execution_options(populate_existing=True))
        .scalars()
        .first()
    )
    if item is None:
        return False
    if item.legal_entity_id is None and legal_entity_id is not None:
        item.legal_entity_id = legal_entity_id
    if actor.type == "mcp" or item.state not in WAITING:
        return False
    withdraw_proposals(session, item, actor)
    item.state = "filed"
    item.reason = _FILED_AS_PURCHASE
    item.read_started_at = None
    ActivityService(session).record(
        ENTITY, item.id, "filed", actor, {"purchase_id": str(purchase_id)}
    )
    return True
