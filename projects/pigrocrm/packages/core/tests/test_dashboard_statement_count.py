"""The economic dashboard reads a number of statements that does not grow with the deals.

It used to run one `deal_summary` per deal in the window (an N+1 that showed as 2,000
identical statements on a seed of 1,000 deals). The count is taken through the real entry
point, `DashboardService.get_economic_dashboard`, with a listener on the engine, so any
per-deal or per-customer loop on that path shows up as a difference between a small and a
large corpus. Committed sessions of its own, for the reason `test_dashboard_economic.py`
gives: the dashboard opens a `REPEATABLE READ` snapshot as its first statement.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fakes.azienda_fixtures import committed_default_azienda, remove_azienda
from sqlalchemy import Engine, delete, event, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import PeriodoQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage

READONLY = Actor(id=uuid7(), type="user", role="readonly")
_PREFIX = "STMT"
_DA = date(2026, 3, 1)
_A = date(2026, 3, 31)


def _seed(session: Session, deals: int) -> None:
    open_stage = PipelineStage(
        nome=f"{_PREFIX} aperto", posizione=0, probabilita_default=20, tipo="open"
    )
    won_stage = PipelineStage(
        nome=f"{_PREFIX} vinto", posizione=1, probabilita_default=100, tipo="won"
    )
    session.add_all([open_stage, won_stage])
    session.flush()
    for index in range(deals):
        customer = Customer(
            ragione_sociale=f"{_PREFIX} Cliente {index}", nazione="IT", custom_fields={}
        )
        session.add(customer)
        session.flush()
        stage = open_stage if index % 2 else won_stage
        deal = Deal(
            nome=f"{_PREFIX} deal {index}",
            customer_id=customer.id,
            pipeline_stage_id=stage.id,
            probabilita=stage.probabilita_default,
            custom_fields={},
        )
        session.add(deal)
        session.flush()
        session.add(
            Invoice(
                customer_id=customer.id,
                deal_id=deal.id,
                tipo="fattura",
                stato="emessa",
                stato_pagamento="da_incassare",
                imponibile=Decimal("100.00"),
                imposta=Decimal("0.00"),
                bollo=Decimal("0.00"),
                totale=Decimal("100.00"),
                data_emissione=date(2026, 3, 10),
                data_scadenza=date(2026, 3, 20),
                tipo_documento="TD01",
                divisa="EUR",
                custom_fields={},
            )
        )
    session.commit()


def _clean(session: Session) -> None:
    session.execute(
        delete(Invoice).where(
            Invoice.deal_id.in_(select(Deal.id).where(Deal.nome.like(f"{_PREFIX} %")))
        )
    )
    session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
    session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
    session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
    session.commit()


@pytest.fixture
def azienda(db_engine: Engine) -> Iterator[None]:
    factory = session_factory(db_engine)
    with factory() as session:
        azienda_id = committed_default_azienda(session)
    try:
        yield
    finally:
        with factory() as session:
            _clean(session)
            remove_azienda(session, azienda_id)
            session.commit()


def _statements_for(engine: Engine, deals: int) -> int:
    factory = session_factory(engine)
    with factory() as session:
        _seed(session, deals)
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        with factory() as session:
            result = DashboardService(session).get_economic_dashboard(
                PeriodoQuery(da=_DA, a=_A), READONLY
            )
    finally:
        event.remove(engine, "before_cursor_execute", count)
        with factory() as session:
            _clean(session)
    assert result.pnl.chiusi.deal + result.pnl.in_corso.deal == deals
    return len(statements)


@pytest.mark.usefixtures("azienda")
def test_the_economic_dashboard_reads_the_same_number_of_statements_for_any_number_of_deals(
    db_engine: Engine,
) -> None:
    few = _statements_for(db_engine, 3)
    many = _statements_for(db_engine, 30)
    assert few == many
