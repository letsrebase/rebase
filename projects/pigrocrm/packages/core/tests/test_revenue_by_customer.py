"""Whole-practice, calendar-year client concentration (REB-370):
`AnalyticsRepository.revenue_by_customer` and its embedding in
`AnalyticsService.economic_overview` (§1.5, §5 item 1 of
`docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`).

Buildable off existing `Invoice`/`Customer` data alone, with no dependency on the
ledger milestone: every row here is a raw `Invoice`, never issued through
`InvoiceService`, the same shortcut `test_dashboard_receivables.py` takes for the
same reason -- this is a repository aggregate, not a fiscal-document lifecycle test.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.invoices.models import Invoice

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATORE = Actor(id=None, type="mcp", role="collaboratore")
# A year no other fixture in this suite writes an invoice against.
ANNO = 2031


def _invoice(customer_id: UUID, numero: int, importo: str, *, anno: int = ANNO) -> Invoice:
    return Invoice(
        customer_id=customer_id,
        tipo="fattura",
        stato="emessa",
        anno=anno,
        numero=numero,
        stato_pagamento="da_incassare",
        imponibile=Decimal(importo),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal(importo),
        data_emissione=date(anno, 6, 1),
        tipo_documento="TD01",
        divisa="EUR",
        custom_fields={},
    )


def _corpus(session: Session) -> tuple[UUID, UUID, UUID]:
    """`grande` at 6000.00 over two invoices, `piccolo` at 3000.00 over one --
    `annual_revenue(ANNO)` is exactly 9000.00, and neither share (2/3, 1/3) is a
    round number the largest-row formula (`InvoiceRepository._quota`) would also
    produce, so the two are not accidentally indistinguishable. `assente` has an
    invoice only in the *previous* year: present in the register, absent from this
    method's result, the way `receivables_by_customer` omits a customer with
    nothing outstanding.

    `grande` also carries an annulled invoice for 50000.00, far larger than every
    counted row: a `revenue_by_customer` that forgot `_revenue_filter()` would fail
    both the total and the ranking, not just an edge case nobody would notice.
    """
    grande = Customer(ragione_sociale="Grande S.r.l.", nazione="IT", custom_fields={})
    piccolo = Customer(ragione_sociale="Piccolo S.r.l.", nazione="IT", custom_fields={})
    assente = Customer(ragione_sociale="Assente S.r.l.", nazione="IT", custom_fields={})
    session.add_all([grande, piccolo, assente])
    session.flush()

    annullata = _invoice(grande.id, 4, "50000.00")
    annullata.stato = "annullata"
    annullata.annullata_il = date(ANNO, 6, 2)
    annullata.motivo_annullamento = "storno di prova"

    session.add_all(
        [
            _invoice(grande.id, 1, "4000.00"),
            _invoice(grande.id, 2, "2000.00"),
            _invoice(piccolo.id, 3, "3000.00"),
            annullata,
            _invoice(assente.id, 5, "9999.00", anno=ANNO - 1),
        ]
    )
    session.flush()
    return grande.id, piccolo.id, assente.id


def test_revenue_by_customer_ranks_by_share_of_the_years_whole_revenue(
    db_session: Session,
) -> None:
    grande_id, piccolo_id, assente_id = _corpus(db_session)
    repo = AnalyticsRepository(db_session)

    rows = repo.revenue_by_customer(ANNO)

    # Ranked, largest first -- the acceptance criterion's own word.
    assert [row.customer_id for row in rows] == [grande_id, piccolo_id]
    grande, piccolo = rows
    assert grande.ricavi == Decimal("6000.00")
    assert grande.fatture == 2
    assert piccolo.ricavi == Decimal("3000.00")
    assert piccolo.fatture == 1

    # The share of the *whole year's* revenue (9000.00), never of the largest row:
    # `InvoiceRepository._quota`'s formula on this same data would give the top row
    # exactly 1.0, which is the bug this assertion is written to catch.
    assert abs(grande.quota - 2 / 3) < 1e-9
    assert abs(piccolo.quota - 1 / 3) < 1e-9
    assert grande.quota != 1.0
    assert abs(sum(row.quota for row in rows) - 1.0) < 1e-9

    # A customer with no invoice this year is simply absent from the result.
    assert assente_id not in [row.customer_id for row in rows]


def test_revenue_by_customer_excludes_an_annulled_invoice_from_both_total_and_count(
    db_session: Session,
) -> None:
    grande_id, _piccolo_id, _assente_id = _corpus(db_session)
    repo = AnalyticsRepository(db_session)

    grande = next(row for row in repo.revenue_by_customer(ANNO) if row.customer_id == grande_id)

    # The annulled 50000.00 invoice would dwarf every other row; its absence from
    # both the sum and the count is what proves `_revenue_filter()` was applied.
    assert grande.ricavi == Decimal("6000.00")
    assert grande.fatture == 2


def test_revenue_by_customer_agrees_with_annual_revenue_to_the_cent(db_session: Session) -> None:
    _corpus(db_session)
    repo = AnalyticsRepository(db_session)

    rows = repo.revenue_by_customer(ANNO)

    assert sum((row.ricavi for row in rows), Decimal("0.00")) == repo.annual_revenue(ANNO)


def test_economic_overview_carries_the_same_concentration_for_every_role(
    db_session: Session,
) -> None:
    grande_id, piccolo_id, _assente_id = _corpus(db_session)
    service = AnalyticsService(db_session)

    for actor in (ADMIN, COLLABORATORE):
        overview = service.economic_overview(ANNO, actor)
        assert [row.customer_id for row in overview.concentrazione_clienti] == [
            grande_id,
            piccolo_id,
        ]
        assert overview.concentrazione_clienti[0].ricavi == Decimal("6000.00")
        assert overview.concentrazione_clienti[0].ragione_sociale == "Grande S.r.l."
