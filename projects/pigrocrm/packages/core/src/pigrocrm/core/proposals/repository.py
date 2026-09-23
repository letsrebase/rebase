from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.proposals.schemas import ProposalListQuery


class ProposalRepository:
    """A repository never commits (project rule): every method here either reads or
    flushes, and the surrounding `ProposalService` method is always the one
    transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, proposal_id: UUID) -> Proposal | None:
        return self.session.get(Proposal, proposal_id)

    def add(self, proposal: Proposal) -> Proposal:
        self.session.add(proposal)
        self.session.flush()
        return proposal

    # `list` is defined LAST in this class on purpose: `def list(...)` rebinds `list`
    # in the class namespace, and Python 3.13 evaluates annotations eagerly, so a
    # later `-> list[...]` would raise `TypeError` at import time (the same
    # convention every other repository's own `list`/`list_for_contract` follows).
    def list(self, query: ProposalListQuery) -> list[Proposal]:
        """Oldest first: a review queue is worked in the order proposals arrived,
        not the order they were last touched."""
        stmt = select(Proposal)
        if query.stato:
            stmt = stmt.where(Proposal.stato == query.stato)
        if query.target_type:
            stmt = stmt.where(Proposal.target_type == query.target_type)
        if query.document_id:
            stmt = stmt.where(Proposal.document_id == query.document_id)
        if query.contract_id:
            stmt = stmt.where(Proposal.contract_id == query.contract_id)
        stmt = stmt.order_by(Proposal.created_at.asc(), Proposal.id.asc()).limit(query.limit)
        return list(self.session.execute(stmt).scalars())
