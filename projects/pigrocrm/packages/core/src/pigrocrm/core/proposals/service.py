"""The one writer of `proposals` -- and, on `accept`, the one writer of the
`contracts`/`rate_cards` pair or the `approvals`/`work_units` pair it produces, in the
same transaction as the proposal's own `stato` flip. Spec §10: "agents propose, humans
confirm."

**Why this module, not `WorkUnitService.create`/`ApprovalService.create`, writes a
`'giornata'` accept.** Those two commit on their own (see their own docstrings); calling
either from here would make "one transaction, never as two separate calls a caller could
interrupt between" (spec §10's own words) false the moment the second call failed after
the first had already committed. `_accept_giornata` instead builds the `Approval` and
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.service import (
    check_payment_terms_together,
    check_rate_card_shape,
    check_renewal_notice,
)
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.proposals.repository import ProposalRepository
from pigrocrm.core.proposals.schemas import (
    ContrattoProposalFields,
    GiornataProposalFields,
    ProposalAccept,
    ProposalCreate,
    ProposalListQuery,
    ProposalPage,
    ProposalRead,
    ProposalReject,
)
from pigrocrm.core.work_units.models import Approval, WorkUnit
from pigrocrm.core.work_units.service import set_transition_context

ENTITY = "proposal"


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

    def _parse_contratto_fields(self, campi: dict[str, Any]) -> ContrattoProposalFields:
        try:
            return ContrattoProposalFields.model_validate(campi)
        except PydanticValidationError as exc:
            raise ValidationFailed(
                ENTITY,
                "campi_proposti",
                _first_error(exc),
                expected="{'contract': {...ContractCreate}, 'rate_card': {...RateCardCreate}}",
            ) from exc

    def _parse_giornata_fields(self, campi: dict[str, Any]) -> GiornataProposalFields:
        try:
            return GiornataProposalFields.model_validate(campi)
        except PydanticValidationError as exc:
            raise ValidationFailed(
                ENTITY,
                "campi_proposti",
                _first_error(exc),
                expected="{'contract_id', 'data', 'quantita', 'descrizione', 'approvazione'}",
            ) from exc

    # ---- create (the one proposal-creation step, reachable from any door) ---------

    def create(self, data: ProposalCreate, actor: Actor) -> ProposalRead:
        """Reads a `documents` row and a `target_type`, never a source: whether the
        document arrived through `import_drive_file`, a direct upload, or a mail
        attachment archived the same way, this is the single call every door
        converges on (spec §10's own "none of the three needs its own
        proposal-creation code path")."""
        actor.require_write("create_proposal")
        document = self.documents.get(data.document_id)
        if document is None:
            raise NotFound("document", data.document_id)

        if data.target_type == "contratto":
            if data.contract_id is not None:
                raise ValidationFailed(
                    ENTITY,
                    "contract_id",
                    "una proposta di tipo 'contratto' non punta a un contratto "
                    "esistente: e' la prima bozza, non un rinnovo",
                    expected="assente quando target_type = 'contratto'",
                )
            fields = self._parse_contratto_fields(data.campi_proposti)
            if self.session.get(Customer, fields.contract.customer_id) is None:
                raise NotFound("customer", fields.contract.customer_id)
        else:
            if data.contract_id is None:
                raise ValidationFailed(
                    ENTITY,
                    "contract_id",
                    "obbligatorio per una proposta di tipo 'giornata'",
                    expected="un contract_id esistente",
                )
            giornata_fields = self._parse_giornata_fields(data.campi_proposti)
            if giornata_fields.contract_id != data.contract_id:
                raise ValidationFailed(
                    ENTITY,
                    "campi_proposti.contract_id",
                    "non corrisponde al contract_id della proposta",
                    expected=str(data.contract_id),
                )
            if self.session.get(Contract, data.contract_id) is None:
                raise NotFound("contract", data.contract_id)

        proposal = self.repo.add(
            Proposal(
                document_id=data.document_id,
                contract_id=data.contract_id,
                target_type=data.target_type,
                campi_proposti=data.campi_proposti,
                estratto=data.estratto,
                tipo_estratto=data.tipo_estratto,
                confidenza=data.confidenza,
                motivo_confidenza=data.motivo_confidenza,
            )
        )
        self.activities.record(
            ENTITY,
            proposal.id,
            "created",
            actor,
            {"target_type": proposal.target_type, "document_id": str(proposal.document_id)},
        )
        self.session.commit()
        return ProposalRead.model_validate(proposal)

    def get(self, proposal_id: UUID, actor: Actor) -> ProposalRead:
        proposal = self.repo.get(proposal_id)
        if proposal is None:
            raise NotFound(ENTITY, proposal_id)
        return ProposalRead.model_validate(proposal)

    def list(self, query: ProposalListQuery, actor: Actor) -> ProposalPage:
        return ProposalPage(items=[ProposalRead.model_validate(p) for p in self.repo.list(query)])

    def _require_pending(self, proposal_id: UUID) -> Proposal:
        proposal = self.repo.get(proposal_id)
        if proposal is None:
            raise NotFound(ENTITY, proposal_id)
        if proposal.stato != "in_attesa":
            raise Conflict(
                ENTITY,
                f"già decisa ({proposal.stato}): una proposta si decide una sola volta",
                proposal_id=str(proposal_id),
            )
        return proposal

    def reject(self, proposal_id: UUID, data: ProposalReject, actor: Actor) -> ProposalRead:
        actor.require_write("reject_proposal")
        proposal = self._require_pending(proposal_id)
        proposal.stato = "rifiutata"
        proposal.deciso_da = data.deciso_da
        proposal.deciso_il = datetime.now(UTC)
        self.activities.record(
            ENTITY,
            proposal.id,
            "rejected",
            actor,
            {"deciso_da": data.deciso_da, "motivo": data.motivo},
        )
        self.session.commit()
        return ProposalRead.model_validate(proposal)

    def accept(self, proposal_id: UUID, data: ProposalAccept, actor: Actor) -> ProposalRead:
        """One transaction, start to finish: the target row (`Contract`+`RateCard`,
        or `Approval`+`WorkUnit`) is built, the proposal's own `stato`/`campi_accettati`
        /`id_risultato`/`deciso_da`/`deciso_il` are set, and exactly one commit closes
        all of it. A `'giornata'` accept never leaves a day at `'proposto'` because
        there is no separate call in between that could be interrupted."""
        actor.require_write("accept_proposal")
        proposal = self._require_pending(proposal_id)
        accepted = (
            data.campi_accettati
            if data.campi_accettati is not None
            else dict(proposal.campi_proposti)
        )
        if proposal.target_type == "contratto":
            id_risultato = self._accept_contratto(proposal, accepted, actor)
        else:
            id_risultato = self._accept_giornata(proposal, accepted, actor)

        proposal.campi_accettati = accepted
        proposal.stato = "accettata"
        proposal.id_risultato = id_risultato
        proposal.deciso_da = data.deciso_da
        proposal.deciso_il = datetime.now(UTC)
        self.activities.record(
            ENTITY,
            proposal.id,
            "accepted",
            actor,
            {"deciso_da": data.deciso_da, "id_risultato": str(id_risultato)},
        )
        self.session.commit()
        return ProposalRead.model_validate(proposal)

    def _accept_contratto(self, proposal: Proposal, campi: dict[str, Any], actor: Actor) -> UUID:
        """Creates the `contracts` row (plus its first `rate_cards` row) and
        re-points the originating document's ownership at it -- spec §10, §11's own
        "widen at accept time, not at archive time" ordering."""
        fields = self._parse_contratto_fields(campi)
        if self.session.get(Customer, fields.contract.customer_id) is None:
            raise NotFound("customer", fields.contract.customer_id)

        contract_payload = fields.contract.model_dump()
        check_payment_terms_together(contract_payload)
        check_renewal_notice(contract_payload)
        contract = Contract(**contract_payload)
        self.session.add(contract)
        self.session.flush()
        self.activities.record(
            "contract",
            contract.id,
            "created",
            actor,
            {"titolo": contract.titolo, "proposal_id": str(proposal.id)},
        )

        rate_card_payload = fields.rate_card.model_dump()
        check_rate_card_shape(rate_card_payload)
        rate_card_payload["contract_id"] = contract.id
        rate_card = RateCard(**rate_card_payload)
        self.session.add(rate_card)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                "rate_card",
                "il periodo di validità si sovrappone a un'altra scheda tariffaria "
                "di questo contratto",
                contract_id=str(contract.id),
            ) from exc
        self.activities.record(
            "contract",
            contract.id,
            "rate_card_added",
            actor,
            {"tipo": rate_card.tipo, "valido_da": str(rate_card.valido_da)},
        )

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

    def _accept_giornata(self, proposal: Proposal, campi: dict[str, Any], actor: Actor) -> UUID:
        """Creates the `approval` row and the `work_unit` at `'approvato'`, both in
        one transaction -- never leaving a day at `'proposto'` (spec §10).

        Re-checks `fields.contract_id` against `proposal.contract_id` even though
        `create()` already checked it once: `campi` here may be a human's edited
        `campi_accettati`, not the original `campi_proposti` `create()` validated,
        and a reviewer who approved a proposal against contract A must not have
        their accept silently redirected to contract B by an edited payload."""
        fields = self._parse_giornata_fields(campi)
        if fields.contract_id != proposal.contract_id:
            raise ValidationFailed(
                ENTITY,
                "campi_accettati.contract_id",
                "non corrisponde al contract_id della proposta",
                expected=str(proposal.contract_id),
            )
        if self.session.get(Contract, fields.contract_id) is None:
            raise NotFound("contract", fields.contract_id)

        approval = Approval(
            contract_id=fields.contract_id,
            canale=fields.approvazione.canale,
            mittente=fields.approvazione.mittente,
            ricevuto_il=fields.approvazione.ricevuto_il,
            message_id=fields.approvazione.message_id,
            document_id=proposal.document_id,
            estratto=proposal.estratto,
            origine={"kind": "agente", "proposal_id": str(proposal.id)},
        )
        self.session.add(approval)
        self.session.flush()

        set_transition_context(self.session, actor, f"proposta {proposal.id} accettata")
        work_unit = WorkUnit(
            contract_id=fields.contract_id,
            data=fields.data,
            quantita=fields.quantita,
            descrizione=fields.descrizione,
            stato="approvato",
            approval_id=approval.id,
        )
        self.session.add(work_unit)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                "work_unit",
                "esiste già un day non concluso per questo contratto in questa data",
                contract_id=str(fields.contract_id),
                data=str(fields.data),
            ) from exc
        self.activities.record(
            "contract",
            fields.contract_id,
            "day_approved",
            actor,
            {"data": str(fields.data), "proposal_id": str(proposal.id)},
        )
        return work_unit.id


__all__ = ["ProposalService"]
