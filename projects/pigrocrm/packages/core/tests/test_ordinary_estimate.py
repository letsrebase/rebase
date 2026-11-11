"""REB-1072: an ordinary-regime person's INPS, IRPEF and surcharges, estimated from the year's
cash basis.

The arithmetic is proved on `estimate_ordinary_taxes` with hand-built bases, against numbers worked
by hand from the law's own tables: the Agenzia delle Entrate's example («Aliquote e calcolo
dell'Irpef»: 13,700 euro at 50,000), the regional surcharge of Emilia-Romagna and the municipal one
of Albinea, a real municipality of the Finanze's 2026 list. The service tests then prove the
withholdings, the declared figure outranking the estimate, the frozen year and that the REST
payload's source, `get_fiscal_estimate`, and the economic overview answer one figure.
"""

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from itertools import count
from uuid import uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.fiscal.basis_schemas import BasisCosts, YearBasis
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.irpef_2026 import IRPEF_2026
from pigrocrm.core.fiscal.law_inps import INPS_2026
from pigrocrm.core.fiscal.law_irpef import RateBracket
from pigrocrm.core.fiscal.models import FiscalEstimateReading, FiscalProfile
from pigrocrm.core.fiscal.ordinary_estimate import (
    InpsFacts,
    OrdinaryEstimate,
    OrdinaryTax,
    estimate_ordinary_taxes,
)
from pigrocrm.core.fiscal.pack import IT_STANDARD_PACK
from pigrocrm.core.fiscal.payment_data import WITHHOLDING_CREDIT
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureSet, FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.fiscal.withholdings_suffered import withholdings_suffered
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY
from pigrocrm.core.places.locate import Place, locate

D = Decimal
ADMIN = Actor(id=uuid4(), type="user", role="admin")
LAW = IT_STANDARD_PACK.law_for(2026)
_NUMBERS = count(1)

# A person in the gestione separata who is insured elsewhere (24%), and the facts of a trader.
GS_COVERED = InpsFacts("gestione_separata", None, True, None)
GS_FULL = InpsFacts("gestione_separata", None, False, None)
TRADER = InpsFacts("artigiani_commercianti", "commercianti", None, False)

ALBINEA = locate("Albinea", "RE", "IT")  # Emilia-Romagna, municipality A162
MILANO = locate("Milano", "MI", "IT")  # no 2026 deliberation on the Finanze's list of 11 November
NOWHERE = locate(None, None, "IT")


def _basis(revenue: str, purchases: str = "0.00", year: int = 2026) -> YearBasis:
    costs = D(purchases)
    return YearBasis(
        year=year,
        legal_entity_id=uuid4(),
        basis="cash",
        period_from=dt.date(year, 1, 1),
        period_to=dt.date(year, 12, 31),
        label=f"Incassato nel {year}, secondo il principio di cassa",
        wording="art. 54",
        source="TUIR art. 54 c. 1",
        revenue=D(revenue),
        costs=BasisCosts(purchases=costs, staff=ZERO_MONEY, director=ZERO_MONEY, total=costs),
        left_out=[],
        assumptions=[],
    )


def _estimate(
    basis: YearBasis,
    *,
    facts: InpsFacts = GS_COVERED,
    place: Place = ALBINEA,
    province: str | None = "RE",
    withholdings: Decimal | None = ZERO_MONEY,
) -> OrdinaryEstimate:
    law = IT_STANDARD_PACK.law_for(basis.year)
    assert law is not None
    estimate = estimate_ordinary_taxes(
        basis,
        IT_STANDARD_PACK,
        law,
        inps_facts=facts,
        place=place,
        province=province,
        withholdings=withholdings,
    )
    assert estimate is not None
    return estimate


def _part(tax: OrdinaryTax, code: str) -> Decimal | None:
    (line,) = [part for part in tax.parts if part.code == code]
    return line.amount


# --- the worked example --------------------------------------------------------------------


