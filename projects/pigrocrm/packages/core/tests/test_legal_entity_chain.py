"""The legal entity on customers, deals, contracts, documents and costs (REB-623, spec
2026-10-03 §1.6, §1.7, §8 «Chain»).

A new customer gets the legal entity its nation proposes; a deal, a contract, a document and
an invoice take their parent's at creation and keep it; a cost takes its deal's or is
shared; every list and the search narrow to one legal entity, and the sum over legal entities is the
unfiltered list. The second legal entity is written by row, since no route creates one before
milestone 5 (§9).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.calendar.service import CalendarService
from pigrocrm.core.contracts.schemas import ContractCreate, ContractListQuery
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.customers.schemas import CustomerCreate, CustomerListQuery, CustomerUpdate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.schemas import DealCreate, DealListQuery
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents.schemas import DocumentCreate, DocumentListQuery
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn, InvoiceListQuery
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.search.schemas import SearchQuery
from pigrocrm.core.search.service import SearchService
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.schemas import (
    CostCreate,
    CostListQuery,
    CostUpdate,
    TimeEntryListQuery,
)
from pigrocrm.core.timetracking.service import TimeEntryService

ADMIN = Actor(id=None, type="system", role="admin")


def _default(session: Session) -> LegalEntity:
    legal_entity = LegalEntityRepository(session).default()
    assert legal_entity is not None
    return legal_entity


def _legal_entity(
    session: Session, name: str, country: str = "IT", **overrides: object
) -> LegalEntity:
    values: dict[str, object] = {
        "name": name,
        "legal_name": f"{name.title()} S.r.l.",
        "country": country,
        "address": "Via Po 1",
        "cap": "10100",
        "city": "Torino",
        "province": "TO",
    }
    values.update(overrides)
    row = LegalEntity(**values)
    session.add(row)
    session.flush()
    return row


def _customer(session: Session, name: str = "Acme", **overrides: object) -> UUID:
    values: dict[str, object] = {"legal_name": f"{name} S.r.l.", "country": "IT"}
    values.update(overrides)
    return CustomerService(session).create(CustomerCreate(**values), ADMIN).id  # type: ignore[arg-type]


def _contract(session: Session, customer_id: UUID) -> UUID:
    return (
        ContractService(session)
        .create(
            ContractCreate(
                customer_id=customer_id,
                title="Consulenza",
                start=date(2026, 1, 1),
                renewal_type="none",
                termination_notice_days=30,
                billing_cadence="monthly",
                expense_policy={"kind": "not_reimbursable"},
            ),
            ADMIN,
        )
        .id
    )


# --- the proposal -----------------------------------------------------------------


def test_the_nation_proposes_the_one_legal_entity_that_shares_it(db_session: Session) -> None:
    default = _default(db_session)
    british = _legal_entity(db_session, "rebase ltd", "GB", province=None)
    service = LegalEntityService(db_session)
    assert service.propose("GB").id == british.id
    assert service.propose("gb").id == british.id
    assert service.propose("IT").id == default.id
    assert service.propose(None).id == default.id


def test_to_foreign_customer_gets_the_one_foreign_legal_entity_when_its_nation_has_none(
    db_session: Session,
) -> None:
    french = _legal_entity(db_session, "rebase sarl", "FR", province=None)
    assert LegalEntityService(db_session).propose("DE").id == french.id


def test_the_nation_never_decides_between_two_legal_entities_that_share_it(
    db_session: Session,
) -> None:
    """Two Italian legal entities: an Italian customer gets the default, the person picks. Two
    foreign ones and a customer of a third country: the default again."""
    default = _default(db_session)
    _legal_entity(db_session, "rebase")
    service = LegalEntityService(db_session)
    assert service.propose("IT").id == default.id
    _legal_entity(db_session, "rebase ltd", "GB", province=None)
    _legal_entity(db_session, "rebase sarl", "FR", province=None)
    assert service.propose("DE").id == default.id
    assert service.propose("GB").id != default.id


def test_an_inactive_legal_entity_is_never_proposed(db_session: Session) -> None:
    _legal_entity(db_session, "rebase ltd", "GB", province=None, active=False)
    assert LegalEntityService(db_session).propose("GB").id == _default(db_session).id


# --- the customer -----------------------------------------------------------------


def test_to_customer_takes_the_proposal_or_the_legal_entity_named(db_session: Session) -> None:
    default = _default(db_session)
    british = _legal_entity(db_session, "rebase ltd", "GB", province=None)
    service = CustomerService(db_session)
    proposed = service.create(CustomerCreate(legal_name="Overseas", country="GB"), ADMIN)
    assert proposed.legal_entity_id == british.id
    named = service.create(
        CustomerCreate(legal_name="Oltre", country="GB", legal_entity_id=default.id), ADMIN
    )
    assert named.legal_entity_id == default.id
    with pytest.raises(NotFound):
        service.create(CustomerCreate(legal_name="X", legal_entity_id=uuid4()), ADMIN)
    inactive = _legal_entity(db_session, "chiusa", active=False)
    with pytest.raises(ValidationFailed) as refused:
        service.create(CustomerCreate(legal_name="X", legal_entity_id=inactive.id), ADMIN)
    assert refused.value.details["field"] == "legal_entity_id"


def test_moving_a_customer_leaves_its_deals_documents_and_invoices_behind(
    db_session: Session, local_storage: LocalFileStorage, seeded_open_stage_id: UUID
) -> None:
    """Spec §1.7, Ivan's rule: «i deal aperti restano dove sono». Only what is created
    after the move follows the customer."""
    default = _default(db_session)
    second = _legal_entity(db_session, "rebase")
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(regime_code="RF19"), ADMIN, legal_entity_id=second.id
    )
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    customer_id = _customer(db_session)
    deal = DealService(db_session).create(
        DealCreate(name="Primo", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    contract_id = _contract(db_session, customer_id)
    document = DocumentService(db_session, local_storage).create(
        DocumentCreate(customer_id=customer_id, title="Brief"), ADMIN
    )
    invoices = InvoiceService(db_session, local_storage)
    before = invoices.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("100.00"))],
        ),
        ADMIN,
    )
    assert {deal.legal_entity_id, document.legal_entity_id, before.legal_entity_id} == {default.id}
    assert ContractService(db_session).get(contract_id, ADMIN).legal_entity_id == default.id

    moved = CustomerService(db_session).update(
        customer_id, CustomerUpdate(legal_entity_id=second.id), ADMIN
    )
    assert moved.legal_entity_id == second.id
    assert DealService(db_session).get(deal.id, ADMIN).legal_entity_id == default.id
    assert ContractService(db_session).get(contract_id, ADMIN).legal_entity_id == default.id
    assert (
        DocumentService(db_session, local_storage).get(document.id, ADMIN).legal_entity_id
        == default.id
    )
    assert invoices.get(before.id, ADMIN).legal_entity_id == default.id

    after_deal = DealService(db_session).create(
        DealCreate(name="Secondo", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    after_invoice = invoices.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("100.00"))],
        ),
        ADMIN,
    )
    assert (after_deal.legal_entity_id, after_invoice.legal_entity_id) == (second.id, second.id)
    # An invoice born from a deal takes the deal's legal entity, not the customer's of today.
    from_old_deal = invoices.create(
        InvoiceCreate(
            customer_id=customer_id,
            deal_id=deal.id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("100.00"))],
        ),
        ADMIN,
    )
    assert from_old_deal.legal_entity_id == default.id
    # And a document hung on the old deal takes the deal's too.
    on_deal = DocumentService(db_session, local_storage).create(
        DocumentCreate(deal_id=deal.id, title="Verbale"), ADMIN
    )
    assert on_deal.legal_entity_id == default.id


# --- the cost ---------------------------------------------------------------------


def test_to_cost_takes_its_deals_legal_entity_and_to_shared_one_has_none(
    db_session: Session, seeded_open_stage_id: UUID, seeded_category_id: UUID
) -> None:
    default = _default(db_session)
    second = _legal_entity(db_session, "rebase")
    customer_id = _customer(db_session, legal_entity_id=second.id)
    deal = DealService(db_session).create(
        DealCreate(name="Rebase", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    costs = CostService(db_session)
    on_deal = costs.create(
        CostCreate(
            deal_id=deal.id,
            category_id=seeded_category_id,
            date=date(2026, 3, 1),
            amount=Decimal("10.00"),
            description="Treno",
        ),
        ADMIN,
    )
    assert on_deal.legal_entity_id == second.id
    shared = costs.create(
        CostCreate(
            category_id=seeded_category_id,
            date=date(2026, 3, 2),
            amount=Decimal("20.00"),
            description="Software",
        ),
        ADMIN,
    )
    assert shared.legal_entity_id is None
    own = costs.create(
        CostCreate(
            category_id=seeded_category_id,
            date=date(2026, 3, 3),
            amount=Decimal("30.00"),
            description="Licenza",
            legal_entity_id=default.id,
        ),
        ADMIN,
    )
    assert own.legal_entity_id == default.id
    with pytest.raises(ValidationFailed) as refused:
        costs.create(
            CostCreate(
                deal_id=deal.id,
                legal_entity_id=default.id,
                category_id=seeded_category_id,
                date=date(2026, 3, 4),
                amount=Decimal("1.00"),
                description="Sbagliato",
            ),
            ADMIN,
        )
    assert refused.value.details["field"] == "legal_entity_id"
    # Off the deal, the cost is shared unless a legal entity is named; onto it, the deal's.
    assert costs.update(on_deal.id, CostUpdate(deal_id=None), ADMIN).legal_entity_id is None
    assert costs.update(on_deal.id, CostUpdate(deal_id=deal.id), ADMIN).legal_entity_id == second.id
    assert (
        costs.update(shared.id, CostUpdate(legal_entity_id=default.id), ADMIN).legal_entity_id
        == default.id
    )

    listed = costs.list(CostListQuery(legal_entity_id=second.id), ADMIN).items
    assert [c.id for c in listed] == [on_deal.id]
    everything = costs.list(CostListQuery(), ADMIN).items
    assert {c.id for c in everything} >= {on_deal.id, shared.id, own.id}


# --- the lists and the search -----------------------------------------------------


def test_every_list_narrows_to_one_legal_entity_and_the_sum_is_the_whole(
    db_session: Session,
    local_storage: LocalFileStorage,
    seeded_open_stage_id: UUID,
    time_entry_factory,  # type: ignore[no-untyped-def]  # noqa: ANN001
) -> None:
    default = _default(db_session)
    second = _legal_entity(db_session, "rebase")
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(regime_code="RF19"), ADMIN, legal_entity_id=second.id
    )
    acme = _customer(db_session, "Acme")
    beta = _customer(db_session, "Beta", legal_entity_id=second.id)
    deals = DealService(db_session)
    deal_a = deals.create(
        DealCreate(name="Acme deal", customer_id=acme, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    deal_b = deals.create(
        DealCreate(name="Beta deal", customer_id=beta, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    _contract(db_session, acme)
    _contract(db_session, beta)
    documents = DocumentService(db_session, local_storage)
    documents.create(DocumentCreate(customer_id=acme, title="Acme brief"), ADMIN)
    documents.create(DocumentCreate(deal_id=deal_b.id, title="Beta brief"), ADMIN)
    invoices = InvoiceService(db_session, local_storage)
    for customer in (acme, beta):
        invoices.create(
            InvoiceCreate(
                customer_id=customer,
                lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1.00"))],
            ),
            ADMIN,
        )
    time_entry_factory(date=date(2026, 3, 2), deal_id=deal_a.id)
    time_entry_factory(date=date(2026, 3, 3), deal_id=deal_b.id)

    def ids(items: list) -> set[UUID]:  # type: ignore[type-arg]
        return {item.id for item in items}

    pairs = [
        (
            CustomerService(db_session),
            lambda a: CustomerListQuery(legal_entity_id=a),
        ),
        (deals, lambda a: DealListQuery(legal_entity_id=a)),
        (ContractService(db_session), lambda a: ContractListQuery(legal_entity_id=a)),
        (documents, lambda a: DocumentListQuery(legal_entity_id=a)),
        (invoices, lambda a: InvoiceListQuery(legal_entity_id=a)),
        (TimeEntryService(db_session), lambda a: TimeEntryListQuery(legal_entity_id=a)),
    ]
    for service, query in pairs:
        on_default = ids(service.list(query(default.id), ADMIN).items)
        on_second = ids(service.list(query(second.id), ADMIN).items)
        everything = ids(service.list(query(None), ADMIN).items)
        assert on_default and on_second, type(service).__name__
        assert on_default.isdisjoint(on_second), type(service).__name__
        assert on_default | on_second == everything, type(service).__name__
        assert ids(service.list(query(uuid4()), ADMIN).items) == set()

    # The calendar's hours follow the deal's legal entity.
    calendar = CalendarService(db_session)
    whole = calendar.month("2026-03", ADMIN, user_id=None)
    only_second = calendar.month("2026-03", ADMIN, user_id=None, legal_entity_id=second.id)
    assert len(whole.days) >= 2
    assert [g.day for g in only_second.days if g.hours] == [date(2026, 3, 3)]

    # And the search: Beta is the second legal entity's, Acme is not.
    search = SearchService(db_session)
    everyone = search.search_everything(SearchQuery(term="brief"), ADMIN)
    narrowed = search.search_everything(SearchQuery(term="brief", legal_entity_id=second.id), ADMIN)
    documents_group = next(g for g in narrowed.groups if g.entity == "document")
    assert [h.label for h in documents_group.hits] == ["Beta brief"]
    assert next(g for g in everyone.groups if g.entity == "document").total == 2
    customers_group = next(
        g
        for g in search.search_everything(
            SearchQuery(term="Acme", legal_entity_id=second.id), ADMIN
        ).groups
        if g.entity == "customer"
    )
    assert customers_group.hits == []


def test_to_space_with_one_legal_entity_sees_nothing_change(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    default = _default(db_session)
    customer = CustomerService(db_session).create(CustomerCreate(legal_name="Solo"), ADMIN)
    deal = DealService(db_session).create(
        DealCreate(name="Solo", customer_id=customer.id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    assert customer.legal_entity_id == deal.legal_entity_id == default.id
    assert LegalEntityService(db_session).propose("DE").id == default.id


# --- a deactivated legal entity --------------------------------------------------------


def test_nothing_new_is_born_under_to_customer_of_to_deactivated_legal_entity(
    db_session: Session, local_storage: LocalFileStorage, seeded_open_stage_id: UUID
) -> None:
    """Spec §3: the history of a closed legal entity stays readable, a deal, a contract, a
    document or a proforma under one of its customers is refused in words."""
    second = _legal_entity(db_session, "rebase")
    customer_id = _customer(db_session, legal_entity_id=second.id)
    second.active = False
    db_session.flush()
    with pytest.raises(ValidationFailed) as deal:
        DealService(db_session).create(
            DealCreate(
                name="Dopo", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id
            ),
            ADMIN,
        )
    assert deal.value.details["field"] == "customer_id"
    assert "sposta prima il cliente" in str(deal.value)
    with pytest.raises(ValidationFailed):
        _contract(db_session, customer_id)
    with pytest.raises(ValidationFailed):
        DocumentService(db_session, local_storage).create(
            DocumentCreate(customer_id=customer_id, title="Brief"), ADMIN
        )
    with pytest.raises(ValidationFailed):
        InvoiceService(db_session, local_storage).create(
            InvoiceCreate(
                customer_id=customer_id,
                lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1.00"))],
            ),
            ADMIN,
        )
    # Moved to an active legal entity, the same customer works again.
    CustomerService(db_session).update(
        customer_id, CustomerUpdate(legal_entity_id=_default(db_session).id), ADMIN
    )
    assert (
        DealService(db_session)
        .create(
            DealCreate(
                name="Dopo", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id
            ),
            ADMIN,
        )
        .legal_entity_id
        == _default(db_session).id
    )


def test_a_cost_of_an_archived_deal_can_still_be_edited(
    db_session: Session, seeded_open_stage_id: UUID, seeded_category_id: UUID
) -> None:
    """The SPA sends the seeded `legal_entity_id` back on every edit, so the deal it reads it
    against may be archived since: a domain answer, never an assertion."""
    customer_id = _customer(db_session)
    deal = DealService(db_session).create(
        DealCreate(name="Vecchio", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    costs = CostService(db_session)
    cost = costs.create(
        CostCreate(
            deal_id=deal.id,
            category_id=seeded_category_id,
            date=date(2026, 3, 1),
            amount=Decimal("10.00"),
            description="Treno",
        ),
        ADMIN,
    )
    DealService(db_session).soft_delete(deal.id, ADMIN)
    edited = costs.update(
        cost.id,
        CostUpdate(description="Treno regionale", legal_entity_id=deal.legal_entity_id),
        ADMIN,
    )
    assert (edited.description, edited.legal_entity_id) == ("Treno regionale", deal.legal_entity_id)


def test_to_new_general_cost_is_refused_on_to_deactivated_legal_entity_and_an_old_one_stays(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """Spec §3 for the one record that names its legal entity directly: nothing new is filed
    under a closed legal entity, but a cost already there can still be edited in place."""
    closed = _legal_entity(db_session, "rebase")
    costs = CostService(db_session)
    old = costs.create(
        CostCreate(
            category_id=seeded_category_id,
            date=date(2026, 3, 1),
            amount=Decimal("10.00"),
            description="Licenza",
            legal_entity_id=closed.id,
        ),
        ADMIN,
    )
    closed.active = False
    db_session.flush()
    with pytest.raises(ValidationFailed) as refused:
        costs.create(
            CostCreate(
                category_id=seeded_category_id,
                date=date(2026, 3, 2),
                amount=Decimal("5.00"),
                description="Dopo",
                legal_entity_id=closed.id,
            ),
            ADMIN,
        )
    assert refused.value.details["field"] == "legal_entity_id"
    kept = costs.update(
        old.id, CostUpdate(description="Licenza annuale", legal_entity_id=closed.id), ADMIN
    )
    assert (kept.description, kept.legal_entity_id) == ("Licenza annuale", closed.id)
    # `deal_id: null` echoed on a cost that never had a deal is no move: it keeps A.
    echoed = costs.update(old.id, CostUpdate(deal_id=None, description="Licenza"), ADMIN)
    assert echoed.legal_entity_id == closed.id
    # Off the closed legal entity, onto an active one or to shared, is a move and allowed.
    moved = costs.update(old.id, CostUpdate(legal_entity_id=_default(db_session).id), ADMIN)
    assert moved.legal_entity_id == _default(db_session).id
    assert costs.update(old.id, CostUpdate(legal_entity_id=None), ADMIN).legal_entity_id is None


def test_to_new_cost_on_to_deal_of_to_deactivated_legal_entity_is_refused_and_an_old_one_stays(
    db_session: Session, seeded_open_stage_id: UUID, seeded_category_id: UUID
) -> None:
    """The deal's legal entity may close after the deal: a cost already on it is edited in
    place, a new one is refused like a deal or a document would be."""
    closed = _legal_entity(db_session, "rebase")
    customer_id = _customer(db_session, legal_entity_id=closed.id)
    deal = DealService(db_session).create(
        DealCreate(name="Vecchio", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    costs = CostService(db_session)
    old = costs.create(
        CostCreate(
            deal_id=deal.id,
            category_id=seeded_category_id,
            date=date(2026, 3, 1),
            amount=Decimal("10.00"),
            description="Treno",
        ),
        ADMIN,
    )
    closed.active = False
    db_session.flush()
    with pytest.raises(ValidationFailed) as refused:
        costs.create(
            CostCreate(
                deal_id=deal.id,
                category_id=seeded_category_id,
                date=date(2026, 3, 2),
                amount=Decimal("5.00"),
                description="Dopo",
            ),
            ADMIN,
        )
    assert "sposta prima il cliente" in str(refused.value)
    kept = costs.update(old.id, CostUpdate(description="Treno regionale"), ADMIN)
    assert (kept.description, kept.legal_entity_id) == ("Treno regionale", closed.id)
    assert (
        costs.update(old.id, CostUpdate(legal_entity_id=closed.id), ADMIN).legal_entity_id
        == closed.id
    )
    # The same deal sent back, as a client echoing the row does, is no move either.
    echoed = costs.update(old.id, CostUpdate(deal_id=deal.id, legal_entity_id=closed.id), ADMIN)
    assert (echoed.deal_id, echoed.legal_entity_id) == (deal.id, closed.id)
    # A move onto another deal of the closed legal entity is a new assignment, and refused.
    # Built by row: the customer is on the closed legal entity, so the service would refuse.
    sibling = Deal(
        name="Fratello",
        customer_id=customer_id,
        pipeline_stage_id=seeded_open_stage_id,
        legal_entity_id=closed.id,
        probability=10,
    )
    db_session.add(sibling)
    db_session.flush()
    with pytest.raises(ValidationFailed):
        costs.update(old.id, CostUpdate(deal_id=sibling.id), ADMIN)
