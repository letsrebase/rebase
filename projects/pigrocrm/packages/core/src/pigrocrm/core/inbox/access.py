"""Who sees which item (design § 1, Visibility).

Before an item has a legal entity it is visible to the administrators without an entity scope
and to whoever uploaded it. After classification the usual entity scope applies. The entity
scope itself is Postgres's (`legal_entity_scope` on `inbox_items`, migration 0074); the rule
that an unclassified item is nobody else's is a role, which a row-level policy cannot read,
so it is this clause, added to every read.
"""

from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import NotFound
from pigrocrm.core.inbox.models import InboxItem

ENTITY = "inbox_item"


def visible_to(actor: Actor) -> ColumnElement[bool] | None:
    """The clause every read of items carries, or `None` for an actor who sees every row
    the policy lets through (an administrator with no entity scope, a system actor)."""
    if actor.can_administer and not actor.scoped:
        return None
    own = InboxItem.uploaded_by_id == actor.id if actor.id is not None else InboxItem.id.is_(None)
    return or_(InboxItem.legal_entity_id.is_not(None), own)


def visible_item(session: Session, item_id: UUID, actor: Actor) -> InboxItem:
    """The item, or `NotFound`: an item the actor may not see is not there for them."""
    stmt = select(InboxItem).where(InboxItem.id == item_id)
    clause = visible_to(actor)
    if clause is not None:
        stmt = stmt.where(clause)
    item = session.execute(stmt).scalars().first()
    if item is None:
        raise NotFound(ENTITY, item_id)
    return item