def test_the_agenzias_example_13700_at_50000_with_a_regional_and_a_municipal_surcharge() -> None:
    """A professional insured elsewhere (24%) collects 65,789.47: the INPS is 15,789.47 (65,789.47
    x 24%, rounded to the cent) and the IRPEF base is exactly 50,000, on which the Agenzia's page
    «Aliquote e calcolo dell'Irpef» says the gross tax is 13,700. Emilia-Romagna's surcharge by
    slice (1.33% to 15,000, 1.93% to 28,000, 2.78% to 50,000: 199.50 + 250.90 + 611.60) is
    1,062.00 and Albinea's (0.55% to 28,000, 0.70% to 50,000, above 12,000 the whole income:
    154.00 + 154.00) is 308.00."""
    estimate = _estimate(_basis("65789.47"))

    assert estimate.inps.amount == D("15789.47")
    assert estimate.inps.base == D("65789.47") and estimate.inps.rate == D("24.00")
    assert estimate.income.taxable_income == D("50000.00")
    assert [(line.code, line.amount) for line in estimate.income.lines] == [
        ("revenue", D("65789.47")),
        ("costs", D("0.00")),
        ("inps_deducted", D("-15789.47")),
    ]
    irpef = estimate.irpef
    assert irpef.base == D("50000.00")
    assert _part(irpef, "gross_irpef") == D("13700.00")
    assert _part(irpef, "regional_surcharge") == D("1062.00")
    assert _part(irpef, "municipal_surcharge") == D("308.00")
    assert _part(irpef, "withholdings") == D("0.00")
    assert irpef.amount == D("13700.00") and irpef.credit == D("0.00")
    assert irpef.with_surcharges == D("15070.00")
    assert (irpef.region, irpef.cadastral_code) == ("08", "A162")
    assert estimate.due_total == D("30859.47")
    assert estimate.missing_parameters == [] and irpef.missing == [] and estimate.inps.missing == []


def test_costs_come_off_before_the_contributions_and_the_contributions_before_the_tax() -> None:
    """60,000 collected and 10,000 paid: the income is 50,000, the gestione separata owes
    13,035.00 on it (26.07%), the base is 36,965.00 and the tax is by slice on that: 6,440.00 to
    28,000 plus 33% of 8,965 = 2,958.45. Surcharges: 154.00 + 12,000... on 36,965 Emilia-Romagna
    is 199.50 + 250.90 + 249.23 and Albinea 154.00 + 62.76."""
    estimate = _estimate(_basis("60000.00", "10000.00"), facts=GS_FULL)

    assert estimate.income.professional_income == D("50000.00")
    assert estimate.inps.amount == D("13035.00")
    assert estimate.income.taxable_income == D("36965.00")
    assert _part(estimate.irpef, "gross_irpef") == D("9398.45")
    assert _part(estimate.irpef, "regional_surcharge") == D("699.63")
    assert _part(estimate.irpef, "municipal_surcharge") == D("216.76")
    assert estimate.irpef.amount == D("9398.45")
    assert estimate.irpef.with_surcharges == D("10314.84")
    assert estimate.due_total == D("23349.84")


def test_the_withholdings_come_off_the_tax_and_the_excess_is_a_credit_never_a_negative_tax() -> (
    None
):
    basis = _basis("65789.47")
    under = _estimate(basis, withholdings=D("5000.00"))
    assert _part(under.irpef, "withholdings") == D("-5000.00")
    assert under.irpef.amount == D("8700.00") and under.irpef.credit == D("0.00")
    assert under.irpef.with_surcharges == D("10070.00")
    # The withholdings are credited against the IRPEF alone: the surcharges are still owed.
    over = _estimate(basis, withholdings=D("20000.00"))
    assert over.irpef.amount == D("0.00") and over.irpef.credit == D("6300.00")
    assert over.irpef.with_surcharges == D("1370.00")


