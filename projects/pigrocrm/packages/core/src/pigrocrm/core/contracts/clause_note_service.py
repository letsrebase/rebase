from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import ClauseNote, Contract
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.contracts.rules import ENTITY
from pigrocrm.core.contracts.schemas import ClauseNoteCreate, ClauseNoteRead, ClauseNoteUpdate
from pigrocrm.core.errors import NotFound
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes


class ClauseNoteService:
    """The readings of a contract's clauses (REB-762): written, corrected and listed, never
    deleted (see `ClauseNote`). Each write is on the contract's timeline, with the clause it
    is about and not the words, which can run to pages."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.contracts = ContractRepository(session)
        self.activities = ActivityService(session)

    def _contract(self, contract_id: UUID) -> Contract:
        contract = self.contracts.get(contract_id)
        if contract is None:
            raise NotFound(ENTITY, contract_id)
        return contract

    def create(self, contract_id: UUID, data: ClauseNoteCreate, actor: Actor) -> ClauseNoteRead:
        actor.require_write("create_clause_note")
        self._contract(contract_id)
        note = ClauseNote(**data.model_dump(), contract_id=contract_id)
        self.session.add(note)
        self.session.flush()
        self.activities.record(
            ENTITY,
            contract_id,
            "clause_note_added",
            actor,
            {"riferimento_clausola": note.riferimento_clausola},
        )
        self.session.commit()
        return ClauseNoteRead.model_validate(note)

    def update(
        self, contract_id: UUID, note_id: UUID, data: ClauseNoteUpdate, actor: Actor
    ) -> ClauseNoteRead:
        actor.require_write("update_clause_note")
        self._contract(contract_id)
        note = self.session.get(ClauseNote, note_id)
        if note is None or note.contract_id != contract_id:
            raise NotFound("clause_note", note_id)
        changes = supplied_changes(data)
        reject_cleared_columns("clause_note", ClauseNote, changes)
        if not changes:
            return ClauseNoteRead.model_validate(note)
        for key, value in changes.items():
            setattr(note, key, value)
        self.session.flush()
        self.activities.record(
            ENTITY,
            contract_id,
            "clause_note_updated",
            actor,
            {"riferimento_clausola": note.riferimento_clausola, "changed": sorted(changes)},
        )
        self.session.commit()
        return ClauseNoteRead.model_validate(note)

    def list_for_contract(self, contract_id: UUID, actor: Actor) -> list[ClauseNoteRead]:
        self._contract(contract_id)
        notes = self.session.scalars(
            select(ClauseNote)
            .where(ClauseNote.contract_id == contract_id)
            .order_by(ClauseNote.created_at, ClauseNote.id)
        )
        return [ClauseNoteRead.model_validate(note) for note in notes]
