"""REB-974: tax law keyed by tax year, the taxpayer's parameters on the profile, and a year
that keeps its figures when the legal entity changes pack (taxes design § 1).

Three things are proved here and nowhere else: the law the packs carry (every figure is a row
of the frame's A5 register), the estimate giving the numbers it always gave once the formula
is read from the law, and a year computed under the flat-rate pack surviving the move to the
ordinary one.
"""

from decimal import Decimal
from itertools import product
from uuid import UUID

import pytest
from fakes.collected_invoices import collect_every_issued_invoice
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.fiscal import WARNING, estimate_income
from pigrocrm.core.analytics.schemas import FiscalEstimate
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.law import TaxYearLaw
from pigrocrm.core.fiscal.law_flat_rate import FLAT_RATE_2026
from pigrocrm.core.fiscal.models import FiscalEstimateReading
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK, IT_STANDARD_PACK, NON_IT_PACK, FiscalPack
from pigrocrm.core.fiscal.schemas import TAXPAYER_PARAMETERS, FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.money import round_money

ADMIN = Actor(id=None, type="system", role="admin")
LEGAL_ENTITY = UUID("00000000-0000-7000-8000-000000000001")
YEAR = today_local().year
HUNDRED = Decimal(100)
# The entry of the flat-rate pack that governs the year the service tests run in.
_FLAT_RATE_LAW_NOW = IT_FLAT_RATE_PACK.law_for(YEAR)
assert _FLAT_RATE_LAW_NOW is not None
FLAT_RATE_LAW_YEAR = _FLAT_RATE_LAW_NOW.from_year


# --- the law the packs carry ---------------------------------------------------------


def test_the_flat_rate_pack_carries_the_estimate_for_every_year_of_the_regime() -> None:
    assert IT_FLAT_RATE_PACK.law_for(2014) is None
    for year in (2015, 2016, 2023, 2026, 2040):
        law = IT_FLAT_RATE_PACK.law_for(year)
        assert law is not None and law.has_income_estimate
        assert law.flat_rate is not None
        assert law.flat_rate.contribution_base == "flat_rate_income"
        assert law.flat_rate.substitute_tax_base == "flat_rate_income_net_of_contributions"
        assert law.income_basis is not None and law.income_basis.basis == "cash"
    # One entry per change of the law (`test_flat_rate_law.py` says what each one holds).
    assert [law.from_year for law in IT_FLAT_RATE_PACK.laws] == [2015, 2016, 2026]


def test_the_ordinary_pack_carries_the_verified_advance_rules_and_no_estimate() -> None:
    """Every figure is a verified row of the frame's A5 register, Agenzia delle Entrate,
    «Imposte sui redditi (Irpef, Ires)»: 100% of the base in 40/60 or 50/50 under ISA, no
    advance at or below 51.65, a single November payment below 257.52, 0.40% for paying
    within 30 days after the deadline. Their dates are the pack's calendar
    (`test_tax_calendar.py`)."""
    assert IT_STANDARD_PACK.law_for(2025) is None
    law = IT_STANDARD_PACK.law_for(2026)
    assert law is not None
    assert not law.has_income_estimate
    advances = law.advances
    assert advances is not None
    assert advances.advance_percent_of_base == Decimal("100")
    assert (advances.first_advance_percent, advances.second_advance_percent) == (40, 60)
    assert (advances.first_advance_percent_isa, advances.second_advance_percent_isa) == (50, 50)
    assert advances.no_advance_at_or_below == Decimal("51.65")
    assert advances.single_november_payment_below == Decimal("257.52")
    assert advances.deferral_surcharge_percent == Decimal("0.40")
    assert advances.deferral_window_days == 30
    # The advance percentages of each method add up to the whole base.
    assert advances.first_advance_percent + advances.second_advance_percent == HUNDRED
    assert advances.first_advance_percent_isa + advances.second_advance_percent_isa == HUNDRED


def test_a_pack_with_no_regime_to_compute_has_no_law() -> None:
    assert NON_IT_PACK.laws == ()
    assert NON_IT_PACK.law_for(YEAR) is None