def test_the_estimate_says_what_it_does_not_apply_and_where_its_law_was_read() -> None:
    irpef = _estimate(_basis("65789.47")).irpef
    text = " ".join(irpef.assumptions)
    assert "articolo 13 del TUIR" in text and "per eccesso" in text
    assert "art. 50 c. 2 D.Lgs. 446/1997" in text
    sources = " ".join(irpef.sources)
    assert "art. 11 c. 1" in sources and "Addizionale regionale" in sources
    assert "elenchi generali 2026" in sources and "art. 22 c. 1 lett. c)" in sources
    income = _estimate(_basis("65789.47")).income
    assert any("art. 10 c. 1 lett. e)" in source for source in income.sources)
    assert any("circolare n. 8" in source for source in _estimate(_basis("1000.00")).inps.sources)


# --- a missing fact gives no figure and names what is missing ----------------------------


def test_without_an_inps_category_there_is_no_inps_and_no_irpef() -> None:
    """A base computed as if no contribution were deducted would overstate the tax, which is a
    guess: no IRPEF either, and the sentence says why."""
    estimate = _estimate(_basis("65789.47"), facts=InpsFacts(None, None, None, None))
    assert estimate.missing_parameters == ["inps_category"]
    assert estimate.inps.amount is None and "categoria INPS" in estimate.inps.missing[0]
    assert estimate.income.taxable_income is None
    assert estimate.irpef.amount is None and estimate.irpef.parts == []
    assert "senza i contributi" in estimate.irpef.missing[0]
    assert estimate.due_total is None


def test_each_inps_fact_the_law_reads_is_named_when_it_is_missing() -> None:
    covered = _estimate(_basis("40000.00"), facts=InpsFacts("gestione_separata", None, None, None))
    assert covered.missing_parameters == ["inps_covered_elsewhere"]
    assert covered.inps.amount is None and covered.irpef.amount is None

    no_trade = _estimate(
        _basis("40000.00"), facts=InpsFacts("artigiani_commercianti", None, None, None)
    )
    assert no_trade.missing_parameters == ["inps_trade"]


def test_the_contributions_before_1996_are_asked_only_where_the_ceiling_decides() -> None:
    """The older ceiling is 93,707 euro and the other 122,295: below the older one both answer
    the same and the question is not asked; above it the figure differs and it is."""
    unstated = InpsFacts("artigiani_commercianti", "artigiani", None, None)
    low = _estimate(_basis("60000.00"), facts=unstated)
    assert low.missing_parameters == [] and low.inps.amount is not None
    high = _estimate(_basis("110000.00"), facts=unstated)
    assert high.missing_parameters == ["inps_before_1996"] and high.inps.amount is None
    stated = _estimate(
        _basis("110000.00"), facts=InpsFacts("artigiani_commercianti", "artigiani", None, True)
    )
    assert stated.inps.amount is not None and stated.irpef.amount is not None


def test_an_artisan_owes_the_fixed_part_on_the_minimum_even_with_no_income() -> None:
    """The contribution on the minimum income and the maternity one are owed whatever the income
    (INPS circolare 14/2026), and are deducted only up to the income there is."""
    estimate = _estimate(_basis("0.00"), facts=TRADER)
    owed = INPS_2026.contributions("artigiani_commercianti", D("0"), trade="commercianti")
    assert estimate.inps.amount == owed.total and owed.fixed > 0
    assert [part.code for part in estimate.inps.parts] == ["fixed", "percentage"]
    assert estimate.income.lines[-1].amount == D("0.00")
    assert estimate.income.taxable_income == D("0.00")


def test_an_income_above_the_ceiling_shows_the_part_left_out() -> None:
    estimate = _estimate(_basis("130000.00"), facts=GS_FULL)
    assert estimate.inps.base == INPS_2026.gestione_separata.ceiling
    assert [line.code for line in estimate.inps.lines] == ["income", "above_ceiling"]
    assert estimate.inps.lines[1].amount == D("122295.00") - D("130000.00")


def test_a_year_with_no_inps_law_has_no_figure_not_the_year_before() -> None:
    """The pack's 2026 law governs later years, but INPS re-sets every figure each year and a
    year it has no circular for is not given the previous one's."""
    estimate = _estimate(_basis("40000.00", year=2027))
    assert estimate.inps.amount is None and "2027" in estimate.inps.missing[0]
    assert estimate.irpef.amount is None


