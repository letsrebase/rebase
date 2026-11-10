"""Who sees which proposal (design 2026-11-09, incoming documents inbox, § 3.5 and § 1).

A proposal's visibility is its source's. A proposal with an **owner** (a task read from a
personal mailbox) is the owner's alone, not even an administrator's. One read out of an inbox
item is visible to whoever sees the item (`inbox/access.py`: an unclassified item is an
unscoped administrator's and its uploader's, a classified one follows the entity scope).
Every other proposal, which reads from an archived document, is visible as it always was.

A proposal that carries pay (a `payslip`, staff design § 4, REB-1045) is listed, read and decided
only by whoever may read pay (`Actor.reads_pay`): an administrator, and an agent only with
`full_access`. Whoever else asks finds it is not there, which is what hides it from the review
queue, the timeline and the counts. The contract and work-unit types are unchanged.
"""

from sqlalchemy import ColumnElement, and_, exists, or_, select

from pigrocrm.core.actor import Actor
from pigrocrm.core.inbox import access as inbox_access
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.proposals.models import Proposal

PAY_TARGET_TYPES: tuple[str, ...] = ("payslip",)
"""The target types whose fields are pay."""


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
    clause = and_(
        owned, or_(Proposal.inbox_item_id.is_(None), exists(select(1).where(item_clause)))
    )
    if actor.reads_pay:
        return clause
    return and_(clause, Proposal.target_type.not_in(PAY_TARGET_TYPES))
