"""Who sees which proposal (design 2026-11-09, incoming documents inbox, § 3.5 and § 1).

A proposal's visibility is its source's. A proposal with an **owner** (a task read from a
personal mailbox) is the owner's alone, not even an administrator's. One read out of an inbox
item is visible to whoever sees the item (`inbox/access.py`: an unclassified item is an
unscoped administrator's and its uploader's, a classified one follows the entity scope).
Every other proposal, which reads from an archived document, is visible as it always was.
"""

from sqlalchemy import ColumnElement, and_, exists, or_, select

from pigrocrm.core.actor import Actor
from pigrocrm.core.inbox import access as inbox_access
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.proposals.models import Proposal


def visible_to(actor: Actor) -> ColumnElement[bool]:
    """The clause every read of proposals carries."""
    owned = (
        Proposal.owner_user_id.is_(None)
        if actor.id is None
        else or_(Proposal.owner_user_id.is_(None), Proposal.owner_user_id == actor.id)
    )
    item_scope = inbox_access.visible_to(actor)
    item_clause = InboxItem.id == Proposal.inbox_item_id
    if item_scope is not None:
        item_clause = and_(item_clause, item_scope)
    return and_(owned, or_(Proposal.inbox_item_id.is_(None), exists(select(1).where(item_clause))))