def test_a_year_with_no_irpef_entry_has_no_irpef_and_says_so_but_keeps_its_inps() -> None:
    """IRPEF is a yearly figure (REB-1083): 2027 has the carried-forward law but no brackets, so
    its IRPEF is not 2026's. The INPS has its own year and stands."""
    pack = replace(IT_STANDARD_PACK, inps=(INPS_2026, replace(INPS_2026, year=2027)))
    law = pack.law_for(2027)
    assert law is not None
    estimate = estimate_ordinary_taxes(
        _basis("65789.47", year=2027),
        pack,
        law,
        inps_facts=GS_COVERED,
        place=ALBINEA,
        province="RE",
        withholdings=ZERO_MONEY,
    )
    assert estimate is not None
    assert estimate.inps.amount == D("15789.47")
    assert estimate.irpef.amount is None and estimate.irpef.with_surcharges is None
    assert estimate.irpef.parts == [] and estimate.due_total is None
    assert len(estimate.irpef.missing) == 1
    assert "IRPEF del 2027" in estimate.irpef.missing[0]


def test_two_irpef_years_coexist_and_each_reads_its_own() -> None:
    later = replace(
        IRPEF_2026, year=2027, brackets=(RateBracket(None, D("20")),), brackets_source="a test"
    )
    pack = replace(
        IT_STANDARD_PACK,
        irpef=(IRPEF_2026, later),
        inps=(INPS_2026, replace(INPS_2026, year=2027)),
    )
    assert pack.irpef_for(2026) is IRPEF_2026 and pack.irpef_for(2027) is later
    assert pack.irpef_for(2028) is None and pack.irpef_for(2025) is None
    gross: dict[int, Decimal | None] = {}
    for year in (2026, 2027):
        law = pack.law_for(year)
        assert law is not None
        estimate = estimate_ordinary_taxes(
            _basis("65789.47", year=year),
            pack,
            law,
            inps_facts=GS_COVERED,
            place=ALBINEA,
            province="RE",
            withholdings=ZERO_MONEY,
        )
        assert estimate is not None
        gross[year] = _part(estimate.irpef, "gross_irpef")
    assert gross == {2026: D("13700.00"), 2027: D("10000.00")}


def test_no_address_gives_no_surcharge_and_names_the_address() -> None:
    estimate = _estimate(_basis("65789.47"), place=NOWHERE, province=None)
    assert estimate.inps.amount == D("15789.47")
    assert _part(estimate.irpef, "gross_irpef") == D("13700.00")
    assert _part(estimate.irpef, "regional_surcharge") is None
    assert _part(estimate.irpef, "municipal_surcharge") is None
    # The IRPEF itself is known; only the total with its surcharges is not.
    assert estimate.irpef.amount == D("13700.00") and estimate.irpef.with_surcharges is None
    assert estimate.due_total is None
    assert any("regione" in sentence for sentence in estimate.irpef.missing)
    assert any("comune" in sentence for sentence in estimate.irpef.missing)


def test_a_foreign_address_is_said_not_to_be_italian() -> None:
    estimate = _estimate(_basis("65789.47"), place=locate("Lyon", None, "FR"), province=None)
    assert estimate.irpef.with_surcharges is None
    assert all("non è in Italia" in sentence for sentence in estimate.irpef.missing)


def test_a_city_that_is_no_municipality_gives_the_region_but_not_the_municipality() -> None:
    place = locate("Cittadella Inesistente", "RE", "IT")
    estimate = _estimate(_basis("65789.47"), place=place, province="RE")
    assert _part(estimate.irpef, "regional_surcharge") == D("1062.00")
    assert _part(estimate.irpef, "municipal_surcharge") is None
    assert estimate.irpef.with_surcharges is None
    assert "Manca il comune" in estimate.irpef.missing[0]


