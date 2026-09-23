"""REB-362's own "what the implementing issues must test" for proposal review (design
spec §14, "Proposals"): the CHECK tying `contract_id`'s nullability to `target_type`,
a `'giornata'` accept creating both the `approval` and the `work_unit` in one
transaction, `campi_proposti`/`campi_accettati` never overwritten, and `estratto`
verifiable against the source document's own text for `tipo_estratto = 'citato'`.

`campi_proposti` payloads below are built as plain JSON-safe primitives (strings for
UUIDs/dates, decimal strings for money and quantities) -- exactly the shape a real
MCP tool or REST call would submit, never a Python `UUID`/`date`/`Decimal` object --
so these tests exercise the same coercion `ProposalService` applies to caller-supplied
JSON, not a shortcut that skips it.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_text import minimal_pdf

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.proposals.schemas import (
    ProposalAccept,
    ProposalCreate,
    ProposalListQuery,
    ProposalReject,
)
from pigrocrm.core.proposals.service import ProposalService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.work_units.models import Approval, WorkUnit

ADMIN = Actor(id=None, type="system", role="admin")


def _customer(db_session: Session) -> Customer:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    return customer


def _contract(
    db_session: Session, *, customer_id: UUID | None = None, **overrides: object
) -> Contract:
    payload: dict[str, object] = {
        "customer_id": customer_id or _customer(db_session).id,
        "titolo": "Consulenza",
        "inizio": date(2026, 1, 1),
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"kind": "non_rimborsabile"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    db_session.add(contract)
    db_session.flush()
    return contract


def _document(db_session: Session, **owner: object) -> Document:
    document = Document(tipo="contratto", titolo="Documento archiviato", **owner)  # type: ignore[arg-type]
    db_session.add(document)
    db_session.flush()
    return document


def _contratto_campi(customer_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract": {
            "customer_id": str(customer_id),
            "titolo": "Consulenza annuale",
            "inizio": "2026-01-01",
            "tipo_rinnovo": "nessuno",
            "preavviso_disdetta_giorni": 30,
            "cadenza_fatturazione": "mensile",
            "politica_spese": {"kind": "non_rimborsabile"},
        },
        "rate_card": {
            "valido_da": "2026-01-01",
            "tipo": "ricorrente_fisso",
            "importo": "1000.00",
            "unita": "mese",
            "periodo_erogazione": "mensile",
        },
    }
    payload.update(overrides)
    return payload


def _giornata_campi(contract_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_id": str(contract_id),
        "data": "2026-03-05",
        "quantita": "1.00",
        "descrizione": "Giornata di consulenza",
        "approvazione": {
            "canale": "email",
            "mittente": "cliente@example.test",
            "ricevuto_il": "2026-03-01T09:00:00+00:00",
            "message_id": None,
        },
    }
    payload.update(overrides)
    return payload


def _proposal_create(document_id: UUID, **overrides: object) -> ProposalCreate:
    payload: dict[str, object] = {
        "document_id": document_id,
        "target_type": "giornata",
        "campi_proposti": {},
        "estratto": "Confermiamo la giornata del 5 marzo.",
        "confidenza": Decimal("0.90"),
    }
    payload.update(overrides)
    return ProposalCreate(**payload)  # type: ignore[arg-type]


# ---- create: the one proposal-creation step -----------------------------------------


def test_create_of_a_contratto_proposal_produces_a_reviewable_row_with_its_excerpt(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    proposal = ProposalService(db_session).create(
        _proposal_create(
            document.id,
            target_type="contratto",
            campi_proposti=_contratto_campi(customer.id),
            estratto="Il presente contratto ha durata annuale.",
        ),
        ADMIN,
    )
    assert proposal.stato == "in_attesa"
    assert proposal.contract_id is None
    assert proposal.estratto == "Il presente contratto ha durata annuale."
    assert proposal.campi_proposti["contract"]["titolo"] == "Consulenza annuale"


def test_create_of_a_giornata_proposal_produces_a_reviewable_row_with_its_excerpt(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    proposal = ProposalService(db_session).create(
        _proposal_create(
            document.id,
            target_type="giornata",
            contract_id=contract.id,
            campi_proposti=_giornata_campi(contract.id),
            estratto="Confermiamo la giornata del 5 marzo.",
        ),
        ADMIN,
    )
    assert proposal.stato == "in_attesa"
    assert proposal.contract_id == contract.id
    assert proposal.campi_accettati is None


def test_create_rejects_an_unknown_document(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ProposalService(db_session).create(
            _proposal_create(uuid4(), target_type="contratto", campi_proposti={}), ADMIN
        )


def test_create_of_contratto_rejects_a_supplied_contract_id(db_session: Session) -> None:
    """REB-344's own scope-down: this spike does not model a renewal-targeted
    proposal, so a `'contratto'` proposal never points at an existing contract."""
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    contract = _contract(db_session, customer_id=customer.id)
    with pytest.raises(ValidationFailed) as excinfo:
        ProposalService(db_session).create(
            _proposal_create(
                document.id,
                target_type="contratto",
                contract_id=contract.id,
                campi_proposti=_contratto_campi(customer.id),
            ),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "contract_id"


def test_create_of_giornata_requires_a_contract_id(db_session: Session) -> None:
    document = _document(db_session, customer_id=_customer(db_session).id)
    with pytest.raises(ValidationFailed) as excinfo:
        ProposalService(db_session).create(
            _proposal_create(document.id, target_type="giornata", campi_proposti={}), ADMIN
        )
    assert excinfo.value.details["field"] == "contract_id"


def test_create_of_giornata_rejects_a_contract_id_mismatched_with_campi_proposti(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    other_contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    with pytest.raises(ValidationFailed) as excinfo:
        ProposalService(db_session).create(
            _proposal_create(
                document.id,
                target_type="giornata",
                contract_id=contract.id,
                campi_proposti=_giornata_campi(other_contract.id),
            ),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "campi_proposti.contract_id"


def test_create_of_giornata_rejects_an_unknown_contract(db_session: Session) -> None:
    document = _document(db_session, customer_id=_customer(db_session).id)
    unknown = uuid4()
    with pytest.raises(NotFound):
        ProposalService(db_session).create(
            _proposal_create(
                document.id,
                target_type="giornata",
                contract_id=unknown,
                campi_proposti=_giornata_campi(unknown),
            ),
            ADMIN,
        )


def test_create_of_contratto_rejects_an_unknown_customer(db_session: Session) -> None:
    document = _document(db_session, customer_id=_customer(db_session).id)
    with pytest.raises(NotFound):
        ProposalService(db_session).create(
            _proposal_create(
                document.id, target_type="contratto", campi_proposti=_contratto_campi(uuid4())
            ),
            ADMIN,
        )


def test_create_rejects_a_malformed_campi_proposti_shape_with_a_clean_error(
    db_session: Session,
) -> None:
    """A schema mismatch is a `ValidationFailed`, not a raw pydantic traceback or a
    silent write of garbage into the JSONB column."""
    document = _document(db_session, customer_id=_customer(db_session).id)
    with pytest.raises(ValidationFailed) as excinfo:
        ProposalService(db_session).create(
            _proposal_create(document.id, target_type="contratto", campi_proposti={"contract": {}}),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "campi_proposti"


# ---- get / list -----------------------------------------------------------------------


def test_get_of_an_unknown_proposal_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ProposalService(db_session).get(uuid4(), ADMIN)


def test_list_filters_by_stato_oldest_first(db_session: Session) -> None:
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    first = service.create(
        _proposal_create(
            document.id, target_type="contratto", campi_proposti=_contratto_campi(customer.id)
        ),
        ADMIN,
    )
    second = service.create(
        _proposal_create(
            document.id, target_type="contratto", campi_proposti=_contratto_campi(customer.id)
        ),
        ADMIN,
    )
    service.reject(second.id, ProposalReject(deciso_da="Lorenzo"), ADMIN)

    pending = service.list(ProposalListQuery(stato="in_attesa"), ADMIN)
    assert [p.id for p in pending.items] == [first.id]


# ---- reject -----------------------------------------------------------------------


def test_reject_moves_a_proposal_to_rifiutata_and_records_who_and_when(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contratto", campi_proposti=_contratto_campi(customer.id)
        ),
        ADMIN,
    )
    rejected = service.reject(proposal.id, ProposalReject(deciso_da="Lorenzo Fiore"), ADMIN)
    assert rejected.stato == "rifiutata"
    assert rejected.deciso_da == "Lorenzo Fiore"
    assert rejected.deciso_il is not None


def test_reject_of_an_already_decided_proposal_is_a_conflict(db_session: Session) -> None:
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contratto", campi_proposti=_contratto_campi(customer.id)
        ),
        ADMIN,
    )
    service.reject(proposal.id, ProposalReject(deciso_da="Lorenzo"), ADMIN)
    with pytest.raises(Conflict):
        service.reject(proposal.id, ProposalReject(deciso_da="Lorenzo"), ADMIN)


def test_reject_of_an_unknown_proposal_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ProposalService(db_session).reject(uuid4(), ProposalReject(deciso_da="Lorenzo"), ADMIN)


# ---- accept: 'contratto' -----------------------------------------------------------


def test_accept_contratto_creates_a_contract_and_its_first_rate_card(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contratto", campi_proposti=_contratto_campi(customer.id)
        ),
        ADMIN,
    )

    accepted = service.accept(proposal.id, ProposalAccept(deciso_da="Lorenzo Fiore"), ADMIN)

    assert accepted.stato == "accettata"
    assert accepted.id_risultato is not None
    contract = db_session.get(Contract, accepted.id_risultato)
    assert contract is not None
    assert contract.titolo == "Consulenza annuale"
    cards = db_session.query(RateCard).filter_by(contract_id=contract.id).all()
    assert len(cards) == 1
    assert cards[0].tipo == "ricorrente_fisso"


def test_accept_contratto_repoints_the_originating_documents_ownership(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contratto", campi_proposti=_contratto_campi(customer.id)
        ),
        ADMIN,
    )

    accepted = service.accept(proposal.id, ProposalAccept(deciso_da="Lorenzo Fiore"), ADMIN)

    db_session.refresh(document)
    assert document.contract_id == accepted.id_risultato
    assert document.customer_id is None
    assert document.deal_id is None


def test_accept_preserves_campi_proposti_and_stores_the_override_separately(
    db_session: Session,
) -> None:
    """Spec §10: "kept separately... it stays correct forever only if neither side
    is ever overwritten." """
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    original_campi = _contratto_campi(customer.id)
    proposal = service.create(
        _proposal_create(document.id, target_type="contratto", campi_proposti=original_campi),
        ADMIN,
    )

    edited_contract = {**original_campi["contract"], "titolo": "Consulenza (corretta)"}
    edited_campi = _contratto_campi(customer.id, contract=edited_contract)
    accepted = service.accept(
        proposal.id, ProposalAccept(deciso_da="Lorenzo", campi_accettati=edited_campi), ADMIN
    )

    assert accepted.campi_proposti["contract"]["titolo"] == "Consulenza annuale"
    assert accepted.campi_accettati["contract"]["titolo"] == "Consulenza (corretta)"
    contract = db_session.get(Contract, accepted.id_risultato)
    assert contract.titolo == "Consulenza (corretta)"