def test_a_later_law_entry_governs_from_its_year_and_the_earlier_one_before() -> None:
    first = TaxYearLaw(from_year=2020, flat_rate=FLAT_RATE_2026)
    second = TaxYearLaw(from_year=2027)
    pack = FiscalPack(id="x", version="1", ceilings=(), charges=(), laws=(first, second))
    assert pack.law_for(2019) is None
    assert pack.law_for(2020) is first
    assert pack.law_for(2026) is first
    assert pack.law_for(2027) is second
    assert pack.law_for(2031) is second


def test_law_entries_must_be_in_increasing_year_order() -> None:
    with pytest.raises(ValueError, match="increasing"):
        FiscalPack(
            id="x",
            version="1",
            ceilings=(),
            charges=(),
            laws=(TaxYearLaw(from_year=2027), TaxYearLaw(from_year=2020)),
        )
    with pytest.raises(ValueError, match="increasing"):
        FiscalPack(
            id="x",
            version="1",
            ceilings=(),
            charges=(),
            laws=(TaxYearLaw(from_year=2020), TaxYearLaw(from_year=2020)),
        )


# --- the estimate follows the law, and the total it gave before does not move -------


def _formula_of_the_law(
    revenue: Decimal,
    coefficient: Decimal | None,
    rate_substitute: Decimal | None,
    social_security_rate: Decimal | None,
) -> dict[str, Decimal | None]:
    """L. 190/2014 art. 1 c. 64, written out a second time and independently of
    `analytics/fiscal.py`: the contributions on the income, deducted from it, the substitute
    tax on what is left. Copied here on purpose, so a later edit of the estimate has to
    change this test to change the formula."""
    taxable = round_money(revenue * coefficient / HUNDRED) if coefficient is not None else None
    contributions = (
        round_money(taxable * social_security_rate / HUNDRED)
        if (taxable is not None and social_security_rate is not None)
        else None
    )
    base = taxable - contributions if (taxable is not None and contributions is not None) else None
    substitute = (
        round_money(base * rate_substitute / HUNDRED)
        if (base is not None and rate_substitute is not None)
        else None
    )
    net = (
        round_money(revenue - substitute - contributions)
        if (substitute is not None and contributions is not None)
        else None
    )
    return {
        "taxable_amount": taxable,
        "contributions": contributions,
        "substitute_tax_base": base,
        "substitute_tax": substitute,
        "estimated_net_income": net,
        "due_total": (
            round_money(substitute + contributions)
            if (substitute is not None and contributions is not None)
            else None
        ),
    }


def _formula_before_reb_1066(
    revenue: Decimal,
    coefficient: Decimal | None,
    rate_substitute: Decimal | None,
    social_security_rate: Decimal | None,
) -> dict[str, Decimal | None]:
    """The estimate as it was until REB-1066: the contributions on the income less the
    substitute tax. Kept to show what did not change: the total to set aside and the net
    income, to within two cents."""
    taxable = round_money(revenue * coefficient / HUNDRED) if coefficient is not None else None
    substitute = (
        round_money(taxable * rate_substitute / HUNDRED)
        if (taxable is not None and rate_substitute is not None)
        else None
    )
    contributions = (
        round_money((taxable - substitute) * social_security_rate / HUNDRED)
        if (taxable is not None and substitute is not None and social_security_rate is not None)
        else None
    )
    net = (
        round_money(revenue - substitute - contributions)
        if (substitute is not None and contributions is not None)
        else None
    )
    return {
        "taxable_amount": taxable,
        "estimated_net_income": net,
        "due_total": (
            round_money(substitute + contributions)
            if (substitute is not None and contributions is not None)
            else None
        ),
    }


REVENUES = [Decimal(v) for v in ("0.00", "0.01", "1.05", "1583.83", "33500.00", "85000.00")]
COEFFICIENTS = [None, Decimal("40.00"), Decimal("67.00"), Decimal("78.00")]
SUBSTITUTE_RATES = [None, Decimal("5.00"), Decimal("15.00")]
SOCIAL_SECURITY_RATES = [None, Decimal("24.00"), Decimal("26.07")]
CENT = Decimal("0.01")


