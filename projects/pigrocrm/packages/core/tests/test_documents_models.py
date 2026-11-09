from uuid import uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.schema_registry import ENTITY_TYPES, native_fields
from pigrocrm.core.templates.models import Template


def _contract(db_session: Session, customer: Organization) -> Contract:
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza CTO",
        start="2026-01-01",
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def test_a_document_belongs_to_a_customer(db_session: Session) -> None:
    customer = make_customer(db_session)
    document = Document(customer_id=customer.id, type="offer", title="Offerta 1", status="draft")
    db_session.add(document)
    db_session.flush()
    assert document.current_version == 0
    assert document.custom_fields == {}


def test_a_document_with_neither_customer_nor_deal_is_refused(db_session: Session) -> None:
    db_session.add(Document(type="document", title="Orfano"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_document_with_both_customer_and_deal_is_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    db_session.add(
        Document(customer_id=customer.id, deal_id=uuid4(), type="document", title="Doppio")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_document_can_belong_to_a_contract(db_session: Session) -> None:
    """REB-358 §11: the three-way widening's own third branch -- a contract's
    signed original, discoverable from the contract itself."""
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    document = Document(contract_id=contract.id, type="contract", title="Contratto firmato")
    db_session.add(document)
    db_session.flush()
    assert document.customer_id is None
    assert document.deal_id is None


def test_a_document_with_customer_and_contract_is_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    db_session.add(
        Document(customer_id=customer.id, contract_id=contract.id, type="document", title="Doppio")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_document_with_all_three_owners_is_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    db_session.add(
        Document(
            customer_id=customer.id,
            deal_id=uuid4(),
            contract_id=contract.id,
            type="document",
            title="Triplo",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_two_versions_cannot_share_a_number(db_session: Session) -> None:
    customer = make_customer(db_session)
    document = Document(customer_id=customer.id, type="offer", title="O")
    db_session.add(document)
    db_session.flush()
    for _ in range(2):
        db_session.add(
            DocumentVersion(
                document_id=document.id,
                number=1,
                storage_key="a/b/v1.pdf",
                content_type="application/pdf",
                size=10,
                hash_sha256="0" * 64,
            )
        )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_only_one_legal_entity_can_be_the_default(db_session: Session) -> None:
    # REB-615: the table holds one row per azienda now; what stays unique is the
    # default, by a partial index, and a fiscal id, by two more. The session seeds a
    # default already, so it goes first: the violation must come from the second of
    # the two rows added here, not from the seed.
    from sqlalchemy import delete

    db_session.execute(delete(LegalEntity))
    db_session.flush()
    for i in range(2):
        db_session.add(LegalEntity(legal_name="X", vat_number=f"1234567890{i}", is_default=True))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_two_templates_cannot_share_a_name_case_insensitively(db_session: Session) -> None:
    db_session.add(Template(name="Consulenza CTO", type="offer", body_markdown="x"))
    db_session.add(Template(name="consulenza cto", type="offer", body_markdown="y"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_document_is_a_recognised_entity_type() -> None:
    assert "document" in ENTITY_TYPES
    assert native_fields("document") == [
        "customer_id",
        "deal_id",
        "contract_id",
        "type",
        "title",
        "status",
    ]


def test_documents_has_a_gin_index_on_custom_fields(db_session: Session) -> None:
    indexes = inspect(db_session.get_bind()).get_indexes("documents")
    assert any(index["name"] == "ix_documents_custom_fields" for index in indexes)