def test_accept_of_an_already_decided_proposal_is_a_conflict(db_session: Session) -> None:
    customer = _customer(db_session)
    document = _document(db_session, customer_id=customer.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id, target_type="contratto", campi_proposti=_contratto_campi(customer.id)
        ),
        ADMIN,
    )
    service.reject(proposal.id, ProposalReject(deciso_da="Lorenzo"), ADMIN)
    with pytest.raises(Conflict):
        service.accept(proposal.id, ProposalAccept(deciso_da="Lorenzo"), ADMIN)


# ---- accept: 'giornata' -------------------------------------------------------------


def test_accept_giornata_creates_an_approval_and_a_work_unit_at_approvato_in_one_call(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id,
            target_type="giornata",
            contract_id=contract.id,
            campi_proposti=_giornata_campi(contract.id),
        ),
        ADMIN,
    )

    accepted = service.accept(proposal.id, ProposalAccept(deciso_da="Lorenzo Fiore"), ADMIN)

    assert accepted.stato == "accettata"
    work_unit = db_session.get(WorkUnit, accepted.id_risultato)
    assert work_unit is not None
    # Never left at 'proposto' -- the whole point of spec §10's own rule.
    assert work_unit.stato == "approvato"
    assert work_unit.approval_id is not None
    approval = db_session.get(Approval, work_unit.approval_id)
    assert approval is not None
    assert approval.document_id == document.id
    assert approval.estratto == proposal.estratto
    assert approval.origine == {"kind": "agente", "proposal_id": str(proposal.id)}


