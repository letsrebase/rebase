from uuid import UUID

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.proposals.schemas import ProposalListQuery


class ProposalRepository:
    """A repository never commits (project rule): every method here either reads or
    flushes, and the surrounding `ProposalService` method is always the one
    transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, proposal_id: UUID, visible: ColumnElement[bool] | None = None) -> Proposal | None:
        """The proposal, among those `visible` lets through when a clause is given."""
        stmt = select(Proposal).where(Proposal.id == proposal_id)
        if visible is not None:
            stmt = stmt.where(visible)
        return self.session.execute(stmt).scalars().first()

    def get_for_update(
        self, proposal_id: UUID, visible: ColumnElement[bool] | None = None
    ) -> Proposal | None:
        """The row locked and overwritten from the database: a plain read would return an
        already-loaded copy with its old `status` (`populate_existing`)."""
        stmt = (
            select(Proposal)
            .where(Proposal.id == proposal_id)
            .with_for_update(of=Proposal)
            .execution_options(populate_existing=True)
        )
        if visible is not None:
            stmt = stmt.where(visible)
        return self.session.execute(stmt).scalars().first()

    def pending_for_inbox_item(self, inbox_item_id: UUID) -> Proposal | None:
        return (
            self.session.execute(
                select(Proposal).where(
                    Proposal.inbox_item_id == inbox_item_id, Proposal.status == "pending"
                )
            )
            .scalars()
            .first()
        )

    def add(self, proposal: Proposal) -> Proposal:
        self.session.add(proposal)
        self.session.flush()
        return proposal

    # `list` is defined LAST in this class on purpose: `def list(...)` rebinds `list`
    # in the class namespace, and Python 3.13 evaluates annotations eagerly, so a
    # later `-> list[...]` would raise `TypeError` at import time (the same
    # convention every other repository's own `list`/`list_for_contract` follows).
    def list(
        self, query: ProposalListQuery, visible: ColumnElement[bool] | None = None
    ) -> list[Proposal]:
        """Oldest first by default: a review queue is worked in the order proposals
        arrived, not the order they were last touched. `order="decision"` is the
        decided lists' order, newest decision first."""
        stmt = select(Proposal)
        if visible is not None:
            stmt = stmt.where(visible)
        if query.status:
            stmt = stmt.where(Proposal.status == query.status)
        if query.target_type:
            stmt = stmt.where(Proposal.target_type == query.target_type)
        if query.document_id:
            stmt = stmt.where(Proposal.document_id == query.document_id)
        if query.inbox_item_id:
            stmt = stmt.where(Proposal.inbox_item_id == query.inbox_item_id)
        if query.contract_id:
            stmt = stmt.where(Proposal.contract_id == query.contract_id)
        if query.order == "decision":
            stmt = stmt.order_by(
                Proposal.decided_at.desc().nulls_last(),
                Proposal.created_at.desc(),
                Proposal.id.desc(),
            )
        else:
            stmt = stmt.order_by(Proposal.created_at.asc(), Proposal.id.asc())
        stmt = stmt.limit(query.limit)
        return list(self.session.execute(stmt).scalars())