def test_the_2026_rate_not_yet_published_gives_no_municipal_figure_and_says_why() -> None:
    """Milano has no 2026 deliberation on the Finanze's list read on 11 November: until 20
    December the list shows the same «0*» for a municipality that has not deliberated yet. The
    gross tax and the regional surcharge are shown, the municipal one is not, so there is no total,
    and the previous year's rate is not borrowed."""
    estimate = _estimate(_basis("65789.47"), place=MILANO, province="MI")
    irpef = estimate.irpef
    assert _part(irpef, "gross_irpef") == D("13700.00")
    assert _part(irpef, "regional_surcharge") == IRPEF_2026.regional_surcharge("03", D("50000"))
    assert _part(irpef, "municipal_surcharge") is None
    assert irpef.amount == D("13700.00") and irpef.with_surcharges is None
    (sentence,) = irpef.missing
    assert "Milano" in sentence and "20 dicembre" in sentence and "2026-11-11" in sentence
    assert irpef.cadastral_code == "F205"
    # The 2025 list carries Milano at 0.8% above a 23,000 exemption; none of it is used.
    assert estimate.due_total is None


def test_a_municipality_whose_published_rates_are_not_brackets_is_not_estimated() -> None:
    estimate = _estimate(_basis("65789.47"), place=locate("Airuno", "LC", "IT"), province="LC")
    assert _part(estimate.irpef, "municipal_surcharge") is None
    assert "non sono leggibili" in estimate.irpef.missing[0]


def test_a_pack_with_no_withholding_rule_gives_no_net_figure() -> None:
    estimate = _estimate(_basis("65789.47"), withholdings=None)
    assert _part(estimate.irpef, "withholdings") is None
    assert estimate.irpef.amount is None and estimate.irpef.with_surcharges is None
    assert "ritenute" in estimate.irpef.missing[0]


# --- no tax due, no surcharge ----------------------------------------------------------------


def test_a_loss_owes_nothing_and_asks_for_no_address() -> None:
    """The surcharges are due only if the IRPEF is (art. 50 c. 2 D.Lgs. 446/1997; art. 1 c. 4 D.Lgs.
    360/1998): with no tax there is nothing to ask the place for, and the withholdings suffered
    are a credit."""
    estimate = _estimate(
        _basis("5000.00", "8000.00"), place=NOWHERE, province=None, withholdings=D("300.00")
    )
    assert estimate.income.loss and estimate.income.professional_income == D("-3000.00")
    assert estimate.inps.amount == D("0.00")
    assert estimate.income.taxable_income == D("0.00")
    assert _part(estimate.irpef, "gross_irpef") == D("0.00")
    assert _part(estimate.irpef, "regional_surcharge") == D("0.00")
    assert _part(estimate.irpef, "municipal_surcharge") == D("0.00")
    assert estimate.irpef.amount == D("0.00") and estimate.irpef.credit == D("300.00")
    assert estimate.irpef.with_surcharges == D("0.00")
    assert estimate.irpef.missing == []
    assert any("zero" in sentence for sentence in estimate.irpef.assumptions)


def test_a_regional_exemption_tested_on_the_total_income_is_not_granted_on_the_base() -> None:
    """Valle d'Aosta's 15,000 euro threshold is tested on the «reddito complessivo», before the
    INPS is deducted: 18,000 collected leaves a base of 13,680 but a total income above the
    threshold, so the surcharge is due, and the smaller figure is not given."""
    aosta = locate("Aosta", "AO", "IT")
    estimate = _estimate(_basis("18000.00"), place=aosta, province="AO")
    assert estimate.income.taxable_income == D("13680.00")
    assert _part(estimate.irpef, "regional_surcharge") is None
    assert any("reddito complessivo" in sentence for sentence in estimate.irpef.missing)
    assert estimate.irpef.amount is not None and estimate.irpef.with_surcharges is None


def test_the_autonomous_provinces_are_told_apart_by_province() -> None:
    bolzano = _estimate(
        _basis("65789.47"), place=locate("Bolzano", "BZ", "IT"), province="BZ"
    ).irpef
    assert bolzano.region == "BZ"
    assert _part(bolzano, "regional_surcharge") == IRPEF_2026.regional_surcharge("BZ", D("50000"))
    # Trentino-Alto Adige is one ISTAT region and has no surcharge of its own: with no province
    # of the two it is no region.
    nowhere = _estimate(_basis("65789.47"), place=Place(status="unknown", region_code="04"))
    assert nowhere.irpef.region is None and nowhere.irpef.with_surcharges is None


