"""Reading the inbox: the list and one item, with no bytes (design § 6: bytes never travel over
MCP, and a read answers metadata only). Writing is `uploads.InboxUploads`, reading what arrived
is `classifier.InboxClassifier`."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.inbox.access import ENTITY, visible_item, visible_to
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.inbox.repository import InboxRepository
from pigrocrm.core.inbox.schemas import (
    InboxDiscard,
    InboxDuplicate,
    InboxItemDetail,
    InboxItemPage,
    InboxItemRead,
    InboxKindSet,
    InboxListQuery,
)
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.proposals.schemas import ProposalReject
from pigrocrm.core.proposals.service import ProposalService

DISCARD_ACTION = "discard_inbox_item"
DUPLICATE_ACTION = "mark_inbox_item_duplicate"
KIND_ACTION = "set_inbox_item_kind"

_WAITING = ("new", "proposed")
_STATEMENT_FILES = ("csv", "xls", "xlsx", "camt053")
_CERTAIN_KIND = "il tipo l'ha letto il file stesso, con dati certi: non si cambia"
_WITHDRAWN = "Il documento e' stato rivisto da una persona"


_STATE_WORDS = {
    "new": "in attesa",
    "proposed": "con una proposta",
    "filed": "registrato",
    "duplicate": "un doppione",
    "discarded": "scartato",
}


def _by(actor: Actor) -> str:
    return "dal tuo agente" if actor.type == "mcp" else "da una persona"


def _text(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def attach_proposals(session: Session, reads: Sequence[InboxItemRead]) -> list[InboxItemRead]:
    """Each item of `reads` with the proposal waiting for a person on it, when it has one (an
    item in `proposed` links to its proposal, design § 3.5). The item was read by somebody who
    may see it, and so is its proposal."""
    ids = [read.id for read in reads if read.state == "proposed"]
    pending: dict[UUID, UUID] = {}
    if ids:
        rows = session.execute(
            select(Proposal.inbox_item_id, Proposal.id).where(
                Proposal.inbox_item_id.in_(ids), Proposal.status == "pending"
            )
        )
        pending = {item_id: proposal_id for item_id, proposal_id in rows if item_id is not None}
    return [read.model_copy(update={"proposal_id": pending.get(read.id)}) for read in reads]


class InboxService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = InboxRepository(session)

    def get(self, item_id: UUID, actor: Actor) -> InboxItemDetail:
        item = visible_item(self.session, item_id, actor)
        read = InboxItemDetail.model_validate(item)
        return read.model_copy(
            update={"proposal_id": attach_proposals(self.session, [read])[0].proposal_id}
        )

    # ---- what a person (or an agent) says about an item (design § 6, «classify») ----------

    def _locked(self, item_id: UUID, actor: Actor) -> InboxItem:
        """The item, read under a row lock and overwritten from the database, so two people
        deciding the same item queue and the second finds the first one's state."""
        stmt = select(InboxItem).where(InboxItem.id == item_id)
        clause = visible_to(actor)
        if clause is not None:
            stmt = stmt.where(clause)
        item = (
            self.session.execute(stmt.with_for_update().execution_options(populate_existing=True))
            .scalars()
            .first()
        )
        if item is None:
            raise NotFound(ENTITY, item_id)
        return item

    def _reads(self, item: InboxItem) -> InboxItemRead:
        self.session.refresh(item)
        return attach_proposals(self.session, [InboxItemRead.model_validate(item)])[0]

    def _withdraw_proposals(self, item: InboxItem, actor: Actor) -> None:
        """What a person says about an item makes a proposal waiting on it moot: it is
        rejected, as the person would have rejected it. An agent never decides a proposal
        (REB-806), so it is told to leave the item to a person instead."""
        pending = list(
            self.session.execute(
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
            ProposalService(self.session).reject_without_commit(
                proposal_id, ProposalReject(reason=_WITHDRAWN), actor
            )
        self.session.refresh(item)

    def _require_waiting(self, item: InboxItem, what: str) -> None:
        if item.state not in _WAITING:
            raise Conflict(
                ENTITY,
                f"{what}: l'elemento e' gia' {_STATE_WORDS[item.state]}",
                item_id=str(item.id),
                state=item.state,
            )

    def discard(self, item_id: UUID, data: InboxDiscard, actor: Actor) -> InboxItemRead:
        """Puts an item that waits aside, with the reason. The item stays, readable, with its
        bytes: a discarded file is never deleted. Idempotent for an item already discarded."""
        actor.require_write(DISCARD_ACTION)
        item = self._locked(item_id, actor)
        if item.state == "discarded":
            return self._reads(item)
        self._require_waiting(item, "non si scarta")
        self._withdraw_proposals(item, actor)
        item.state = "discarded"
        item.reason = data.reason
        item.read_started_at = None
        ActivityService(self.session).record(
            ENTITY, item.id, "discarded", actor, {"reason": data.reason}
        )
        self.session.commit()
        return self._reads(item)

    def mark_duplicate(self, item_id: UUID, data: InboxDuplicate, actor: Actor) -> InboxItemRead:
        """Says an item that waits is a copy of another one the caller sees. The copies the
        item itself had now point at the original. Idempotent for the same original."""
        actor.require_write(DUPLICATE_ACTION)
        # The writers of originals queue on one lock (uploads too), so two copies marked at
        # once cannot point at each other and an upload never finds a copy as its original.
        self.repo.lock_for_dedup()
        item = self._locked(item_id, actor)
        if item.state == "duplicate" and item.duplicate_of_id == data.original_id:
            return self._reads(item)
        if item.id == data.original_id:
            raise ValidationFailed(
                ENTITY, "original_id", "un file non puo' essere il doppione di se' stesso"
            )
        original = visible_item(self.session, data.original_id, actor)
        if original.state == "duplicate" and original.duplicate_of_id is not None:
            raise ValidationFailed(
                ENTITY,
                "original_id",
                "quel file e' a sua volta un doppione: indica l'originale",
                expected=str(original.duplicate_of_id),
            )
        if item.state == "duplicate":
            raise Conflict(
                ENTITY,
                "e' gia' il doppione di un altro file",
                item_id=str(item.id),
                duplicate_of_id=str(item.duplicate_of_id),
            )
        self._require_waiting(item, "non si segna come doppione")
        self._withdraw_proposals(item, actor)
        item.state = "duplicate"
        item.duplicate_of_id = original.id
        item.reason = f"Doppione di {original.original_name}, indicato {_by(actor)}"
        item.read_started_at = None
        self.session.execute(
            update(InboxItem)
            .where(InboxItem.duplicate_of_id == item.id)
            .values(duplicate_of_id=original.id)
        )
        ActivityService(self.session).record(
            ENTITY, item.id, "marked_duplicate", actor, {"original_id": str(original.id)}
        )
        self.session.commit()
        return self._reads(item)

    def set_kind(self, item_id: UUID, data: InboxKindSet, actor: Actor) -> InboxItemRead:
        """Says what an item that waits is, and optionally for which legal entity. Nothing is
        written as a record: the item waits for the module that files that kind. A kind a
        FatturaPA decided by itself is certain data and does not change; its legal entity
        may be chosen. Idempotent."""
        actor.require_write(KIND_ACTION)
        item = self._locked(item_id, actor)
        self._require_waiting(item, "non si cambia il tipo")
        if data.kind == "sales_invoice" and item.file_type not in ("xml", "p7m"):
            raise ValidationFailed(
                ENTITY,
                "kind",
                "una fattura emessa si registra da un file XML o .p7m",
            )
        legal_entity_id = item.legal_entity_id
        if data.legal_entity_id is not None:
            entity = LegalEntityRepository(self.session).get(data.legal_entity_id)
            if entity is None or not entity.active:
                raise ValidationFailed(
                    ENTITY, "legal_entity_id", "l'azienda non esiste o non e' tra le tue"
                )
            legal_entity_id = data.legal_entity_id
        kind_changes = data.kind is not None and item.kind != data.kind
        certain = item.parsed_fields is not None or item.file_type in _STATEMENT_FILES
        if kind_changes and item.kind is not None and certain:
            raise Conflict(ENTITY, _CERTAIN_KIND, item_id=str(item.id), kind=item.kind)
        if not kind_changes and legal_entity_id == item.legal_entity_id:
            return self._reads(item)
        previous = {"kind": item.kind, "legal_entity_id": _text(item.legal_entity_id)}
        if kind_changes:
            self._withdraw_proposals(item, actor)
            item.kind = data.kind
            item.state = "new"
            item.reason = f"Tipo indicato {_by(actor)}"
        item.legal_entity_id = legal_entity_id
        ActivityService(self.session).record(
            ENTITY,
            item.id,
            "kind_set",
            actor,
            {
                "kind": item.kind,
                "legal_entity_id": _text(legal_entity_id),
                "previous": previous,
            },
        )
        self.session.commit()
        return self._reads(item)

    # `list` stays the last method in this class, as in the repository.
    def list(self, query: InboxListQuery, actor: Actor) -> InboxItemPage:
        rows = self.repo.list(query, visible_to(actor))
        items = rows[: query.limit]
        return InboxItemPage(
            items=attach_proposals(self.session, [InboxItemRead.model_validate(i) for i in items]),
            next_cursor=items[-1].id if len(rows) > query.limit and items else None,
        )