@pytest.mark.parametrize("year", [2015, 2016, 2024, YEAR])
def test_the_estimate_is_the_laws_formula_for_every_combination(year: int) -> None:
    law = IT_FLAT_RATE_PACK.law_for(year)
    assert law is not None
    for revenue, coefficient, substitute, social in product(
        REVENUES, COEFFICIENTS, SUBSTITUTE_RATES, SOCIAL_SECURITY_RATES
    ):
        new = estimate_income(
            year=year,
            legal_entity_id=LEGAL_ENTITY,
            revenue=revenue,
            coefficient=coefficient,
            rate_substitute=substitute,
            social_security_rate=social,
            flat_rate=law.flat_rate,
        )
        expected = _formula_of_the_law(revenue, coefficient, substitute, social)
        for name, value in expected.items():
            assert getattr(new, name) == value, (
                name,
                year,
                revenue,
                coefficient,
                substitute,
                social,
            )
        assert new.warning == WARNING


def test_the_total_to_set_aside_and_the_net_income_are_what_they_were_before_the_change() -> None:
    """What REB-1066 explains instead of hiding: the contributions and the substitute tax
    change, since the law deducts the first from the base of the second; their sum and the net
    income do not, to within two cents of rounding."""
    law = IT_FLAT_RATE_PACK.law_for(YEAR)
    assert law is not None
    compared = 0
    for revenue, coefficient, substitute, social in product(
        REVENUES, COEFFICIENTS, SUBSTITUTE_RATES, SOCIAL_SECURITY_RATES
    ):
        new = estimate_income(
            year=YEAR,
            legal_entity_id=LEGAL_ENTITY,
            revenue=revenue,
            coefficient=coefficient,
            rate_substitute=substitute,
            social_security_rate=social,
            flat_rate=law.flat_rate,
        )
        old = _formula_before_reb_1066(revenue, coefficient, substitute, social)
        assert new.taxable_amount == old["taxable_amount"]
        if old["due_total"] is None:
            assert new.due_total is None
            continue
        compared += 1
        assert new.due_total is not None and new.estimated_net_income is not None
        assert old["due_total"] is not None and old["estimated_net_income"] is not None
        assert abs(new.due_total - old["due_total"]) <= 2 * CENT, (revenue, coefficient)
        assert abs(new.estimated_net_income - old["estimated_net_income"]) <= 2 * CENT
    assert compared


def test_a_year_with_no_law_computes_nothing_whatever_the_profile_holds() -> None:
    estimate = estimate_income(
        year=2014,
        legal_entity_id=LEGAL_ENTITY,
        revenue=Decimal("1000.00"),
        coefficient=Decimal("67.00"),
        rate_substitute=Decimal("5.00"),
        social_security_rate=Decimal("26.07"),
        flat_rate=None,
    )
    assert estimate.revenue == Decimal("1000.00")
    for name in (
        "profitability_coefficient",
        "taxable_amount",
        "substitute_tax_rate",
        "substitute_tax",
        "social_security_rate",
        "contributions",
        "estimated_net_income",
        "due_total",
    ):
        assert getattr(estimate, name) is None, name


# --- the service: the same numbers, and a year that keeps them -----------------------


def _move_to_ordinary_regime(session: Session) -> None:
    FiscalProfileService(session).upsert(
        FiscalProfileUpsert(pack_id="it-standard", regime_code="RF01"), ADMIN
    )


def _figures(estimate: FiscalEstimate) -> dict[str, Decimal | None]:
    return {
        name: getattr(estimate, name)
        for name in (
            "revenue",
            "profitability_coefficient",
            "taxable_amount",
            "substitute_tax_rate",
            "substitute_tax",
            "social_security_rate",
            "contributions",
            "estimated_net_income",
            "due_total",
        )
    }


def test_the_service_estimate_is_the_one_the_formula_gave_and_names_its_law(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    collect_every_issued_invoice(db_session)
    estimate = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN)
    expected = _formula_of_the_law(
        Decimal("1583.83"), Decimal("67.00"), Decimal("5.00"), Decimal("26.07")
    )
    for name, value in expected.items():
        assert getattr(estimate, name) == value, name
    assert (estimate.pack_id, estimate.pack_version, estimate.law_from_year) == (
        "it-flat-rate",
        "1",
        FLAT_RATE_LAW_YEAR,
    )
    assert estimate.frozen is False


