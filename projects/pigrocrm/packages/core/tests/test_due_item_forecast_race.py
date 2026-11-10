"""The job and a draft or an issue that bill the same period leave one item (REB-997, § 1.2).

Every writer that decides on a contract's forecasts takes the contract row first, so the draft
that bills a period, or the issue of that draft, queues behind a job that is materialising it
and then finds the forecast to replace. Without the lock the draft would read nothing, commit,
and the job's forecast would be added to the document's own: the same money twice.

A committed fixture and two real connections, because the transactional `db_session` cannot
host two transactions.
"""

import datetime as dt
import threading
import time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.fee_billing import FeeBillingService
from pigrocrm.core.contracts.fee_schemas import FeeBilled, FeeBillRequest
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.due_items.forecast import (
    CONTRACT_OCCURRENCE,
    SALES_DRAFT,
    ForecastService,
    forecast_key,
)
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.local import LocalFileStorage

HUMAN = Actor(id=None, type="user", role="admin")
TODAY = today_local()


def _configure(session: Session) -> None:
    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="12345678901",
            tax_code="RSSMRA80A01H501U",
            address="Via Roma 1",
            postal_code="20100",
            city="Milano",
            province="MI",
            country="IT",
            certified_email="studio@pec.it",
        ),
        HUMAN,
    )
    FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), HUMAN)


def _cleanup(factory, customer_id: UUID | None) -> None:  # type: ignore[no-untyped-def]
    with factory() as cleaner:
        if customer_id is not None:
            invoices = select(Invoice.id).where(Invoice.customer_id == customer_id)
            contracts = select(Contract.id).where(Contract.customer_id == customer_id)
            cleaner.execute(
                text("DELETE FROM due_items WHERE organization_id = :id"), {"id": customer_id}
            )
            cleaner.execute(
                text(
                    "DELETE FROM invoice_lines WHERE invoice_id IN (SELECT id FROM invoices "
                    "WHERE customer_id = :id)"
                ),
                {"id": customer_id},
            )
            cleaner.execute(
                text(
                    "DELETE FROM activities WHERE entity_type IN ('invoice', 'contract') AND "
                    "entity_id IN (SELECT id FROM invoices WHERE customer_id = :id "
                    "UNION SELECT id FROM contracts WHERE customer_id = :id)"
                ),
                {"id": customer_id},
            )
            cleaner.execute(Invoice.__table__.delete().where(Invoice.customer_id == customer_id))
            cleaner.execute(RateCard.__table__.delete().where(RateCard.contract_id.in_(contracts)))
            cleaner.execute(Contract.__table__.delete().where(Contract.customer_id == customer_id))
            del invoices
        cleaner.execute(text("DELETE FROM invoice_counters"))
        cleaner.execute(text("DELETE FROM fiscal_profile"))
        cleaner.execute(text("DELETE FROM legal_entities WHERE NOT is_default"))
        if customer_id is not None:
            cleaner.execute(
                Organization.__table__.delete().where(Organization.__table__.c.id == customer_id)
            )
        cleaner.commit()


def _seed(factory) -> tuple[UUID, UUID, UUID, UUID]:  # type: ignore[no-untyped-def]
    """A customer, an active contract with a monthly fee card starting today, and the profiles an
    invoice needs, committed. Answers the customer, contract, card and legal entity ids."""
    with factory() as setup:
        _configure(setup)
        customer = Organization(
            legal_name="Acme S.r.l.",
            vat_number="98765432109",
            sdi_code="ABCDEFG",
            address="Corso Italia 5",
            postal_code="00100",
            city="Roma",
            province="RM",
            country="IT",
        )
        setup.add(customer)
        setup.flush()
        contract = Contract(
            customer_id=customer.id,
            title="Consulenza",
            start=TODAY - dt.timedelta(days=30),
            renewal_type="none",
            termination_notice_days=30,
            billing_cadence="monthly",
            expense_policy={"kind": "not_reimbursable"},
            status="active",
        )
        setup.add(contract)
        setup.flush()
        card = RateCard(
            contract_id=contract.id,
            valid_from=TODAY,
            type="fixed_recurring",
            amount=Decimal("1000.00"),
            unit="month",
            delivery_period="monthly",
        )
        setup.add(card)
        setup.flush()
        ids = customer.id, contract.id, card.id, contract.legal_entity_id
        setup.commit()
    return ids


def _fee_draft(factory, storage, customer_id, card_id, entity_id) -> UUID:  # type: ignore[no-untyped-def]
    with factory() as own:
        return (
            InvoiceService(own, storage)
            .create(
                InvoiceCreate(
                    customer_id=customer_id,
                    lines=[
                        InvoiceLineIn(
                            description="Canone",
                            unit_price=Decimal("1000.00"),
                            rate_card_id=card_id,
                            period_start=TODAY,
                        )
                    ],
                ),
                HUMAN,
                legal_entity_id=entity_id,
            )
            .id
        )


