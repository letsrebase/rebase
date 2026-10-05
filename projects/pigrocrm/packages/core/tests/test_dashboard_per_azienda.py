"""«Tutte» is the sum of the aziende, and the taxes are one azienda's at a time
(REB-630, spec 2026-10-03 §1.9, §8 «Dashboard»).

Two aziende, each with one customer, one open deal, one paid and one unpaid invoice and
some hours this week; a won deal, a per-deal cost and a shared cost on top. Every figure
below is a literal derived by hand from those rows, in the reading
`test_dashboard_economic.py` gives for doing so: an expectation recomputed the way the
code computes it agrees with a wrong implementation. The default azienda is a
forfettario on the Italian pack, the second is a foreign company on `non-it`, so the
estimate and the ceiling have one azienda they apply to and one they do not.

Committed sessions of their own, like every dashboard file: `DashboardService` sets the
isolation level as its first statement and Postgres refuses that once a transaction
has begun, so `db_session` cannot be used.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from fakes.azienda_fixtures import committed_default_azienda, remove_azienda
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import CeilingSimulationQuery, PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import OperationalDashboard, PeriodoQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.emitter.repository import LegalEntityRepository
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.models import Cost, CostCategory, TimeEntry

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=uuid7(), type="user", role="readonly")
_PREFIX = "DUEAZ"
OGGI = today_local()
ANNO = OGGI.year
# One day of the year every invoice is dated and paid on: inside `ANNO` for the yearly
# sums, whichever month the suite runs in.
GIORNO = date(ANNO, 3, 10)
PERIODO = PeriodoQuery(da=date(ANNO, 3, 1), a=date(ANNO, 3, 31))

# The figures, by hand, from the rows the fixture writes.
P_INCASSATO = Decimal("1000.00")  # the studio's paid invoice
P_DA_INCASSARE = Decimal("200.00")  # the studio's unpaid one, already overdue
P_RICAVI = Decimal("1200.00")  # both issued: what the estimate reads
E_INCASSATO = Decimal("5000.00")
E_DA_INCASSARE = Decimal("300.00")  # due in a month, not overdue
COSTO_CONDIVISO = Decimal("50.00")  # no deal, no azienda: «tutte» alone
COSTO_E = Decimal("20.00")  # on the foreign deal, so the foreign azienda's
ORE_P = Decimal("2.00")
ORE_E = Decimal("3.00")


class Corpus(NamedTuple):
    engine: Engine
    studio: UUID
    estero: UUID


def _require_empty(session: Session) -> None:
    for model in (Invoice, TimeEntry, Deal, Cost):
        leftovers = session.execute(select(func.count(model.id))).scalar_one()
        assert leftovers == 0, (
            f"{leftovers} committed {model.__tablename__} row(s) were already present; the "
            "sums on these dashboards have no scope but the azienda, so leftovers change them."
        )


@pytest.fixture
def corpus(db_engine: Engine) -> Iterator[Corpus]:
    factory = session_factory(db_engine)
    with factory() as session:
        _require_empty(session)
        inserted = committed_default_azienda(session)
        studio = LegalEntityRepository(session).default()
        assert studio is not None
        estero = LegalEntity(
            nome=f"{_PREFIX} ltd", ragione_sociale=f"{_PREFIX} Ltd", nazione="GB", predefinita=False
        )
        aperto = PipelineStage(
            nome=f"{_PREFIX} aperto", posizione=0, probabilita_default=20, tipo="open"
        )
        vinto = PipelineStage(
            nome=f"{_PREFIX} vinto", posizione=1, probabilita_default=100, tipo="won"
        )
        user = User(
            email=f"{_PREFIX.lower()}-{uuid7()}@example.test",
            password_hash="x",
            nome=f"{_PREFIX} operatore",
            ruolo="collaboratore",
        )
        session.add_all([estero, aperto, vinto, user])
        session.flush()
        cliente_p = Customer(
            ragione_sociale=f"{_PREFIX} Cliente studio",
            nazione="IT",
            custom_fields={},
            azienda_id=studio.id,
        )
        cliente_e = Customer(
            ragione_sociale=f"{_PREFIX} Cliente estero",
            nazione="GB",
            custom_fields={},
            azienda_id=estero.id,
        )
        session.add_all([cliente_p, cliente_e])
        session.flush()

        def _deal(nome: str, customer: Customer, stage: PipelineStage, valore: str) -> Deal:
            deal = Deal(
                nome=f"{_PREFIX} {nome}",
                customer_id=customer.id,
                azienda_id=customer.azienda_id,
                pipeline_stage_id=stage.id,
                probabilita=stage.probabilita_default,
                valore_previsto=Decimal(valore),
                chiuso_il=GIORNO if stage.tipo == "won" else None,
                custom_fields={},
            )
            session.add(deal)
            session.flush()
            return deal

        def _invoice(
            customer: Customer,
            deal: Deal,
            *,
            numero: int,
            importo: Decimal,
            pagata: bool,
            scadenza: date | None,
        ) -> None:
            session.add(
                Invoice(
                    customer_id=customer.id,
                    azienda_id=customer.azienda_id,
                    deal_id=deal.id,
                    tipo="fattura",
                    stato="emessa",
                    anno=ANNO,
                    numero=numero,
                    stato_pagamento="incassato" if pagata else "da_incassare",
                    data_incasso=GIORNO if pagata else None,
                    imponibile=importo,
                    imposta=Decimal("0.00"),
                    bollo=Decimal("0.00"),
                    totale=importo,
                    data_emissione=GIORNO,
                    data_scadenza=scadenza,
                    custom_fields={},
                )
            )

        def _hours(deal: Deal, ore: Decimal) -> None:
            session.add(
                TimeEntry(
                    deal_id=deal.id,
                    user_id=user.id,
                    data=OGGI,
                    ore=ore,
                    descrizione="Lavorazione",
                    fatturabile=True,
                    tariffa_applicata=Decimal("50.000000"),
                    tariffa_origine="manuale",
                    costo_applicato=None,
                    costo_origine="assente",
                    custom_fields={},
                )
            )

        deal_p = _deal("studio aperto", cliente_p, aperto, "1000.00")
        deal_e = _deal("estero aperto", cliente_e, aperto, "3000.00")
        _deal("estero vinto", cliente_e, vinto, "4000.00")
        _invoice(cliente_p, deal_p, numero=1, importo=P_INCASSATO, pagata=True, scadenza=None)
        _invoice(
            cliente_p,
            deal_p,
            numero=2,
            importo=P_DA_INCASSARE,
            pagata=False,
            scadenza=OGGI - timedelta(days=10),
        )
        _invoice(cliente_e, deal_e, numero=1, importo=E_INCASSATO, pagata=True, scadenza=None)
        _invoice(
            cliente_e,
            deal_e,
            numero=2,
            importo=E_DA_INCASSARE,
            pagata=False,
            scadenza=OGGI + timedelta(days=30),
        )
        _hours(deal_p, ORE_P)
        _hours(deal_e, ORE_E)
        # Written by row, not through `CostCategoryService`: a service commits, and a
        # fixture that commits halfway leaves rows behind when a later step fails.
        categoria = CostCategory(nome=f"{_PREFIX} categoria")
        session.add(categoria)
        session.flush()
        session.add_all(
            [
                Cost(
                    deal_id=None,
                    azienda_id=None,
                    category_id=categoria.id,
                    data=GIORNO,
                    importo=COSTO_CONDIVISO,
                    descrizione=f"{_PREFIX} condiviso",
                    custom_fields={},
                ),
                Cost(
                    deal_id=deal_e.id,
                    azienda_id=estero.id,
                    category_id=categoria.id,
                    data=GIORNO,
                    importo=COSTO_E,
                    descrizione=f"{_PREFIX} estero",
                    custom_fields={},
                ),
            ]
        )
        session.flush()
        # By row, like the category above: the service commits, and a fixture that
        # commits halfway leaves rows behind when a later step fails. A foreign profile
        # with a zero rate needs a natura (REB-619); a British one charges VAT instead,
        # which is the shape REB-621's panel saves.
        session.add_all(
            [
                FiscalProfile(
                    **FiscalProfileUpsert(codice_regime="RF19").model_dump(), azienda_id=studio.id
                ),
                FiscalProfile(
                    **FiscalProfileUpsert(
                        pack_id="non-it", aliquota_iva_default=Decimal("20.00")
                    ).model_dump(),
                    azienda_id=estero.id,
                ),
            ]
        )
        session.commit()
        ids = Corpus(db_engine, studio.id, estero.id)
    try:
        yield ids
    finally:
        with factory() as session:
            clienti = select(Customer.id).where(Customer.ragione_sociale.like(f"{_PREFIX} %"))
            session.execute(
                delete(TimeEntry).where(
                    TimeEntry.deal_id.in_(select(Deal.id).where(Deal.nome.like(f"{_PREFIX} %")))
                )
            )
            session.execute(delete(Cost).where(Cost.descrizione.like(f"{_PREFIX} %")))
            session.execute(delete(CostCategory).where(CostCategory.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(clienti)))
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
            session.execute(delete(User).where(User.nome == f"{_PREFIX} operatore"))
            session.execute(
                delete(FiscalProfile).where(FiscalProfile.azienda_id.in_((ids.studio, ids.estero)))
            )
            session.execute(delete(LegalEntity).where(LegalEntity.id == ids.estero))
            remove_azienda(session, inserted)
            session.commit()


def _operational(engine: Engine, azienda_id: UUID | None) -> OperationalDashboard:
    with session_factory(engine)() as session:
        settings = Settings(_env_file=None, concentrazione_soglia_preferita=0.5)  # type: ignore[call-arg]
        return DashboardService(session, settings).get_operational_dashboard(READONLY, azienda_id)


def _signal(result: OperationalDashboard, codice: str) -> int:
    return next(s.conteggio for s in result.segnali if s.codice == codice)


# --- the cash adds up, the shared cost only in «tutte» ------------------------------


def test_the_cash_overview_in_tutte_is_the_sum_of_the_aziende(corpus: Corpus) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        tutte = service.cash_overview(ANNO, READONLY)
        studio = service.cash_overview(ANNO, READONLY, azienda_id=corpus.studio)
        estero = service.cash_overview(ANNO, READONLY, azienda_id=corpus.estero)
    assert (studio.incassato, studio.da_incassare) == (P_INCASSATO, P_DA_INCASSARE)
    assert (estero.incassato, estero.da_incassare) == (E_INCASSATO, E_DA_INCASSARE)
    assert tutte.incassato == studio.incassato + estero.incassato == Decimal("6000.00")
    assert tutte.da_incassare == studio.da_incassare + estero.da_incassare == Decimal("500.00")
    # The shared cost is nobody's: it is in «tutte» and in neither azienda (§1.7).
    assert (studio.costi, estero.costi) == (Decimal("0.00"), COSTO_E)
    assert tutte.costi == studio.costi + estero.costi + COSTO_CONDIVISO == Decimal("70.00")
    assert (tutte.azienda_id, studio.azienda_id, estero.azienda_id) == (
        None,
        corpus.studio,
        corpus.estero,
    )


def test_the_period_pnl_keeps_the_shared_cost_out_of_each_azienda(corpus: Corpus) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        tutte = service.period_pnl(PeriodPnlQuery(da=PERIODO.da, a=PERIODO.a), READONLY)  # type: ignore[arg-type]
        estero = service.period_pnl(
            PeriodPnlQuery(da=PERIODO.da, a=PERIODO.a, azienda_id=corpus.estero),  # type: ignore[arg-type]
            READONLY,
        )
    assert tutte.spese_generali == COSTO_CONDIVISO
    assert estero.spese_generali == Decimal("0.00")
    assert estero.azienda_id == corpus.estero
    # The foreign deal's own cost is per deal, not general, in both readings.
    assert tutte.in_corso.costi_diretti == COSTO_E == estero.in_corso.costi_diretti


# --- the taxes are one azienda's --------------------------------------------------


def test_the_estimate_needs_the_azienda_on_a_two_azienda_space(corpus: Corpus) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        with pytest.raises(ValidationFailed) as refused:
            service.get_fiscal_estimate(ANNO, ADMIN)
        assert refused.value.details["field"] == "azienda_id"
        studio = service.get_fiscal_estimate(ANNO, ADMIN, azienda_id=corpus.studio)
    assert studio.azienda_id == corpus.studio
    # Every issued invoice of the studio, paid or not, and none of the foreign company's.
    assert studio.ricavi == P_RICAVI


def test_the_overview_in_tutte_carries_no_estimate_and_names_each_share_s_azienda(
    corpus: Corpus,
) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        tutte = service.economic_overview(ANNO, ADMIN)
        studio = service.economic_overview(ANNO, ADMIN, azienda_id=corpus.studio)
    assert tutte.azienda_id is None
    assert tutte.fiscale is None and tutte.fiscale_proiettato is None
    assert tutte.netto_effettivo is None
    # One customer per azienda, each the whole of its own azienda's revenue: a share
    # of the space would read 1/6 and 5/6 and hide exactly that.
    quote = {
        (row.azienda_id, row.ragione_sociale): row.quota for row in tutte.concentrazione_clienti
    }
    assert quote == {
        (corpus.studio, f"{_PREFIX} Cliente studio"): 1.0,
        (corpus.estero, f"{_PREFIX} Cliente estero"): 1.0,
    }
    assert studio.azienda_id == corpus.studio
    assert studio.fiscale is not None and studio.fiscale.azienda_id == corpus.studio
    assert studio.fiscale.ricavi == P_INCASSATO
    assert [row.ragione_sociale for row in studio.concentrazione_clienti] == [
        f"{_PREFIX} Cliente studio"
    ]


def test_the_ceiling_is_one_azienda_s_and_a_foreign_pack_has_none(corpus: Corpus) -> None:
    with session_factory(corpus.engine)() as session:
        service = AnalyticsService(session)
        with pytest.raises(ValidationFailed) as refused:
            service.ceiling_headroom(ANNO, READONLY)
        assert refused.value.details["field"] == "azienda_id"
        studio = service.ceiling_headroom(ANNO, READONLY, azienda_id=corpus.studio)
        estero = service.ceiling_headroom(ANNO, READONLY, azienda_id=corpus.estero)
        simulated = service.simulate_ceiling(
            ANNO,
            CeilingSimulationQuery(valore_preventivato=Decimal("100.00")),
            READONLY,
            azienda_id=corpus.studio,
        )
    assert studio.azienda_id == corpus.studio
    # The studio's paid receipts alone: the foreign company's 5000 never reach its ceiling.
    assert {s.ricavi for s in studio.soglie} == {P_INCASSATO}
    assert estero.pack_id == "non-it" and estero.soglie == []
    assert simulated.azienda_id == corpus.studio
    assert {s.ricavi_simulati for s in simulated.soglie} == {Decimal("1100.00")}


# --- the four dashboards take the azienda as one more predicate --------------------


def test_the_commercial_dashboard_keeps_every_stage_and_sums_the_pipeline(
    corpus: Corpus,
) -> None:
    def _run(azienda_id: UUID | None) -> dict[str, tuple[int, Decimal]]:
        with session_factory(corpus.engine)() as session:
            result = DashboardService(session).get_commercial_dashboard(
                PeriodoQuery(da=PERIODO.da, a=PERIODO.a, azienda_id=azienda_id), READONLY
            )
        assert result.azienda_id == azienda_id
        return {
            row.stage_nome: (row.numero, row.valore_totale)
            for row in result.pipeline
            if row.stage_nome.startswith(_PREFIX)
        }

    tutte, studio, estero = _run(None), _run(corpus.studio), _run(corpus.estero)
    assert tutte == {
        f"{_PREFIX} aperto": (2, Decimal("4000.00")),
        f"{_PREFIX} vinto": (1, Decimal("4000.00")),
    }
    # A stage with none of the studio's deals is still a row, at zero: the outer join
    # keeps it, which a `where` on the deal's azienda would silently have undone.
    assert studio == {
        f"{_PREFIX} aperto": (1, Decimal("1000.00")),
        f"{_PREFIX} vinto": (0, Decimal("0.00")),
    }
    assert estero == {
        f"{_PREFIX} aperto": (1, Decimal("3000.00")),
        f"{_PREFIX} vinto": (1, Decimal("4000.00")),
    }


def test_the_closures_and_the_receivables_follow_the_azienda(corpus: Corpus) -> None:
    def _closures(azienda_id: UUID | None) -> int:
        with session_factory(corpus.engine)() as session:
            return (
                DashboardService(session)
                .get_commercial_dashboard(
                    PeriodoQuery(da=PERIODO.da, a=PERIODO.a, azienda_id=azienda_id), READONLY
                )
                .chiusure.vinti
            )

    def _receivables(azienda_id: UUID | None) -> tuple[Decimal, Decimal]:
        with session_factory(corpus.engine)() as session:
            result = DashboardService(session).get_receivables_dashboard(READONLY, azienda_id)
        assert result.azienda_id == azienda_id
        scaduto = next(f.importo for f in result.fasce if f.codice == "scaduto")
        return result.totale, scaduto

    assert (_closures(None), _closures(corpus.studio), _closures(corpus.estero)) == (1, 0, 1)
    assert _receivables(corpus.studio) == (P_DA_INCASSARE, P_DA_INCASSARE)
    assert _receivables(corpus.estero) == (E_DA_INCASSARE, Decimal("0.00"))
    assert _receivables(None) == (Decimal("500.00"), P_DA_INCASSARE)


def test_the_operational_dashboard_narrows_the_week_the_backlog_and_the_signals(
    corpus: Corpus,
) -> None:
    tutte = _operational(corpus.engine, None)
    studio = _operational(corpus.engine, corpus.studio)
    estero = _operational(corpus.engine, corpus.estero)
    assert (studio.settimana.ore_totali, estero.settimana.ore_totali) == (ORE_P, ORE_E)
    assert tutte.settimana.ore_totali == ORE_P + ORE_E
    assert (studio.arretrato.valore_maturato, estero.arretrato.valore_maturato) == (
        Decimal("100.00"),
        Decimal("150.00"),
    )
    assert tutte.arretrato.valore_maturato == Decimal("250.00")
    assert [_signal(r, "scaduto_non_incassato") for r in (tutte, studio, estero)] == [1, 1, 0]
    # Each customer is the whole of its own azienda, so above a 50% threshold in both;
    # «tutte» counts the two of them, never a space-wide 1/6 that would hide the studio's.
    assert [_signal(r, "concentrazione_sopra_soglia") for r in (tutte, studio, estero)] == [
        2,
        1,
        1,
    ]
    assert (tutte.azienda_id, studio.azienda_id, estero.azienda_id) == (
        None,
        corpus.studio,
        corpus.estero,
    )


# --- the two cases the review of REB-630 asked for ------------------------------------


def test_a_deactivated_azienda_s_cash_never_reaches_the_active_one_s_estimate(
    corpus: Corpus,
) -> None:
    """With one active azienda the overview in «tutte» still adds up a deactivated
    azienda's receipts, which must not reach the active one's coefficients: the estimate
    and the two nets are read off the active azienda's own cash (spec §1.9)."""
    factory = session_factory(corpus.engine)
    with factory() as session:
        LegalEntityService(session).deactivate(corpus.estero, ADMIN)
    try:
        with factory() as session:
            tutte = AnalyticsService(session).economic_overview(ANNO, ADMIN)
        # The cash is every azienda's, the deactivated one included.
        assert tutte.cassa.incassato == P_INCASSATO + E_INCASSATO
        assert tutte.fiscale is not None and tutte.fiscale.azienda_id == corpus.studio
        assert tutte.fiscale.ricavi == P_INCASSATO
        assert tutte.fiscale.totale_dovuto is not None
        # The net is the studio's own lordo less its own taxes, not the page's lordo.
        assert tutte.netto_effettivo == P_INCASSATO - tutte.fiscale.totale_dovuto
    finally:
        with factory() as session:
            row = session.get(LegalEntity, corpus.estero)
            assert row is not None
            row.attiva = True
            session.commit()