def test_accept_giornata_rejects_an_edited_contract_id_that_diverges_from_the_proposal(
    db_session: Session,
) -> None:
    """The reviewer looked at `proposal.contract_id`; an edited `campi_accettati`
    must not be able to silently redirect the accept onto a different contract."""
    contract = _contract(db_session)
    other_contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id,
            target_type="giornata",
            contract_id=contract.id,
            campi_proposti=_giornata_campi(contract.id),
        ),
        ADMIN,
    )

    diverging = _giornata_campi(other_contract.id)
    with pytest.raises(ValidationFailed) as excinfo:
        service.accept(
            proposal.id, ProposalAccept(deciso_da="Lorenzo", campi_accettati=diverging), ADMIN
        )
    assert excinfo.value.details["field"] == "campi_accettati.contract_id"
    assert db_session.query(WorkUnit).count() == 0
    assert db_session.query(Approval).count() == 0
    reloaded = service.get(proposal.id, ADMIN)
    assert reloaded.stato == "in_attesa"


def test_accept_giornata_is_atomic_when_the_work_unit_write_conflicts(
    db_session: Session,
) -> None:
    """The partial unique index (`uq_work_units_contract_data_live`) refuses a
    second live day on the same contract and date. When it does, nothing of this
    accept attempt survives: no orphan `Approval`, and the proposal stays
    `'in_attesa'` -- never a day silently left at `'proposto'`, and never a
    half-applied accept."""
    contract = _contract(db_session)
    document = _document(db_session, contract_id=contract.id)
    service = ProposalService(db_session)
    proposal = service.create(
        _proposal_create(
            document.id,
            target_type="giornata",
            contract_id=contract.id,
            campi_proposti=_giornata_campi(contract.id, data="2026-03-05"),
        ),
        ADMIN,
    )
    # An already-live day on the same contract and date, committed independently of
    # the proposal above.
    db_session.add(
        WorkUnit(
            contract_id=contract.id,
            data=date(2026, 3, 5),
            quantita=Decimal("1.00"),
            descrizione="Giornata già registrata",
            stato="proposto",
        )
    )
    db_session.commit()
    approvals_before = db_session.query(Approval).filter_by(contract_id=contract.id).count()

    with pytest.raises(Conflict):
        service.accept(proposal.id, ProposalAccept(deciso_da="Lorenzo Fiore"), ADMIN)

    approvals_after = db_session.query(Approval).filter_by(contract_id=contract.id).count()
    assert approvals_after == approvals_before

    reloaded = service.get(proposal.id, ADMIN)
    assert reloaded.stato == "in_attesa"
    assert reloaded.id_risultato is None


