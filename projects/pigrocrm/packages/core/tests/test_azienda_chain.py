"""The azienda on customers, deals, contracts, documents and costs (REB-623, spec
2026-10-03 §1.6, §1.7, §8 «Chain»).

A new customer gets the azienda its nation proposes; a deal, a contract, a document and
an invoice take their parent's at creation and keep it; a cost takes its deal's or is
shared; every list and the search narrow to one azienda, and the sum over aziende is the
unfiltered list. The second azienda is written by row, since no route creates one before
milestone 5 (§9).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.calendario.service import CalendarService
from pigrocrm.core.contracts.schemas import ContractCreate, ContractListQuery
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.customers.schemas import CustomerCreate, CustomerListQuery, CustomerUpdate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.schemas import DealCreate, DealListQuery
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents.schemas import DocumentCreate, DocumentListQuery
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.emitter.models import Azienda
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn, InvoiceListQuery
from pigrocrm.core.invoices.service import InvoiceService
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


def _default(session: Session) -> Azienda:
    azienda = AziendaRepository(session).default()
    assert azienda is not None
    return azienda


def _azienda(session: Session, nome: str, nazione: str = "IT", **overrides: object) -> Azienda:
    values: dict[str, object] = {
        "nome": nome,
        "ragione_sociale": f"{nome.title()} S.r.l.",
        "nazione": nazione,
        "indirizzo": "Via Po 1",
        "cap": "10100",
        "comune": "Torino",
        "provincia": "TO",
    }
    values.update(overrides)
    row = Azienda(**values)
    session.add(row)
    session.flush()
    return row


def _customer(session: Session, nome: str = "Acme", **overrides: object) -> UUID:
    values: dict[str, object] = {"ragione_sociale": f"{nome} S.r.l.", "nazione": "IT"}
    values.update(overrides)
    return CustomerService(session).create(CustomerCreate(**values), ADMIN).id  # type: ignore[arg-type]


def _contract(session: Session, customer_id: UUID) -> UUID:
    return (
        ContractService(session)
        .create(
            ContractCreate(
                customer_id=customer_id,
                titolo="Consulenza",
                inizio=date(2026, 1, 1),
                tipo_rinnovo="nessuno",
                preavviso_disdetta_giorni=30,
                cadenza_fatturazione="mensile",
                politica_spese={"tipo": "non_rimborsabile"},
            ),
            ADMIN,
        )
        .id
    )


# --- the proposal -----------------------------------------------------------------


def test_the_nation_proposes_the_one_azienda_that_shares_it(db_session: Session) -> None:
    default = _default(db_session)
    british = _azienda(db_session, "rebase ltd", "GB", provincia=None)
    service = AziendaService(db_session)
    assert service.propose("GB").id == british.id
    assert service.propose("gb").id == british.id
    assert service.propose("IT").id == default.id
    assert service.propose(None).id == default.id


def test_a_foreign_customer_gets_the_one_foreign_azienda_when_its_nation_has_none(
    db_session: Session,
) -> None:
    french = _azienda(db_session, "rebase sarl", "FR", provincia=None)
    assert AziendaService(db_session).propose("DE").id == french.id


def test_the_nation_never_decides_between_two_aziende_that_share_it(db_session: Session) -> None:
    """Two Italian aziende: an Italian customer gets the default, the person picks. Two
    foreign ones and a customer of a third country: the default again."""
    default = _default(db_session)
    _azienda(db_session, "rebase")
    service = AziendaService(db_session)
    assert service.propose("IT").id == default.id
    _azienda(db_session, "rebase ltd", "GB", provincia=None)
    _azienda(db_session, "rebase sarl", "FR", provincia=None)
    assert service.propose("DE").id == default.id
    assert service.propose("GB").id != default.id


def test_an_inactive_azienda_is_never_proposed(db_session: Session) -> None:
    _azienda(db_session, "rebase ltd", "GB", provincia=None, attiva=False)
    assert AziendaService(db_session).propose("GB").id == _default(db_session).id


# --- the customer -----------------------------------------------------------------


def test_a_customer_takes_the_proposal_or_the_azienda_named(db_session: Session) -> None:
    default = _default(db_session)
    british = _azienda(db_session, "rebase ltd", "GB", provincia=None)
    service = CustomerService(db_session)
    proposed = service.create(CustomerCreate(ragione_sociale="Overseas", nazione="GB"), ADMIN)
    assert proposed.azienda_id == british.id
    named = service.create(
        CustomerCreate(ragione_sociale="Oltre", nazione="GB", azienda_id=default.id), ADMIN
    )
    assert named.azienda_id == default.id
    with pytest.raises(NotFound):
        service.create(CustomerCreate(ragione_sociale="X", azienda_id=uuid4()), ADMIN)
    inactive = _azienda(db_session, "chiusa", attiva=False)
    with pytest.raises(ValidationFailed) as refused:
        service.create(CustomerCreate(ragione_sociale="X", azienda_id=inactive.id), ADMIN)
    assert refused.value.details["field"] == "azienda_id"


def test_moving_a_customer_leaves_its_deals_documents_and_invoices_behind(
    db_session: Session, local_storage: LocalFileStorage, seeded_open_stage_id: UUID
) -> None:
    """Spec §1.7, Ivan's rule: «i deal aperti restano dove sono». Only what is created
    after the move follows the customer."""
    default = _default(db_session)
    second = _azienda(db_session, "rebase")
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), ADMIN, azienda_id=second.id
    )
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    customer_id = _customer(db_session)
    deal = DealService(db_session).create(
        DealCreate(nome="Primo", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    contract_id = _contract(db_session, customer_id)
    document = DocumentService(db_session, local_storage).create(
        DocumentCreate(customer_id=customer_id, titolo="Brief"), ADMIN
    )
    invoices = InvoiceService(db_session, local_storage)
    before = invoices.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("100.00"))],
        ),
        ADMIN,
    )
    assert {deal.azienda_id, document.azienda_id, before.azienda_id} == {default.id}
    assert ContractService(db_session).get(contract_id, ADMIN).azienda_id == default.id

    moved = CustomerService(db_session).update(
        customer_id, CustomerUpdate(azienda_id=second.id), ADMIN
    )
    assert moved.azienda_id == second.id
    assert DealService(db_session).get(deal.id, ADMIN).azienda_id == default.id
    assert ContractService(db_session).get(contract_id, ADMIN).azienda_id == default.id
    assert (
        DocumentService(db_session, local_storage).get(document.id, ADMIN).azienda_id == default.id
    )
    assert invoices.get(before.id, ADMIN).azienda_id == default.id

    after_deal = DealService(db_session).create(
        DealCreate(nome="Secondo", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    after_invoice = invoices.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("100.00"))],
        ),
        ADMIN,
    )
    assert (after_deal.azienda_id, after_invoice.azienda_id) == (second.id, second.id)
    # An invoice born from a deal takes the deal's azienda, not the customer's of today.
    from_old_deal = invoices.create(
        InvoiceCreate(
            customer_id=customer_id,
            deal_id=deal.id,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("100.00"))],
        ),
        ADMIN,
    )
    assert from_old_deal.azienda_id == default.id
    # And a document hung on the old deal takes the deal's too.
    on_deal = DocumentService(db_session, local_storage).create(
        DocumentCreate(deal_id=deal.id, titolo="Verbale"), ADMIN
    )
    assert on_deal.azienda_id == default.id


# --- the cost ---------------------------------------------------------------------


def test_a_cost_takes_its_deals_azienda_and_a_shared_one_has_none(
    db_session: Session, seeded_open_stage_id: UUID, seeded_category_id: UUID
) -> None:
    default = _default(db_session)
    second = _azienda(db_session, "rebase")
    customer_id = _customer(db_session, azienda_id=second.id)
    deal = DealService(db_session).create(
        DealCreate(nome="Rebase", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    costs = CostService(db_session)
    on_deal = costs.create(
        CostCreate(
            deal_id=deal.id,
            category_id=seeded_category_id,
            data=date(2026, 3, 1),
            importo=Decimal("10.00"),
            descrizione="Treno",
        ),
        ADMIN,
    )
    assert on_deal.azienda_id == second.id
    shared = costs.create(
        CostCreate(
            category_id=seeded_category_id,
            data=date(2026, 3, 2),
            importo=Decimal("20.00"),
            descrizione="Software",
        ),
        ADMIN,
    )
    assert shared.azienda_id is None
    own = costs.create(
        CostCreate(
            category_id=seeded_category_id,
            data=date(2026, 3, 3),
            importo=Decimal("30.00"),
            descrizione="Licenza",
            azienda_id=default.id,
        ),
        ADMIN,
    )
    assert own.azienda_id == default.id
    with pytest.raises(ValidationFailed) as refused:
        costs.create(
            CostCreate(
                deal_id=deal.id,
                azienda_id=default.id,
                category_id=seeded_category_id,
                data=date(2026, 3, 4),
                importo=Decimal("1.00"),
                descrizione="Sbagliato",
            ),
            ADMIN,
        )
    assert refused.value.details["field"] == "azienda_id"
    # Off the deal, the cost is shared unless an azienda is named; onto it, the deal's.
    assert costs.update(on_deal.id, CostUpdate(deal_id=None), ADMIN).azienda_id is None
    assert costs.update(on_deal.id, CostUpdate(deal_id=deal.id), ADMIN).azienda_id == second.id
    assert (
        costs.update(shared.id, CostUpdate(azienda_id=default.id), ADMIN).azienda_id == default.id
    )

    listed = costs.list(CostListQuery(azienda_id=second.id), ADMIN).items
    assert [c.id for c in listed] == [on_deal.id]
    everything = costs.list(CostListQuery(), ADMIN).items
    assert {c.id for c in everything} >= {on_deal.id, shared.id, own.id}


# --- the lists and the search -----------------------------------------------------


def test_every_list_narrows_to_one_azienda_and_the_sum_is_the_whole(
    db_session: Session,
    local_storage: LocalFileStorage,
    seeded_open_stage_id: UUID,
    time_entry_factory,  # type: ignore[no-untyped-def]  # noqa: ANN001
) -> None:
    default = _default(db_session)
    second = _azienda(db_session, "rebase")
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(codice_regime="RF19"), ADMIN, azienda_id=second.id
    )
    acme = _customer(db_session, "Acme")
    beta = _customer(db_session, "Beta", azienda_id=second.id)
    deals = DealService(db_session)
    deal_a = deals.create(
        DealCreate(nome="Acme deal", customer_id=acme, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    deal_b = deals.create(
        DealCreate(nome="Beta deal", customer_id=beta, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    _contract(db_session, acme)
    _contract(db_session, beta)
    documents = DocumentService(db_session, local_storage)
    documents.create(DocumentCreate(customer_id=acme, titolo="Acme brief"), ADMIN)
    documents.create(DocumentCreate(deal_id=deal_b.id, titolo="Beta brief"), ADMIN)
    invoices = InvoiceService(db_session, local_storage)
    for customer in (acme, beta):
        invoices.create(
            InvoiceCreate(
                customer_id=customer,
                righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1.00"))],
            ),
            ADMIN,
        )
    time_entry_factory(data=date(2026, 3, 2), deal_id=deal_a.id)
    time_entry_factory(data=date(2026, 3, 3), deal_id=deal_b.id)

    def ids(items: list) -> set[UUID]:  # type: ignore[type-arg]
        return {item.id for item in items}

    pairs = [
        (
            CustomerService(db_session),
            lambda a: CustomerListQuery(azienda_id=a),
        ),
        (deals, lambda a: DealListQuery(azienda_id=a)),
        (ContractService(db_session), lambda a: ContractListQuery(azienda_id=a)),
        (documents, lambda a: DocumentListQuery(azienda_id=a)),
        (invoices, lambda a: InvoiceListQuery(azienda_id=a)),
        (TimeEntryService(db_session), lambda a: TimeEntryListQuery(azienda_id=a)),
    ]
    for service, query in pairs:
        on_default = ids(service.list(query(default.id), ADMIN).items)
        on_second = ids(service.list(query(second.id), ADMIN).items)
        everything = ids(service.list(query(None), ADMIN).items)
        assert on_default and on_second, type(service).__name__
        assert on_default.isdisjoint(on_second), type(service).__name__
        assert on_default | on_second == everything, type(service).__name__
        assert ids(service.list(query(uuid4()), ADMIN).items) == set()

    # The calendar's hours follow the deal's azienda.
    calendar = CalendarService(db_session)
    whole = calendar.month("2026-03", ADMIN, user_id=None)
    only_second = calendar.month("2026-03", ADMIN, user_id=None, azienda_id=second.id)
    assert len(whole.giorni) >= 2
    assert [g.giorno for g in only_second.giorni if g.ore] == [date(2026, 3, 3)]

    # And the search: Beta is the second azienda's, Acme is not.
    search = SearchService(db_session)
    everyone = search.search_everything(SearchQuery(termine="brief"), ADMIN)
    narrowed = search.search_everything(SearchQuery(termine="brief", azienda_id=second.id), ADMIN)
    documents_group = next(g for g in narrowed.gruppi if g.entity == "document")
    assert [h.etichetta for h in documents_group.hits] == ["Beta brief"]
    assert next(g for g in everyone.gruppi if g.entity == "document").totale == 2
    customers_group = next(
        g
        for g in search.search_everything(
            SearchQuery(termine="Acme", azienda_id=second.id), ADMIN
        ).gruppi
        if g.entity == "customer"
    )
    assert customers_group.hits == []


def test_a_space_with_one_azienda_sees_nothing_change(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    default = _default(db_session)
    customer = CustomerService(db_session).create(CustomerCreate(ragione_sociale="Solo"), ADMIN)
    deal = DealService(db_session).create(
        DealCreate(nome="Solo", customer_id=customer.id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    assert customer.azienda_id == deal.azienda_id == default.id
    assert AziendaService(db_session).propose("DE").id == default.id


# --- a deactivated azienda --------------------------------------------------------


def test_nothing_new_is_born_under_a_customer_of_a_deactivated_azienda(
    db_session: Session, local_storage: LocalFileStorage, seeded_open_stage_id: UUID
) -> None:
    """Spec §3: the history of a closed azienda stays readable, a deal, a contract, a
    document or a proforma under one of its customers is refused in words."""
    second = _azienda(db_session, "rebase")
    customer_id = _customer(db_session, azienda_id=second.id)
    second.attiva = False
    db_session.flush()
    with pytest.raises(ValidationFailed) as deal:
        DealService(db_session).create(
            DealCreate(
                nome="Dopo", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id
            ),
            ADMIN,
        )
    assert deal.value.details["field"] == "customer_id"
    assert "sposta prima il cliente" in str(deal.value)
    with pytest.raises(ValidationFailed):
        _contract(db_session, customer_id)
    with pytest.raises(ValidationFailed):
        DocumentService(db_session, local_storage).create(
            DocumentCreate(customer_id=customer_id, titolo="Brief"), ADMIN
        )
    with pytest.raises(ValidationFailed):
        InvoiceService(db_session, local_storage).create(
            InvoiceCreate(
                customer_id=customer_id,
                righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1.00"))],
            ),
            ADMIN,
        )
    # Moved to an active azienda, the same customer works again.
    CustomerService(db_session).update(
        customer_id, CustomerUpdate(azienda_id=_default(db_session).id), ADMIN
    )
    assert (
        DealService(db_session)
        .create(
            DealCreate(
                nome="Dopo", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id
            ),
            ADMIN,
        )
        .azienda_id
        == _default(db_session).id
    )


def test_a_cost_of_an_archived_deal_can_still_be_edited(
    db_session: Session, seeded_open_stage_id: UUID, seeded_category_id: UUID
) -> None:
    """The SPA sends the seeded `azienda_id` back on every edit, so the deal it reads it
    against may be archived since: a domain answer, never an assertion."""
    customer_id = _customer(db_session)
    deal = DealService(db_session).create(
        DealCreate(nome="Vecchio", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    costs = CostService(db_session)
    cost = costs.create(
        CostCreate(
            deal_id=deal.id,
            category_id=seeded_category_id,
            data=date(2026, 3, 1),
            importo=Decimal("10.00"),
            descrizione="Treno",
        ),
        ADMIN,
    )
    DealService(db_session).soft_delete(deal.id, ADMIN)
    edited = costs.update(
        cost.id, CostUpdate(descrizione="Treno regionale", azienda_id=deal.azienda_id), ADMIN
    )
    assert (edited.descrizione, edited.azienda_id) == ("Treno regionale", deal.azienda_id)


def test_a_new_general_cost_is_refused_on_a_deactivated_azienda_and_an_old_one_stays(
    db_session: Session, seeded_category_id: UUID
) -> None:
    """Spec §3 for the one record that names its azienda directly: nothing new is filed
    under a closed azienda, but a cost already there can still be edited in place."""
    closed = _azienda(db_session, "rebase")
    costs = CostService(db_session)
    old = costs.create(
        CostCreate(
            category_id=seeded_category_id,
            data=date(2026, 3, 1),
            importo=Decimal("10.00"),
            descrizione="Licenza",
            azienda_id=closed.id,
        ),
        ADMIN,
    )
    closed.attiva = False
    db_session.flush()
    with pytest.raises(ValidationFailed) as refused:
        costs.create(
            CostCreate(
                category_id=seeded_category_id,
                data=date(2026, 3, 2),
                importo=Decimal("5.00"),
                descrizione="Dopo",
                azienda_id=closed.id,
            ),
            ADMIN,
        )
    assert refused.value.details["field"] == "azienda_id"
    kept = costs.update(
        old.id, CostUpdate(descrizione="Licenza annuale", azienda_id=closed.id), ADMIN
    )
    assert (kept.descrizione, kept.azienda_id) == ("Licenza annuale", closed.id)
    # Off the closed azienda, onto an active one or to shared, is a move and allowed.
    moved = costs.update(old.id, CostUpdate(azienda_id=_default(db_session).id), ADMIN)
    assert moved.azienda_id == _default(db_session).id
    assert costs.update(old.id, CostUpdate(azienda_id=None), ADMIN).azienda_id is None


def test_a_new_cost_on_a_deal_of_a_deactivated_azienda_is_refused_and_an_old_one_stays(
    db_session: Session, seeded_open_stage_id: UUID, seeded_category_id: UUID
) -> None:
    """The deal's azienda may close after the deal: a cost already on it is edited in
    place, a new one is refused like a deal or a document would be."""
    closed = _azienda(db_session, "rebase")
    customer_id = _customer(db_session, azienda_id=closed.id)
    deal = DealService(db_session).create(
        DealCreate(nome="Vecchio", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id),
        ADMIN,
    )
    costs = CostService(db_session)
    old = costs.create(
        CostCreate(
            deal_id=deal.id,
            category_id=seeded_category_id,
            data=date(2026, 3, 1),
            importo=Decimal("10.00"),
            descrizione="Treno",
        ),
        ADMIN,
    )
    closed.attiva = False
    db_session.flush()
    with pytest.raises(ValidationFailed) as refused:
        costs.create(
            CostCreate(
                deal_id=deal.id,
                category_id=seeded_category_id,
                data=date(2026, 3, 2),
                importo=Decimal("5.00"),
                descrizione="Dopo",
            ),
            ADMIN,
        )
    assert "sposta prima il cliente" in str(refused.value)
    kept = costs.update(old.id, CostUpdate(descrizione="Treno regionale"), ADMIN)
    assert (kept.descrizione, kept.azienda_id) == ("Treno regionale", closed.id)
    assert costs.update(old.id, CostUpdate(azienda_id=closed.id), ADMIN).azienda_id == closed.id
    # The same deal sent back, as a client echoing the row does, is no move either.
    echoed = costs.update(old.id, CostUpdate(deal_id=deal.id, azienda_id=closed.id), ADMIN)
    assert (echoed.deal_id, echoed.azienda_id) == (deal.id, closed.id)
    # A move onto another deal of the closed azienda is a new assignment, and refused.
    # Built by row: the customer is on the closed azienda, so the service would refuse.
    sibling = Deal(
        nome="Fratello",
        customer_id=customer_id,
        pipeline_stage_id=seeded_open_stage_id,
        azienda_id=closed.id,
        probabilita=10,
    )
    db_session.add(sibling)
    db_session.flush()
    with pytest.raises(ValidationFailed):
        costs.update(old.id, CostUpdate(deal_id=sibling.id), ADMIN)