def test_the_concentration_signal_counts_a_customer_billed_by_two_aziende_once(
    corpus: Corpus,
) -> None:
    """A customer over the threshold in both aziende is two rows of the concentration
    table, one share per azienda, and one person to talk to on the signal."""
    factory = session_factory(corpus.engine)
    with factory() as session:
        cliente = session.execute(
            select(Customer).where(Customer.ragione_sociale == f"{_PREFIX} Cliente studio")
        ).scalar_one()
        # The studio's customer, billed by the foreign company for more than everything
        # the foreign company billed its own customer: over 50% on both sides.
        extra = Invoice(
            customer_id=cliente.id,
            azienda_id=corpus.estero,
            deal_id=None,
            tipo="fattura",
            stato="emessa",
            anno=ANNO,
            numero=3,
            stato_pagamento="incassato",
            data_incasso=GIORNO,
            imponibile=Decimal("6000.00"),
            imposta=Decimal("0.00"),
            bollo=Decimal("0.00"),
            totale=Decimal("6000.00"),
            data_emissione=GIORNO,
            custom_fields={},
        )
        session.add(extra)
        session.commit()
        extra_id = extra.id
    try:
        with factory() as session:
            rows = AnalyticsRepository(session).revenue_by_customer(ANNO)
            doppio = [r for r in rows if r.customer_id == cliente.id]
            assert {r.azienda_id for r in doppio} == {corpus.studio, corpus.estero}
            assert all(r.quota > 0.5 for r in doppio)
            # The foreign company's own customer fell under the threshold: 5000 of 11000.
            assert AnalyticsRepository(session).count_over_concentration_threshold(ANNO, 0.5) == 1
        tutte = _operational(corpus.engine, None)
        assert _signal(tutte, "concentrazione_sopra_soglia") == 1
    finally:
        with factory() as session:
            session.execute(delete(Invoice).where(Invoice.id == extra_id))
            session.commit()