def test_a_year_computed_under_the_flat_rate_pack_keeps_its_figures_after_the_move(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The card's «Done when»: the figures of a year computed under the flat-rate pack are
    still there after the legal entity moves to the ordinary pack, with the pack and the
    parameters they were computed with, and the move keeps them whether or not anybody had
    opened the year."""
    collect_every_issued_invoice(db_session)
    service = AnalyticsService(db_session)
    before = service.get_fiscal_estimate(YEAR, ADMIN)
    assert before.estimated_net_income is not None
    assert db_session.execute(select(FiscalEstimateReading)).first() is None

    _move_to_ordinary_regime(db_session)
    after = service.get_fiscal_estimate(YEAR, ADMIN)

    assert _figures(after) == _figures(before)
    expected = _formula_of_the_law(
        Decimal("1583.83"), Decimal("67.00"), Decimal("5.00"), Decimal("26.07")
    )
    for name, value in expected.items():
        assert getattr(after, name) == value, name
    assert after.frozen is True
    assert (after.pack_id, after.pack_version, after.law_from_year) == (
        "it-flat-rate",
        "1",
        FLAT_RATE_LAW_YEAR,
    )
    reading = db_session.execute(select(FiscalEstimateReading)).scalar_one()
    assert reading.tax_year == YEAR
    assert (reading.pack_id, reading.pack_version, reading.law_from_year) == (
        "it-flat-rate",
        "1",
        FLAT_RATE_LAW_YEAR,
    )
    assert reading.parameters == {
        "regime_code": "RF19",
        "profitability_coefficient": "67.00",
        "substitute_tax_rate": "5.00",
        "social_security_rate": "26.07",
        "inps_category": None,
        "isa": None,
        **dict.fromkeys(TAXPAYER_PARAMETERS),
        "fiscal_year_end_month": 12,
        "fiscal_year_end_day": 31,
    }


def test_a_year_with_no_revenue_has_no_reading_and_no_figure_after_the_move(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The move keeps the years the legal entity issued in; any other year has nothing to
    keep, and the ordinary pack computes no income estimate: no figure, never a guess from
    today's profile."""
    _move_to_ordinary_regime(db_session)
    estimate = AnalyticsService(db_session).get_fiscal_estimate(YEAR - 2, ADMIN)
    assert estimate.frozen is False
    assert estimate.taxable_amount is None
    assert estimate.contributions is None
    assert estimate.estimated_net_income is None
    assert estimate.pack_id == "it-standard"
    # The ordinary pack's own entry starts in 2026: before it, the pack has no law at all.
    assert estimate.law_from_year == (2026 if YEAR - 2 >= 2026 else None)


def test_a_move_between_packs_that_both_compute_nothing_freezes_nothing(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    _move_to_ordinary_regime(db_session)
    db_session.execute(delete(FiscalEstimateReading))
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="non-it", default_vat_nature="N2.1"), ADMIN
    )
    assert db_session.execute(select(FiscalEstimateReading)).first() is None


def test_a_save_that_keeps_the_pack_freezes_nothing(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(
            regime_code="RF19",
            profitability_coefficient=Decimal("78.00"),
            substitute_tax_rate=Decimal("15.00"),
            social_security_rate=Decimal("24.00"),
        ),
        ADMIN,
    )
    assert db_session.execute(select(FiscalEstimateReading)).first() is None


def test_the_frozen_figures_are_the_ones_of_the_profile_when_the_entity_left(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    collect_every_issued_invoice(db_session)
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(
            regime_code="RF19",
            profitability_coefficient=Decimal("78.00"),
            substitute_tax_rate=Decimal("15.00"),
            social_security_rate=Decimal("24.00"),
        ),
        ADMIN,
    )
    _move_to_ordinary_regime(db_session)
    frozen = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN)
    assert frozen.profitability_coefficient == Decimal("78.00")
    assert frozen.estimated_net_income == Decimal("1146.50")


def test_a_what_if_revenue_never_reads_a_frozen_year(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    _move_to_ordinary_regime(db_session)
    again = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN, revenue=Decimal("1000"))
    assert again.revenue == Decimal("1000.00")
    assert again.frozen is False and again.taxable_amount is None


