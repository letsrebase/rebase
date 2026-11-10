"""The one writer of `proposals` -- and, on `accept`, the one writer of the
`contracts`/`rate_cards` pair or the `approvals`/`work_units` pair it produces, in the
same transaction as the proposal's own `status` flip. Spec §10: "agents propose, humans
confirm."

**Why this module, not `WorkUnitService.create`/`ApprovalService.create`, writes a
`'work_unit'` accept.** Those two commit on their own (see their own docstrings); calling
either from here would make "one transaction, never as two separate calls a caller could
interrupt between" (spec §10's own words) false the moment the second call failed after
the first had already committed. `_accept_work_unit` instead builds the `Approval` and
`WorkUnit` rows directly through their repositories -- both flush, neither commits -- so
`accept`'s own single `self.session.commit()` at the end is the only place either
becomes durable. `WorkUnitService.create` still validates the identical entry-state
rule (`test_mcp_surface_coverage.py`'s own exclusion for it says as much); the database
trigger `work_unit_enforce_state_machine` enforces it here exactly as it would there --
this method just never gives an agent a way to reach the raw write directly.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.rules import (
    check_payment_terms_together,
    check_rate_card_shape,
    check_renewal_notice,
)
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.inbox.access import visible_item
from pigrocrm.core.inbox.models import MAX_READ_ATTEMPTS, InboxItem
from pigrocrm.core.labels import PROPOSAL_STATUS, label
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.proposals.access import visible_to
from pigrocrm.core.proposals.models import INBOX_TARGET_TYPES, Proposal
from pigrocrm.core.proposals.repository import ProposalRepository
from pigrocrm.core.proposals.schemas import (
    ContractProposalFields,
    ProposalAccept,
    ProposalCreate,
    ProposalListQuery,
    ProposalPage,
    ProposalRead,
    ProposalReject,
    ReadDocumentFields,
    WorkUnitProposalFields,
)
from pigrocrm.core.work_units.models import Approval, WorkUnit
from pigrocrm.core.work_units.service import set_transition_context

ENTITY = "proposal"

# What a proposal read out of the inbox is waiting for, by target type: the module that files
# it. None of the three exists yet (design § 3.5, last paragraph: «Before C3 and C4 ship, a
# supplier invoice or a statement is classified and waits in the inbox with its parsed fields
# shown; nothing is lost while the modules that file them do not exist»), so accepting one is
# refused with the sentence that says what is missing, and the proposal stays pending.
_NO_FILING_MODULE: dict[str, str] = {
    "supplier_invoice": "il registro delle fatture di acquisto non c'e' ancora",
    "receipt": "non c'e' ancora un modulo che archivi le ricevute",
    "bank_statement": "il modulo che legge gli estratti conto non c'e' ancora",
}
_PROPOSAL_ITEM_REASON = "Proposta in attesa di una persona"
_REJECTED_ITEM_REASON = "Proposta rifiutata: l'elemento attende di essere riletto"


def _first_error(exc: PydanticValidationError) -> str:
    """The first pydantic error, rendered `"loc.path: message"` -- enough for a human
    reviewing the proposal to find the field, without dumping the whole error list a
    caller-composed dict was never going to display anyway."""
    errors = exc.errors()
    if not errors:
        return str(exc)
    first = errors[0]
    loc = ".".join(str(part) for part in first["loc"])
    return f"{loc}: {first['msg']}" if loc else str(first["msg"])


class ProposalService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ProposalRepository(session)
        self.documents = DocumentRepository(session)
        self.activities = ActivityService(session)

    # ---- shape parsing (campi_proposti/campi_accettati are plain JSONB) -----------

    def _parse_contract_fields(self, fields: dict[str, Any]) -> ContractProposalFields:
        try:
            return ContractProposalFields.model_validate(fields)
        except PydanticValidationError as exc:
            raise ValidationFailed(
                ENTITY,
                "proposed_fields",
                _first_error(exc),
                expected="{'contract': {...ContractCreate}, 'rate_card': {...RateCardCreate}}",
            ) from exc

    def _parse_work_unit_fields(self, fields: dict[str, Any]) -> WorkUnitProposalFields:
        try:
            return WorkUnitProposalFields.model_validate(fields)
        except PydanticValidationError as exc:
            raise ValidationFailed(
                ENTITY,
                "proposed_fields",
                _first_error(exc),
                expected="{'contract_id', 'date', 'quantity', 'description', 'approval'}",
            ) from exc

    def _parse_read_fields(self, target_type: str, fields: dict[str, Any]) -> ReadDocumentFields:
        try:
            read = ReadDocumentFields.model_validate(fields)
        except PydanticValidationError as exc:
            raise ValidationFailed(
                ENTITY,
                "proposed_fields",
                _first_error(exc),
                expected="i campi letti dal documento: emittente, numero, date, importi",
            ) from exc
        needed = {
            "supplier_invoice": ("issuer_name", "issue_date", "total"),
            "receipt": ("issue_date", "total"),
            "bank_statement": ("period_start", "period_end"),
        }[target_type]
        missing = [name for name in needed if getattr(read, name) is None]
        if missing:
            raise ValidationFailed(
                ENTITY,
                "proposed_fields",
                f"mancano i campi {', '.join(missing)}: senza, una persona non ha nulla da "
                "decidere",
                expected=f"almeno {', '.join(needed)}",
            )
        return read

    # ---- create (the one proposal-creation step, reachable from any door) ---------

    def create(self, data: ProposalCreate, actor: Actor) -> ProposalRead:
        """Reads a `documents` row and a `target_type`, never a source: whether the
        document arrived through `import_drive_file`, a direct upload, or a mail
        attachment archived the same way, this is the single call every door
        converges on (spec §10's own "none of the three needs its own
        proposal-creation code path")."""
        actor.require_write("create_proposal")
        # One dispatch per target type, and no `else` that reads a kind this method does not
        # know as a day (REB-972): the source it reads from, then what it proposes.
        item: InboxItem | None = None
        if data.target_type == "contract":
            self._require_document_source(data)
            if data.contract_id is not None:
                raise ValidationFailed(
                    ENTITY,
                    "contract_id",
                    "una proposta di tipo 'contract' non punta a un contratto "
                    "esistente: e' la prima bozza, non un rinnovo",
                    expected="assente quando target_type = 'contract'",
                )
            fields = self._parse_contract_fields(data.proposed_fields)
            if self.session.get(Organization, fields.contract.customer_id) is None:
                raise NotFound("organization", fields.contract.customer_id)
        elif data.target_type == "work_unit":
            self._require_document_source(data)
            if data.contract_id is None:
                raise ValidationFailed(
                    ENTITY,
                    "contract_id",
                    "obbligatorio per una proposta di tipo 'work_unit'",
                    expected="un contract_id esistente",
                )
            work_unit_fields = self._parse_work_unit_fields(data.proposed_fields)
            if work_unit_fields.contract_id != data.contract_id:
                raise ValidationFailed(
                    ENTITY,
                    "proposed_fields.contract_id",
                    "non corrisponde al contract_id della proposta",
                    expected=str(data.contract_id),
                )
            if self.session.get(Contract, data.contract_id) is None:
                raise NotFound("contract", data.contract_id)
        elif data.target_type in INBOX_TARGET_TYPES:
            item = self._inbox_source(data, actor)
            if data.contract_id is not None:
                raise ValidationFailed(
                    ENTITY,
                    "contract_id",
                    f"una proposta di tipo '{data.target_type}' non punta a un contratto",
                    expected="assente",
                )
            self._parse_read_fields(data.target_type, data.proposed_fields)
        else:
            raise ValidationFailed(
                ENTITY,
                "target_type",
                f"tipo di proposta sconosciuto: '{data.target_type}'",
                expected="contract, work_unit, supplier_invoice, receipt o bank_statement",
            )

        # Two proposals racing on one inbox item meet `uq_proposals_pending_inbox_item`: the
        # second is a refusal in words, not a 500.
        with savepoint(
            self.session,
            lambda exc: (
                Conflict(
                    ENTITY,
                    "per questo elemento c'e' gia' una proposta in attesa",
                    inbox_item_id=str(data.inbox_item_id),
                )
                if "uq_proposals_pending_inbox_item" in str(exc)
                else exc
            ),
        ):
            proposal = self.repo.add(
                Proposal(
                    document_id=data.document_id,
                    inbox_item_id=data.inbox_item_id,
                    contract_id=data.contract_id,
                    target_type=data.target_type,
                    proposed_fields=data.proposed_fields,
                    excerpt=data.excerpt,
                    excerpt_type=data.excerpt_type,
                    confidence=data.confidence,
                    confidence_reason=data.confidence_reason,
                )
            )
        if item is not None:
            item.state = "proposed"
            item.kind = data.target_type
            item.reason = _PROPOSAL_ITEM_REASON
        self.activities.record(
            ENTITY,
            proposal.id,
            "created",
            actor,
            {
                "target_type": proposal.target_type,
                "document_id": str(proposal.document_id) if proposal.document_id else None,
                "inbox_item_id": str(proposal.inbox_item_id) if proposal.inbox_item_id else None,
            },
        )
        self.session.commit()
        return ProposalRead.model_validate(proposal)

    def _require_document_source(self, data: ProposalCreate) -> None:
        if data.inbox_item_id is not None:
            raise ValidationFailed(
                ENTITY,
                "inbox_item_id",
                f"una proposta di tipo '{data.target_type}' legge da un documento archiviato, "
                "non da un elemento di «In arrivo»",
                expected="document_id",
            )
        if data.document_id is None:
            raise ValidationFailed(
                ENTITY,
                "document_id",
                f"obbligatorio per una proposta di tipo '{data.target_type}'",
                expected="il document_id del documento da cui e' letta",
            )
        if self.documents.get(data.document_id) is None:
            raise NotFound("document", data.document_id)

    def _inbox_source(self, data: ProposalCreate, actor: Actor) -> InboxItem:
        """The inbox item a proposal of a kind read out of the inbox reads from: one the actor
        can see, still waiting for somebody to say what it is, and with no proposal pending."""
        if data.document_id is not None:
            raise ValidationFailed(
                ENTITY,
                "document_id",
                f"una proposta di tipo '{data.target_type}' legge da un elemento di «In arrivo», "
                "non da un documento archiviato",
                expected="inbox_item_id",
            )
        if data.inbox_item_id is None:
            raise ValidationFailed(
                ENTITY,
                "inbox_item_id",
                f"obbligatorio per una proposta di tipo '{data.target_type}'",
                expected="l'id dell'elemento di «In arrivo» da cui e' letta",
            )
        item = visible_item(self.session, data.inbox_item_id, actor)
        if item.state != "new" or item.file_type is None:
            raise Conflict(
                ENTITY,
                f"l'elemento di «In arrivo» e' '{item.state}': si propone solo per un elemento "
                "nuovo che conserva il suo file",
                inbox_item_id=str(item.id),
            )
        if item.kind is not None:
            # A certain file (a FatturaPA, a statement file) is already what it is, with its
            # parsed fields: a proposal is for what nobody has said what it is.
            raise Conflict(
                ENTITY,
                f"l'elemento e' gia' stato riconosciuto come '{item.kind}': si propone solo per "
                "un elemento che nessuno ha ancora riconosciuto",
                inbox_item_id=str(item.id),
            )
        if self.repo.pending_for_inbox_item(item.id) is not None:
            raise Conflict(
                ENTITY,
                "per questo elemento c'e' gia' una proposta in attesa",
                inbox_item_id=str(item.id),
            )
        return item

    def get(self, proposal_id: UUID, actor: Actor) -> ProposalRead:
        """A proposal the actor may not see is not there for them (`proposals/access.py`)."""
        proposal = self.repo.get(proposal_id, visible_to(actor))
        if proposal is None:
            raise NotFound(ENTITY, proposal_id)
        return ProposalRead.model_validate(proposal)

    def list(self, query: ProposalListQuery, actor: Actor) -> ProposalPage:
        return ProposalPage(
            items=[ProposalRead.model_validate(p) for p in self.repo.list(query, visible_to(actor))]
        )

    def _require_pending(self, proposal_id: UUID, actor: Actor) -> Proposal:
        """The proposal, read under a row lock and overwritten from the database
        (`populate_existing`), so two concurrent decisions queue and the second finds the
        first one's `status` instead of acting on a copy loaded before it."""
        proposal = self.repo.get_for_update(proposal_id, visible_to(actor))
        if proposal is None:
            raise NotFound(ENTITY, proposal_id)
        if proposal.status != "pending":
            raise Conflict(
                ENTITY,
                f"già decisa ({label(PROPOSAL_STATUS, proposal.status)}): una proposta si "
                f"decide una sola volta",
                proposal_id=str(proposal_id),
            )
        return proposal

    def _decider(self, actor: Actor) -> str:
        """Who decided, read from the credential and never from a request (REB-806): the
        member's name as it is now, or the actor's kind for one without a user row. The
        timeline row carries the user id as well, so a later rename does not orphan it."""
        user = self.session.get(User, actor.id) if actor.id is not None else None
        return user.name or user.email if user is not None else actor.type

    def reject(self, proposal_id: UUID, data: ProposalReject, actor: Actor) -> ProposalRead:
        read = self.reject_without_commit(proposal_id, data, actor)
        self.session.commit()
        return read

    def reject_without_commit(
        self, proposal_id: UUID, data: ProposalReject, actor: Actor
    ) -> ProposalRead:
        """`reject` for a caller that goes on to write in the same transaction (the inbox acts
        that make a waiting proposal moot): it flushes and never commits."""
        actor.require_person("reject_proposal")
        actor.require_write("reject_proposal")
        proposal = self._require_pending(proposal_id, actor)
        decider = self._decider(actor)
        proposal.status = "rejected"
        proposal.decided_by = decider
        proposal.decided_at = datetime.now(UTC)
        if proposal.inbox_item_id is not None:
            # The item waits again, unclassified: a person or an agent may read it anew.
            item = self.session.get(InboxItem, proposal.inbox_item_id)
            if item is not None and item.state == "proposed":
                item.state = "new"
                item.kind = None
                item.reason = _REJECTED_ITEM_REASON
                # Read again by a person or an agent, not by another paid run of the model.
                item.read_attempts = MAX_READ_ATTEMPTS
        self.activities.record(
            ENTITY,
            proposal.id,
            "rejected",
            actor,
            {"decided_by": decider, "reason": data.reason},
        )
        self.session.flush()
        return ProposalRead.model_validate(proposal)

    def accept(self, proposal_id: UUID, data: ProposalAccept, actor: Actor) -> ProposalRead:
        """One transaction, start to finish: the target row (`Contract`+`RateCard`,
        or `Approval`+`WorkUnit`) is built, the proposal's own `status`/`accepted_fields`
        /`result_id`/`decided_by`/`decided_at` are set, and exactly one commit closes
        all of it. A `'work_unit'` accept never leaves a day at `'proposed'` because
        there is no separate call in between that could be interrupted."""
        actor.require_person("accept_proposal")
        actor.require_write("accept_proposal")
        proposal = self._require_pending(proposal_id, actor)
        decider = self._decider(actor)
        accepted = (
            data.accepted_fields
            if data.accepted_fields is not None
            else dict(proposal.proposed_fields)
        )
        # One branch per target type; a type without one is an error, never a day.
        if proposal.target_type == "contract":
            result_id = self._accept_contract(proposal, accepted, actor)
        elif proposal.target_type == "work_unit":
            result_id = self._accept_work_unit(proposal, accepted, actor)
        elif proposal.target_type in INBOX_TARGET_TYPES:
            result_id = self._accept_inbox_kind(proposal, accepted)
        else:
            raise Conflict(
                ENTITY,
                f"non so come accettare una proposta di tipo '{proposal.target_type}'",
                proposal_id=str(proposal.id),
            )

        proposal.accepted_fields = accepted
        proposal.status = "accepted"
        proposal.result_id = result_id
        proposal.decided_by = decider
        proposal.decided_at = datetime.now(UTC)
        self.activities.record(
            ENTITY,
            proposal.id,
            "accepted",
            actor,
            {"decided_by": decider, "result_id": str(result_id)},
        )
        self.session.commit()
        return ProposalRead.model_validate(proposal)

    def _accept_inbox_kind(self, proposal: Proposal, raw: dict[str, Any]) -> UUID:
        """A supplier invoice, a receipt or a statement read out of the inbox is filed by the
        module that owns it (design § 3.5): none exists yet, so the corrected fields are
        checked and the accept is refused whole, with the proposal still pending and the item
        still waiting (nothing is lost)."""
        self._parse_read_fields(proposal.target_type, raw)
        raise Conflict(
            ENTITY,
            f"{_NO_FILING_MODULE[proposal.target_type]}: la proposta resta in attesa e "
            "l'elemento in «In arrivo» con i campi letti",
            proposal_id=str(proposal.id),
            target_type=proposal.target_type,
        )

    def _accept_contract(self, proposal: Proposal, raw: dict[str, Any], actor: Actor) -> UUID:
        """Creates the `contracts` row (plus its first `rate_cards` row) and
        re-points the originating document's ownership at it -- spec §10, §11's own
        "widen at accept time, not at archive time" ordering."""
        fields = self._parse_contract_fields(raw)
        customer = self.session.get(Organization, fields.contract.customer_id)
        if customer is None:
            raise NotFound("organization", fields.contract.customer_id)

        contract_payload = fields.contract.model_dump()
        check_payment_terms_together(contract_payload)
        check_renewal_notice(contract_payload)
        rate_card_payload = fields.rate_card.model_dump()
        check_rate_card_shape(rate_card_payload)
        # The contract and its first card are one SAVEPOINT: the exclusion constraint
        # refuses an overlapping card, and then no contract is left behind either, while
        # whatever else the caller has pending stays.
        with savepoint(
            self.session,
            lambda exc: Conflict(
                "rate_card",
                "il periodo di validità si sovrappone a un'altra scheda tariffaria "
                "di questo contratto",
            ),
        ):
            # The customer's legal entity at acceptance, as `ContractService.create` copies it.
            contract = Contract(
                **contract_payload,
                legal_entity_id=LegalEntityService(self.session).inherited(
                    customer.legal_entity_id, "contract"
                ),
            )
            self.session.add(contract)
            self.session.flush()
            self.activities.record(
                "contract",
                contract.id,
                "created",
                actor,
                {"title": contract.title, "proposal_id": str(proposal.id)},
            )
            rate_card_payload["contract_id"] = contract.id
            rate_card = RateCard(**rate_card_payload)
            self.session.add(rate_card)
        self.activities.record(
            "contract",
            contract.id,
            "rate_card_added",
            actor,
            {"type": rate_card.type, "valid_from": str(rate_card.valid_from)},
        )

        # A contract reads from a document (`ck_proposals_source`).
        assert proposal.document_id is not None
        document = self.documents.get(proposal.document_id)
        if document is None:
            raise NotFound("document", proposal.document_id)
        self.documents.reassign_to_contract(document, contract.id)
        self.activities.record(
            "document",
            document.id,
            "updated",
            actor,
            {"changed": ["contract_id"], "proposal_id": str(proposal.id)},
        )
        return contract.id

    def _accept_work_unit(self, proposal: Proposal, raw: dict[str, Any], actor: Actor) -> UUID:
        """Creates the `approval` row and the `work_unit` at `'approved'`, both in
        one transaction -- never leaving a day at `'proposed'` (spec §10).

        Re-checks `fields.contract_id` against `proposal.contract_id` even though
        `create()` already checked it once: `raw` here may be a human's edited
        `accepted_fields`, not the original `proposed_fields` `create()` validated,
        and a reviewer who approved a proposal against contract A must not have
        their accept silently redirected to contract B by an edited payload."""
        fields = self._parse_work_unit_fields(raw)
        if fields.contract_id != proposal.contract_id:
            raise ValidationFailed(
                ENTITY,
                "accepted_fields.contract_id",
                "non corrisponde al contract_id della proposta",
                expected=str(proposal.contract_id),
            )
        if self.session.get(Contract, fields.contract_id) is None:
            raise NotFound("contract", fields.contract_id)

        # The approval and the day are one SAVEPOINT: the live-day unique index refuses a
        # second day on the date, and then neither row survives (no orphan approval), while
        # whatever else the caller has pending stays.
        with savepoint(
            self.session,
            lambda exc: Conflict(
                "work_unit",
                "esiste già una giornata non conclusa per questo contratto in questa data",
                contract_id=str(fields.contract_id),
                date=str(fields.date),
            ),
        ):
            approval = Approval(
                contract_id=fields.contract_id,
                channel=fields.approval.channel,
                sender=fields.approval.sender,
                received_at=fields.approval.received_at,
                message_id=fields.approval.message_id,
                document_id=proposal.document_id,
                excerpt=proposal.excerpt,
                origin={"kind": "agent", "proposal_id": str(proposal.id)},
            )
            self.session.add(approval)
            self.session.flush()

            set_transition_context(self.session, actor, f"proposta {proposal.id} accettata")
            work_unit = WorkUnit(
                contract_id=fields.contract_id,
                date=fields.date,
                quantity=fields.quantity,
                description=fields.description,
                status="approved",
                approval_id=approval.id,
            )
            self.session.add(work_unit)
        self.activities.record(
            "contract",
            fields.contract_id,
            "day_approved",
            actor,
            {"date": str(fields.date), "proposal_id": str(proposal.id)},
        )
        return work_unit.id


__all__ = ["ProposalService"]