# ---- the database CHECK, bypassing the service entirely -----------------------------


def test_the_database_refuses_a_giornata_proposal_with_no_contract_id(
    db_session: Session,
) -> None:
    """Done-when: the CHECK tying `contract_id`'s nullability to `target_type` is
    enforced by the database, not only by `ProposalService.create`'s friendly
    pre-check -- a direct ORM insert that skips the service hits it too."""
    document = _document(db_session, customer_id=_customer(db_session).id)
    with pytest.raises(IntegrityError):
        db_session.add(
            Proposal(
                document_id=document.id,
                contract_id=None,
                target_type="giornata",
                campi_proposti={},
                estratto="qualcosa",
                confidenza=Decimal("0.50"),
            )
        )
        db_session.flush()


def test_the_database_allows_a_contratto_proposal_with_no_contract_id(
    db_session: Session,
) -> None:
    """The CHECK's asymmetry, asserted directly: `'contratto'` is the one
    `target_type` allowed to omit `contract_id` -- first intake has no contract row
    to point at yet."""
    document = _document(db_session, customer_id=_customer(db_session).id)
    proposal = Proposal(
        document_id=document.id,
        contract_id=None,
        target_type="contratto",
        campi_proposti={},
        estratto="qualcosa",
        confidenza=Decimal("0.50"),
    )
    db_session.add(proposal)
    db_session.flush()
    assert proposal.contract_id is None


# ---- estratto verifiability (tipo_estratto = 'citato') -------------------------------


def test_a_citato_excerpt_is_verifiable_against_the_documents_own_text(
    db_session: Session, tmp_path: object
) -> None:
    """mastro's own reasoning for `citato` (spec §10): "a span the application
    located in the document's own text (verifiable, searchable)". Proved here
    against the real extraction pipeline (`DocumentService.extract_text`), not a
    string the test only asserts is equal to itself."""
    customer = _customer(db_session)
    storage = LocalFileStorage(str(tmp_path))  # type: ignore[arg-type]
    doc_service = DocumentService(db_session, storage)
    created = doc_service.create(
        DocumentCreate(customer_id=customer.id, tipo="contratto", titolo="Contratto firmato"),
        ADMIN,
    )
    excerpt = "Il contratto ha durata di dodici mesi rinnovabili."
    doc_service.add_version(created.id, minimal_pdf([excerpt]), "application/pdf", ADMIN)

    proposal = ProposalService(db_session).create(
        _proposal_create(
            created.id,
            target_type="contratto",
            campi_proposti=_contratto_campi(customer.id),
            estratto=excerpt,
            tipo_estratto="citato",
        ),
        ADMIN,
    )

    letto = doc_service.extract_text(created.id, None, ADMIN)
    assert proposal.tipo_estratto == "citato"
    assert proposal.estratto in letto.testo