def _wait_while_the_job_holds_the_lock(job: Session, work) -> None:  # type: ignore[no-untyped-def]
    """Runs `work` in a thread, shows it waits for the job's lock, then lets the job commit."""
    finished = threading.Event()

    def run() -> None:
        work()
        finished.set()

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(1.0)
    assert not finished.is_set(), "the writer did not wait for the job's lock on the contract"
    job.commit()
    job.close()
    thread.join(timeout=20)
    assert finished.is_set()


def test_a_draft_billing_a_period_waits_for_the_job_and_then_replaces_its_forecast(
    db_engine: Engine, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    factory = session_factory(db_engine)
    storage = LocalFileStorage(tmp_path / "documents")
    customer_id: UUID | None = None
    try:
        customer_id, contract_id, card_id, entity_id = _seed(factory)
        key = forecast_key(CONTRACT_OCCURRENCE, card_id, TODAY)
        job = factory()
        ForecastService(job).refresh_contract(contract_id)  # holds the contract's lock

        _wait_while_the_job_holds_the_lock(
            job, lambda: _fee_draft(factory, storage, customer_id, card_id, entity_id)
        )

        with factory() as check:
            live = list(check.scalars(select(DueItem).where(DueItem.state == "forecast")))
            assert key not in {item.forecast_key for item in live}
            assert len([item for item in live if item.source_kind == SALES_DRAFT]) == 1
            dates = [item.due_date for item in live if item.amount == Decimal("1000.00")]
            assert len(dates) == len(set(dates)), "one period was counted twice"
    finally:
        _cleanup(factory, customer_id)


def test_the_issue_of_a_draft_waits_for_the_job_and_leaves_one_item_for_the_period(
    db_engine: Engine, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    factory = session_factory(db_engine)
    storage = LocalFileStorage(tmp_path / "documents")
    customer_id: UUID | None = None
    try:
        customer_id, contract_id, card_id, entity_id = _seed(factory)
        key = forecast_key(CONTRACT_OCCURRENCE, card_id, TODAY)
        draft_id = _fee_draft(factory, storage, customer_id, card_id, entity_id)
        job = factory()
        ForecastService(job).refresh_contract(contract_id)  # holds the contract's lock

        def issue() -> None:
            with factory() as own:
                InvoiceService(own, storage).issue(draft_id, InvoiceIssue(), HUMAN)

        _wait_while_the_job_holds_the_lock(job, issue)

        with factory() as check:
            items = list(
                check.scalars(
                    select(DueItem).where(
                        DueItem.organization_id == customer_id,
                        DueItem.state.in_(("forecast", "expected")),
                        DueItem.amount == Decimal("1000.00"),
                    )
                )
            )
            period = [
                item
                for item in items
                if item.forecast_key == key or item.source_kind in ("sales_invoice", SALES_DRAFT)
            ]
            assert [(item.source_kind, item.state) for item in period] == [
                ("sales_invoice", "expected")
            ]
    finally:
        _cleanup(factory, customer_id)


def test_two_calls_billing_one_occurrence_queue_and_the_second_answers_the_first_draft(
    db_engine: Engine, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """A double click (REB-1032): both calls wait for the contract's lock, the first makes the
    draft and the second finds it, so the occurrence is on one document and the answer says
    which call made it."""
    factory = session_factory(db_engine)
    storage = LocalFileStorage(tmp_path / "documents")
    customer_id: UUID | None = None
    try:
        customer_id, contract_id, card_id, _entity_id = _seed(factory)
        job = factory()
        ForecastService(job).refresh_contract(contract_id)  # holds the contract's lock
        answers: list[FeeBilled] = []

        def bill() -> None:
            with factory() as own:
                answers.append(
                    FeeBillingService(own, storage).bill(
                        contract_id, FeeBillRequest(rate_card_id=card_id, period_start=TODAY), HUMAN
                    )
                )

        threads = [threading.Thread(target=bill) for _ in range(2)]
        for thread in threads:
            thread.start()
        time.sleep(1.0)
        assert answers == [], "a call did not wait for the contract's lock"
        job.commit()
        job.close()
        for thread in threads:
            thread.join(timeout=30)

        assert sorted(answer.created for answer in answers) == [False, True]
        assert len({answer.invoice.id for answer in answers}) == 1
        with factory() as check:
            assert (
                check.scalar(
                    select(func.count())
                    .select_from(Invoice)
                    .where(Invoice.customer_id == customer_id)
                )
                == 1
            )
    finally:
        _cleanup(factory, customer_id)