# --- the service: withholdings, declared figures, frozen years, three surfaces ---------------


def _person(
    session: Session,
    *,
    city: str | None = "Albinea",
    province: str | None = "RE",
    **profile: object,
) -> LegalEntity:
    entity = LegalEntity(
        name=f"person {uuid4().hex[:6]}",
        legal_name="Rossi Mario",
        country="IT",
        city=city,
        province=province,
    )
    session.add(entity)
    session.flush()
    facts: dict[str, object] = {
        "inps_category": "gestione_separata",
        "inps_covered_elsewhere": True,
    }
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-standard",
            regime_code="RF01",
            **{**facts, **profile},  # type: ignore[arg-type]
        )
    )
    session.flush()
    return entity


def _invoice(
    session: Session,
    entity: LegalEntity,
    taxable: str,
    collected: dt.date | None,
    *,
    withholding: str = "0.00",
) -> Invoice:
    """An issued invoice with no VAT, owed its total less the withholding the customer keeps."""
    amount, kept = D(taxable), D(withholding)
    invoice = Invoice(
        legal_entity_id=entity.id,
        customer_id=make_customer(session).id,
        type="invoice",
        status="issued",
        year=2026,
        number=next(_NUMBERS) + 200_000,
        issue_date=dt.date(2026, 2, 1),
        due_date=dt.date(2026, 3, 3),
        taxable_amount=amount,
        vat_amount=D("0.00"),
        stamp_duty=D("0.00"),
        total=amount,
        withholding_amount=kept,
        withholding_rate=D("20.00") if kept else None,
        net_receivable=amount - kept,
        payment_status="unpaid" if collected is None else "paid",
        payment_date=collected,
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            line_number=1,
            description="Consulenza",
            quantity=D("1.000000"),
            unit_price=amount,
            total_price=amount,
            vat_rate=D("0.00"),
            vat_nature="N2.2",
        )
    )
    session.flush()
    return invoice


def _item_of(session: Session, invoice: Invoice) -> DueItem:
    return session.query(DueItem).filter_by(source_id=invoice.id).one()


def test_the_withholdings_are_the_shares_of_what_each_settlement_paid(
    db_session: Session,
) -> None:
    entity = _person(db_session)
    other = _person(db_session)
    full = _invoice(db_session, entity, "10000.00", dt.date(2026, 3, 10), withholding="2000.00")
    # Half of the 8,000 owed is collected: half of its 2,000 withholding is operated.
    half = _invoice(db_session, entity, "10000.00", None, withholding="2000.00")
    db_session.add(
        DueItemSettlement(
            due_item_id=_item_of(db_session, half).id,
            legal_entity_id=entity.id,
            amount=D("4000.00"),
            settled_on=dt.date(2026, 6, 1),
            recorded_by_type="system",
        )
    )
    # Collected the year after, reversed, and another legal entity's: none of them counts.
    _invoice(db_session, entity, "5000.00", dt.date(2027, 1, 9), withholding="1000.00")
    reversed_ = _invoice(db_session, entity, "5000.00", dt.date(2026, 4, 1), withholding="1000.00")
    db_session.query(DueItemSettlement).filter_by(
        due_item_id=_item_of(db_session, reversed_).id
    ).update({"reversed_at": dt.datetime(2026, 4, 2, tzinfo=dt.UTC), "reversed_by_type": "system"})
    _invoice(db_session, other, "5000.00", dt.date(2026, 4, 1), withholding="1000.00")
    _invoice(db_session, entity, "3000.00", dt.date(2026, 5, 5))
    assert full.withholding_amount == D("2000.00")

    assert withholdings_suffered(db_session, entity.id, 2026, WITHHOLDING_CREDIT) == D("3000.00")
    assert withholdings_suffered(db_session, entity.id, 2027, WITHHOLDING_CREDIT) == D("1000.00")
    assert withholdings_suffered(db_session, entity.id, 2025, WITHHOLDING_CREDIT) == D("0.00")