# --- the taxpayer's parameters on the profile, the year end on the legal entity ------


def test_the_inps_category_and_the_isa_flag_are_kept_when_a_save_does_not_name_them(
    db_session: Session,
) -> None:
    """The web form sends every key it knows and these two are not among them yet: a save
    that leaves them out must not erase what the assistant set."""
    service = FiscalProfileService(db_session)
    saved = service.upsert(
        FiscalProfileUpsert(regime_code="RF19", inps_category="gestione_separata", isa=False),
        ADMIN,
    )
    assert (saved.inps_category, saved.isa) == ("gestione_separata", False)

    resaved = service.upsert(FiscalProfileUpsert(regime_code="RF19", iban=None), ADMIN)
    assert (resaved.inps_category, resaved.isa) == ("gestione_separata", False)

    cleared = service.upsert(
        FiscalProfileUpsert(regime_code="RF19", inps_category=None, isa=None), ADMIN
    )
    assert (cleared.inps_category, cleared.isa) == (None, None)


def test_an_unknown_inps_category_is_refused() -> None:
    with pytest.raises(ValueError, match="inps_category"):
        FiscalProfileUpsert(regime_code="RF19", inps_category="cassa_forense")  # type: ignore[arg-type]


def test_a_company_abroad_has_no_inps_category_and_no_isa(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    with pytest.raises(ValidationFailed) as excinfo:
        service.upsert(
            FiscalProfileUpsert(
                pack_id="non-it",
                default_vat_nature="N2.1",
                inps_category="gestione_separata",
            ),
            ADMIN,
        )
    assert excinfo.value.details["field"] == "inps_category"
    saved = service.upsert(FiscalProfileUpsert(pack_id="non-it", default_vat_nature="N2.1"), ADMIN)
    assert (saved.inps_category, saved.isa) == (None, None)


def test_a_move_abroad_clears_what_the_italian_profile_held(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    service.upsert(
        FiscalProfileUpsert(regime_code="RF19", inps_category="gestione_separata"), ADMIN
    )
    moved = service.upsert(FiscalProfileUpsert(pack_id="non-it", default_vat_nature="N2.1"), ADMIN)
    assert moved.inps_category is None


def _legal_entity(**overrides: object) -> LegalEntityUpsert:
    return LegalEntityUpsert(legal_name="Studio Rossi", **overrides)  # type: ignore[arg-type]


def test_the_fiscal_year_end_is_31_december_until_somebody_says_otherwise(
    db_session: Session,
) -> None:
    service = LegalEntityService(db_session)
    created = service.upsert_default(_legal_entity(), ADMIN)
    assert (created.fiscal_year_end_month, created.fiscal_year_end_day) == (12, 31)

    moved = service.upsert_default(
        _legal_entity(fiscal_year_end_month=6, fiscal_year_end_day=30), ADMIN
    )
    assert (moved.fiscal_year_end_month, moved.fiscal_year_end_day) == (6, 30)

    # A whole-row save that does not name the pair leaves it as it is.
    renamed = service.upsert_default(_legal_entity(city="Milano"), ADMIN)
    assert (renamed.fiscal_year_end_month, renamed.fiscal_year_end_day) == (6, 30)


@pytest.mark.parametrize(
    ("month", "day", "field"),
    [
        (6, None, "fiscal_year_end_day"),
        (None, 30, "fiscal_year_end_month"),
        (4, 31, "fiscal_year_end_day"),
        (2, 30, "fiscal_year_end_day"),
    ],
)
def test_a_fiscal_year_end_that_is_not_a_date_is_refused(
    db_session: Session, month: int | None, day: int | None, field: str
) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        LegalEntityService(db_session).upsert_default(
            _legal_entity(fiscal_year_end_month=month, fiscal_year_end_day=day), ADMIN
        )
    assert excinfo.value.details["field"] == field


def test_29_february_is_a_fiscal_year_end(db_session: Session) -> None:
    saved = LegalEntityService(db_session).upsert_default(
        _legal_entity(fiscal_year_end_month=2, fiscal_year_end_day=29), ADMIN
    )
    assert (saved.fiscal_year_end_month, saved.fiscal_year_end_day) == (2, 29)