def test_the_invoiced_not_won_signal_follows_the_deal_s_azienda_like_its_list(
    corpus: Corpus,
) -> None:
    """The card counts deals and its drill-through lists deals by `Deal.azienda_id`, so
    an invoice that sits on another azienda than its deal's (an import can do that)
    must not make the deal count under the invoice's azienda."""
    factory = session_factory(corpus.engine)
    with factory() as session:
        deal = session.execute(
            select(Deal).where(Deal.nome == f"{_PREFIX} studio aperto")
        ).scalar_one()
        stray = Invoice(
            customer_id=deal.customer_id,
            azienda_id=corpus.estero,
            deal_id=deal.id,
            tipo="fattura",
            stato="emessa",
            anno=ANNO,
            numero=4,
            stato_pagamento="da_incassare",
            imponibile=Decimal("10.00"),
            imposta=Decimal("0.00"),
            bollo=Decimal("0.00"),
            totale=Decimal("10.00"),
            data_emissione=GIORNO,
            custom_fields={},
        )
        session.add(stray)
        session.commit()
        stray_id = stray.id
    try:
        studio = _operational(corpus.engine, corpus.studio)
        estero = _operational(corpus.engine, corpus.estero)
        # The studio's open deal is invoiced either way; the foreign company's open deal
        # is invoiced too, by its own paid invoice. The stray invoice adds no deal to
        # the foreign company's count: the deal it sits on is the studio's.
        assert _signal(studio, "fatturato_non_vinto") == 1
        assert _signal(estero, "fatturato_non_vinto") == 1
        with session_factory(corpus.engine)() as session:
            counted = DealRepository(session).list(
                DealListQuery(fatturato_non_vinto=True, azienda_id=corpus.estero, limit=50)
            )
        assert [d.nome for d in counted] == [f"{_PREFIX} estero aperto"]
    finally:
        with factory() as session:
            session.execute(delete(Invoice).where(Invoice.id == stray_id))
            session.commit()