def test_the_service_estimate_nets_the_withholdings_its_own_invoices_suffered(
    db_session: Session,
) -> None:
    entity = _person(db_session)
    _invoice(db_session, entity, "65789.47", dt.date(2026, 5, 20), withholding="13157.89")
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        2026, ADMIN, legal_entity_id=entity.id
    )
    person = estimate.person
    assert person is not None
    assert estimate.basis is not None and estimate.basis.revenue == D("65789.47")
    assert (person.inps.amount, person.income.taxable_income) == (D("15789.47"), D("50000.00"))
    assert _part(person.irpef, "withholdings") == D("-13157.89")
    assert person.irpef.amount == D("542.11")  # 13,700.00 - 13,157.89
    assert person.irpef.with_surcharges == D("1912.11")  # plus 1,062.00 and 308.00
    # The payload's flat-rate lines stay empty, and the shared lines carry the person's figures.
    assert (estimate.taxable_amount, estimate.substitute_tax) == (None, None)
    assert estimate.contributions == person.inps.amount
    assert estimate.due_total == D("17701.58")
    assert estimate.missing_parameters == [] and estimate.warning.startswith("Stima indicativa")
    assert (estimate.pack_id, estimate.pack_version) == ("it-standard", "1")


def test_a_missing_inps_fact_is_a_missing_parameter_of_the_payload(db_session: Session) -> None:
    entity = _person(db_session, inps_covered_elsewhere=None)
    _invoice(db_session, entity, "10000.00", dt.date(2026, 5, 20))
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        2026, ADMIN, legal_entity_id=entity.id
    )
    assert estimate.missing_parameters == ["inps_covered_elsewhere"]
    assert estimate.contributions is None and estimate.due_total is None
    assert estimate.readings is not None
    assert [(line.tax, line.estimable, line.estimate) for line in estimate.readings.lines] == [
        ("irpef", True, None),
        ("inps", True, None),
    ]


def test_the_readings_carry_the_two_estimates_and_a_declared_figure_outranks_one(
    db_session: Session,
) -> None:
    entity = _person(db_session)
    _invoice(db_session, entity, "65789.47", dt.date(2026, 5, 20))
    service = AnalyticsService(db_session)
    before = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=entity.id)
    assert before.readings is not None and before.person is not None
    lines = {line.tax: line for line in before.readings.lines}
    assert list(lines) == ["irpef", "inps"]
    assert lines["irpef"].estimate == D("13700.00") == before.person.irpef.amount
    assert lines["inps"].estimate == D("15789.47") == before.person.inps.amount
    assert lines["irpef"].counts == "estimate" and before.readings.totals.estimate == D("29489.47")

    DeclaredTaxFigureService(db_session).set_declared_tax_figure(
        DeclaredTaxFigureSet(tax="irpef", year=2026, tax_due=D("14000.00")),
        ADMIN,
        legal_entity_id=entity.id,
    )
    after = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=entity.id)
    assert after.reading == "declared" and after.readings is not None
    line = {line.tax: line for line in after.readings.lines}["irpef"]
    assert (line.estimate, line.declared, line.counts) == (D("13700.00"), D("14000.00"), "declared")
    assert after.person == before.person


def test_the_estimate_the_economic_overview_and_the_readings_answer_one_figure(
    db_session: Session,
) -> None:
    """The REST route, the MCP tool and the economic overview all read `get_fiscal_estimate`
    (REB-1071): the overview's tax is that estimate, whichever reading the charts are on."""
    entity = _person(db_session)
    _invoice(db_session, entity, "65789.47", dt.date(2026, 5, 20), withholding="5000.00")
    service = AnalyticsService(db_session)
    estimate = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=entity.id)
    for reading in ("accrual", "collection"):
        overview = service.economic_overview(2026, ADMIN, reading, entity.id)  # type: ignore[arg-type]
        assert overview.tax is not None
        assert overview.tax.model_dump() == estimate.model_dump()
        assert overview.projected_tax is None and overview.actual_net is None


