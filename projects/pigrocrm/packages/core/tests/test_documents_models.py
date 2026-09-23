from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.schema_registry import ENTITY_TYPES, native_fields
from pigrocrm.core.templates.models import Template


def _customer(db_session: Session) -> Customer:
    customer = Customer(ragione_sociale="ACME")
    db_session.add(customer)
    db_session.flush()
    return customer


def _contract(db_session: Session, customer: Customer) -> Contract:
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza CTO",
        inizio="2026-01-01",
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"tipo": "non_rimborsabile"},
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def test_a_document_belongs_to_a_customer(db_session: Session) -> None:
    customer = _customer(db_session)
    document = Document(customer_id=customer.id, tipo="offerta", titolo="Offerta 1", stato="bozza")
    db_session.add(document)
    db_session.flush()
    assert document.versione_corrente == 0
    assert document.custom_fields == {}


def test_a_document_with_neither_customer_nor_deal_is_refused(db_session: Session) -> None:
    db_session.add(Document(tipo="documento", titolo="Orfano"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_document_with_both_customer_and_deal_is_refused(db_session: Session) -> None:
    customer = _customer(db_session)
    db_session.add(
        Document(customer_id=customer.id, deal_id=uuid4(), tipo="documento", titolo="Doppio")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_document_can_belong_to_a_contract(db_session: Session) -> None:
    """REB-358 §11: the three-way widening's own third branch -- a contract's
    signed original, discoverable from the contract itself."""
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    document = Document(contract_id=contract.id, tipo="contratto", titolo="Contratto firmato")
    db_session.add(document)
    db_session.flush()
    assert document.customer_id is None
    assert document.deal_id is None


def test_a_document_with_customer_and_contract_is_refused(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    db_session.add(
        Document(
            customer_id=customer.id, contract_id=contract.id, tipo="documento", titolo="Doppio"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_document_with_all_three_owners_is_refused(db_session: Session) -> None:
    customer = _customer(db_session)
    contract = _contract(db_session, customer)
    db_session.add(
        Document(
            customer_id=customer.id,
            deal_id=uuid4(),
            contract_id=contract.id,
            tipo="documento",
            titolo="Triplo",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_two_versions_cannot_share_a_number(db_session: Session) -> None:
    customer = _customer(db_session)
    document = Document(customer_id=customer.id, tipo="offerta", titolo="O")
    db_session.add(document)
    db_session.flush()
    for _ in range(2):
        db_session.add(
            DocumentVersion(
                document_id=document.id,
                numero=1,
                storage_key="a/b/v1.pdf",
                content_type="application/pdf",
                dimensione=10,
                hash_sha256="0" * 64,
            )
        )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_only_one_emitter_profile_row_can_exist(db_session: Session) -> None:
    for _ in range(2):
        db_session.add(EmitterProfile(ragione_sociale="X", partita_iva="12345678901"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_two_templates_cannot_share_a_name_case_insensitively(db_session: Session) -> None:
    db_session.add(Template(nome="Consulenza CTO", tipo="offerta", corpo_markdown="x"))
    db_session.add(Template(nome="consulenza cto", tipo="offerta", corpo_markdown="y"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_document_is_a_recognised_entity_type() -> None:
    assert "document" in ENTITY_TYPES
    assert native_fields("document") == [
        "customer_id",
        "deal_id",
        "contract_id",
        "tipo",
        "titolo",
        "stato",
    ]


def test_documents_has_a_gin_index_on_custom_fields(db_session: Session) -> None:
    indexes = inspect(db_session.get_bind()).get_indexes("documents")
    assert any(index["name"] == "ix_documents_custom_fields" for index in indexes)