def test_a_what_if_revenue_has_no_person_estimate(db_session: Session) -> None:
    entity = _person(db_session)
    what_if = AnalyticsService(db_session).get_fiscal_estimate(
        2026, ADMIN, revenue=D("50000.00"), legal_entity_id=entity.id
    )
    assert what_if.person is None and what_if.basis is None and what_if.due_total is None


def test_a_year_moved_off_the_ordinary_pack_keeps_its_figures_and_the_place_they_used(
    db_session: Session,
) -> None:
    entity = _person(db_session)
    _invoice(db_session, entity, "65789.47", dt.date(2026, 5, 20))
    service = AnalyticsService(db_session)
    before = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=entity.id)
    assert before.person is not None

    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="non-it", default_vat_nature="N2.1"),
        ADMIN,
        legal_entity_id=entity.id,
    )
    after = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=entity.id)
    assert after.frozen and after.person == before.person
    assert after.pack_id == "it-standard"
    reading = db_session.execute(select(FiscalEstimateReading)).scalar_one()
    assert reading.parameters["irpef_region"] == "08"
    assert reading.parameters["municipal_cadastral_code"] == "A162"
    assert reading.parameters["inps_category"] == "gestione_separata"


def test_an_entity_that_left_the_flat_rate_keeps_that_year_though_the_ordinary_pack_computes_it(
    db_session: Session,
) -> None:
    """A year belongs to the regime it was lived in: the ordinary pack's law covers 2026 too, but
    a flat-rate year frozen on the move is served as it was, not recomputed as an IRPEF year."""
    entity = LegalEntity(name="mover", legal_name="Mover", country="IT")
    db_session.add(entity)
    db_session.flush()
    db_session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-flat-rate",
            regime_code="RF19",
            profitability_coefficient=D("67.00"),
            substitute_tax_rate=D("5.00"),
            social_security_rate=D("26.07"),
        )
    )
    db_session.flush()
    _invoice(db_session, entity, "10000.00", dt.date(2026, 5, 20))
    service = AnalyticsService(db_session)
    flat = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=entity.id)
    assert flat.substitute_tax is not None

    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="it-standard", regime_code="RF01"),
        ADMIN,
        legal_entity_id=entity.id,
    )
    kept = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=entity.id)
    assert kept.frozen and kept.person is None and kept.substitute_tax == flat.substitute_tax


@pytest.mark.parametrize("year", [2025, 2027])
def test_a_year_the_pack_has_no_basis_for_has_no_person_estimate(
    db_session: Session, year: int
) -> None:
    """2025 is before the law the pack carries. 2027 has the 2026 entry's basis but no INPS
    circular and no IRPEF entry, so its INPS and IRPEF have no figure rather than the year
    before's, and the IRPEF says its figures are not entered."""
    entity = _person(db_session)
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        year, ADMIN, legal_entity_id=entity.id
    )
    if year == 2025:
        assert estimate.person is None and estimate.basis is None
    else:
        assert estimate.person is not None
        assert estimate.person.inps.amount is None and estimate.person.irpef.amount is None
        assert "IRPEF del 2027" in estimate.person.irpef.missing[0]


def test_the_legal_entity_id_is_never_confused_between_two_people(db_session: Session) -> None:
    one, two = _person(db_session), _person(db_session, city="Milano", province="MI")
    _invoice(db_session, one, "65789.47", dt.date(2026, 5, 20))
    _invoice(db_session, two, "65789.47", dt.date(2026, 5, 20))
    service = AnalyticsService(db_session)
    first = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=one.id).person
    second = service.get_fiscal_estimate(2026, ADMIN, legal_entity_id=two.id).person
    assert first is not None and second is not None
    assert first.irpef.cadastral_code == "A162" and second.irpef.cadastral_code == "F205"
    assert first.irpef.with_surcharges == D("15070.00") and second.irpef.with_surcharges is None
