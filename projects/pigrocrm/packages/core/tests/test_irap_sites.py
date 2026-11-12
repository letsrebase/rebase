"""REB-1092: the IRAP base of an srl with places of business in more than one region.

Art. 4 c. 2 D.Lgs. 446/1997 attributes the value of production to each region in proportion to the
pay of the staff assigned there for at least three months, and what is done elsewhere without staff
for that time to the region of the registered office. The pure split is proven against numbers
worked by hand; the estimate against the real schema: one site (or none, or sites in the
registered office's own region) is the single-region estimate it always was, two regions split by
pay, and a person with no place stated stops the split and is named.

The worked example, a Milan srl (Lombardia, 3.90%) with a site in Veneto (4.08%) in 2026:

    revenue 100,000.00, purchases 20,000.00; the small-base deduction takes 8,000.00 off, so the
    base is 72,000.00 (no contract type is stated, so no staff deduction applies).
    pay (gross a year): employee at the registered office 24,000, director at the registered
    office 12,000, employee at the Veneto site 24,000; total 60,000.
    Lombardia 36,000 / 60,000 = 60%: base 43,200.00, 3.90% = 1,684.80.
    Veneto    24,000 / 60,000 = 40%: base 28,800.00, 4.08% = 1,175.04.
    IRAP: 1,684.80 + 1,175.04 = 2,859.84.
"""

import datetime as dt
from decimal import Decimal
from itertools import count
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.fiscal.irap_sites import (
    RegionRate,
    SiteFacts,
    SitePlace,
    StaffPay,
    irap_regions,
    served_months,
    split_irap_base,
    splits,
)
from pigrocrm.core.fiscal.law_irap import IRAP_LAW
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.site_schemas import (
    LegalEntitySiteCreate,
    LegalEntitySiteUpdate,
)
from pigrocrm.core.legal_entities.site_service import LegalEntitySiteService
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.staff.models import StaffMember
from pigrocrm.core.staff.schemas import DirectorCreate, EmployeeCreate, StaffMemberUpdate
from pigrocrm.core.staff.service import StaffService
from pigrocrm.core.timetracking.models import CostCategory

D = dt.date
M = Decimal
YEAR = 2026
ADMIN = Actor(id=uuid4(), type="user", role="admin")
SYSTEM = Actor(id=None, type="system", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
_NUMBERS = count(1)

LOMBARDIA, VENETO, TOSCANA = "03", "05", "09"
TERRITORY = IRAP_LAW.territory
RATES = {LOMBARDIA: M("3.90"), VENETO: M("4.08"), TOSCANA: M("3.90")}
NAMES = {LOMBARDIA: "Lombardia", VENETO: "Veneto", TOSCANA: "Toscana"}


def _rate_of(key: str) -> RegionRate:
    return RegionRate(NAMES[key], RATES[key])


# ---- the pure rule -------------------------------------------------------------------------------

SITE_VENETO = SitePlace(id=uuid4(), region=VENETO, label="Padova")
SITE_TOSCANA = SitePlace(id=uuid4(), region=TOSCANA, label="Firenze")
FULL_YEAR = (D(YEAR, 1, 1), D(YEAR, 12, 31))


def _pay(
    name: str,
    pay: str,
    *,
    site: SitePlace | None = None,
    office: bool = False,
    served: tuple[dt.date, dt.date] = FULL_YEAR,
) -> StaffPay:
    return StaffPay(
        staff_member_id=uuid4(),
        name=name,
        pay=M(pay),
        service_from=served[0],
        service_to=served[1],
        site_id=None if site is None else site.id,
        at_registered_office=office,
    )


def test_the_base_is_split_by_the_pay_of_the_staff_of_each_region() -> None:
    facts = SiteFacts(
        sites=(SITE_VENETO,),
        staff=(
            _pay("Giulia", "24000.00", office=True),
            _pay("Marco", "12000.00", office=True),
            _pay("Luca", "24000.00", site=SITE_VENETO),
        ),
    )
    split = split_irap_base(M("72000.00"), False, LOMBARDIA, facts, TERRITORY, _rate_of)

    assert split.missing is None
    assert [
        (part.region, part.pay, part.share, part.base, part.rate, part.amount)
        for part in split.parts
    ] == [
        (LOMBARDIA, M("36000.00"), M("60.00"), M("43200.00"), M("3.90"), M("1684.80")),
        (VENETO, M("24000.00"), M("40.00"), M("28800.00"), M("4.08"), M("1175.04")),
    ]
    assert split.amount == M("2859.84")
    assert any("art. 4 c. 2" in text for text in split.assumptions)


def test_the_parts_add_up_to_the_base_whatever_the_cents() -> None:
    facts = SiteFacts(
        sites=(SITE_VENETO, SITE_TOSCANA),
        staff=(
            _pay("A", "1000.00", office=True),
            _pay("B", "1000.00", site=SITE_VENETO),
            _pay("C", "1000.00", site=SITE_TOSCANA),
        ),
    )
    split = split_irap_base(M("100.00"), False, LOMBARDIA, facts, TERRITORY, _rate_of)

    assert sum(part.base for part in split.parts) == M("100.00")
    assert sorted(part.base for part in split.parts) == [M("33.33"), M("33.33"), M("33.34")]


def test_a_person_in_service_less_than_three_months_counts_for_the_registered_office() -> None:
    short = (D(YEAR, 11, 1), D(YEAR, 12, 31))
    facts = SiteFacts(
        sites=(SITE_VENETO,),
        staff=(
            _pay("Giulia", "20000.00", office=True),
            _pay("Luca", "20000.00", site=SITE_VENETO, served=short),
        ),
    )
    split = split_irap_base(M("1000.00"), False, LOMBARDIA, facts, TERRITORY, _rate_of)

    assert [(part.region, part.base) for part in split.parts] == [(LOMBARDIA, M("1000.00"))]
    assert any("Luca" in text and "meno di 3 mesi" in text for text in split.assumptions)
    assert any("tutta la base" in text for text in split.assumptions)


@pytest.mark.parametrize(
    ("first", "last", "enough"),
    [
        (D(YEAR, 1, 1), D(YEAR, 3, 31), True),
        (D(YEAR, 1, 1), D(YEAR, 3, 30), False),
        (D(YEAR, 1, 15), D(YEAR, 4, 14), True),
        (D(YEAR, 11, 30), D(YEAR + 1, 2, 28), True),
        (D(YEAR, 12, 1), D(YEAR, 12, 31), False),
    ],
)
def test_three_months_are_counted_in_calendar_months(
    first: dt.date, last: dt.date, enough: bool
) -> None:
    assert served_months(first, last, 3) is enough


def test_no_staff_at_all_sends_the_base_to_the_registered_office() -> None:
    split = split_irap_base(
        M("500.00"),
        False,
        LOMBARDIA,
        SiteFacts(sites=(SITE_VENETO,), staff=()),
        TERRITORY,
        _rate_of,
    )

    assert [(part.region, part.base, part.share) for part in split.parts] == [
        (LOMBARDIA, M("500.00"), M("100.00"))
    ]


def test_a_person_with_no_place_stated_stops_the_split_and_is_named() -> None:
    facts = SiteFacts(
        sites=(SITE_VENETO,),
        staff=(_pay("Giulia", "24000.00", office=True), _pay("Luca", "24000.00")),
    )
    split = split_irap_base(M("72000.00"), False, LOMBARDIA, facts, TERRITORY, _rate_of)

    assert split.parts == () and split.amount is None
    assert split.missing is not None and "Luca" in split.missing and "Giulia" not in split.missing


def test_a_person_whose_pay_is_not_estimated_stops_the_split_and_is_named() -> None:
    facts = SiteFacts(sites=(SITE_VENETO,), staff=(), unpriced=("Anna",))
    split = split_irap_base(M("100.00"), False, LOMBARDIA, facts, TERRITORY, _rate_of)

    assert split.amount is None and split.missing is not None and "Anna" in split.missing


def test_a_region_with_no_rate_withholds_the_total_and_says_which() -> None:
    def rate_of(key: str) -> RegionRate:
        if key == VENETO:
            return RegionRate("Veneto", None, "Manca l'aliquota del Veneto.")
        return _rate_of(key)

    facts = SiteFacts(
        sites=(SITE_VENETO,),
        staff=(_pay("A", "10.00", office=True), _pay("B", "10.00", site=SITE_VENETO)),
    )
    split = split_irap_base(M("100.00"), False, LOMBARDIA, facts, TERRITORY, rate_of)

    assert split.missing == "Manca l'aliquota del Veneto."
    assert split.amount is None
    assert [part.amount for part in split.parts] == [M("1.95"), None]


def test_a_loss_is_split_and_taxes_nothing() -> None:
    facts = SiteFacts(
        sites=(SITE_VENETO,),
        staff=(_pay("A", "10.00", office=True), _pay("B", "30.00", site=SITE_VENETO)),
    )
    split = split_irap_base(M("-400.00"), True, LOMBARDIA, facts, TERRITORY, _rate_of)

    assert [(part.region, part.base, part.amount) for part in split.parts] == [
        (LOMBARDIA, M("-100.00"), M("0.00")),
        (VENETO, M("-300.00"), M("0.00")),
    ]
    assert split.amount == M("0.00")


def test_only_a_site_outside_the_registered_offices_region_splits() -> None:
    same = SitePlace(id=uuid4(), region=LOMBARDIA, label="Bergamo")
    assert not splits(LOMBARDIA, ())
    assert not splits(LOMBARDIA, (same,))
    assert splits(LOMBARDIA, (same, SITE_VENETO))
    assert not splits(None, (SITE_VENETO,))


def test_the_regions_a_site_can_be_in_are_the_ones_the_pack_has_rates_for() -> None:
    regions = dict(irap_regions())
    assert regions["03"] == "Lombardia" and regions["BZ"].startswith("Provincia autonoma")
    assert len(regions) == 21


# ---- the estimate, against the real schema -------------------------------------------------------


def _srl(session: Session, city: str = "Milano", province: str = "MI") -> LegalEntity:
    """A company on the `it-company` pack that states an activity no regional list singles out
    (ATECO 70.22.09), so every region answers its ordinary rate."""
    entity = LegalEntity(
        name=f"srl {uuid4().hex[:6]}",
        legal_name="Esempio S.r.l.",
        country="IT",
        city=city,
        province=province,
    )
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-company",
            regime_code="RF01",
            ateco_2007_code="70.22.09",
            ateco_2025_code="70.22.09",
        )
    )
    session.flush()
    return entity


def _books(session: Session, entity: LegalEntity) -> None:
    taxable = M("100000.00")
    invoice = Invoice(
        legal_entity_id=entity.id,
        customer_id=make_customer(session).id,
        type="invoice",
        status="issued",
        year=YEAR,
        number=next(_NUMBERS) + 300_000,
        issue_date=D(YEAR, 6, 1),
        due_date=D(YEAR, 7, 1),
        taxable_amount=taxable,
        vat_amount=M("0.00"),
        stamp_duty=M("0.00"),
        total=taxable,
        payment_status="unpaid",
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            line_number=1,
            description="Consulenza",
            quantity=M("1.000000"),
            unit_price=taxable,
            total_price=taxable,
            vat_rate=M("0.00"),
            vat_nature="N2.2",
        )
    )
    category = CostCategory(name=f"Servizi {uuid4().hex[:4]}")
    session.add(category)
    session.flush()
    session.add(
        Purchase(
            legal_entity_id=entity.id,
            category_id=category.id,
            kind="expense",
            status="registered",
            date=D(YEAR, 3, 1),
            amount=M("20000.00"),
            cost_amount=M("20000.00"),
            description="Acquisto",
            custom_fields={},
        )
    )
    session.flush()


def _employee(
    session: Session,
    entity: LegalEntity,
    name: str,
    *,
    start: dt.date | None = None,
    **workplace: object,
) -> UUID:
    return (
        StaffService(session)
        .create_staff_member(
            EmployeeCreate(
                legal_entity_id=entity.id,
                name=name,
                start_date=start or D(YEAR, 1, 1),
                gross_annual_pay=M("24000.00"),
                monthly_payments=13,
                employer_inps_rate=M("30.00"),
                **workplace,  # type: ignore[arg-type]
            ),
            ADMIN,
        )
        .id
    )


def _director(session: Session, entity: LegalEntity, **workplace: object) -> UUID:
    return (
        StaffService(session)
        .create_staff_member(
            DirectorCreate(
                legal_entity_id=entity.id,
                name="Marco Rossi",
                start_date=D(YEAR, 1, 1),
                annual_fee=M("12000.00"),
                **workplace,  # type: ignore[arg-type]
            ),
            ADMIN,
        )
        .id
    )


def _site(session: Session, entity: LegalEntity, region: str, city: str | None = None) -> UUID:
    return (
        LegalEntitySiteService(session)
        .add_legal_entity_site(entity.id, LegalEntitySiteCreate(region=region, city=city), ADMIN)
        .id
    )


def _irap(session: Session, entity: LegalEntity):  # type: ignore[no-untyped-def]
    estimate = AnalyticsService(session).get_fiscal_estimate(
        YEAR, SYSTEM, legal_entity_id=entity.id
    )
    assert estimate.company is not None
    return estimate, estimate.company.irap


def test_with_no_site_the_estimate_is_the_single_region_one(db_session: Session) -> None:
    entity = _srl(db_session)
    _books(db_session, entity)
    _employee(db_session, entity, "Giulia Bianchi")
    estimate, irap = _irap(db_session, entity)

    assert (irap.base, irap.rate, irap.amount, irap.region) == (
        M("72000.00"),
        M("3.90"),
        M("2808.00"),
        LOMBARDIA,
    )
    assert irap.regions == []
    assert any("Si considera una sola sede" in text for text in irap.assumptions)


def test_a_site_in_the_registered_offices_region_changes_nothing(db_session: Session) -> None:
    entity = _srl(db_session)
    _books(db_session, entity)
    _employee(db_session, entity, "Giulia Bianchi")
    _, alone = _irap(db_session, entity)
    _site(db_session, entity, LOMBARDIA, "Bergamo")
    estimate, with_site = _irap(db_session, entity)

    assert (with_site.base, with_site.rate, with_site.amount, with_site.region) == (
        alone.base,
        alone.rate,
        alone.amount,
        alone.region,
    )
    assert with_site.regions == [] and with_site.missing is None
    assert not any("art. 4 c. 2" in text for text in with_site.assumptions)
    assert not any("Si considera una sola sede" in text for text in with_site.assumptions)
    assert any("Le sedi indicate sono tutte in questa regione" in t for t in with_site.assumptions)
    # A person with no place stated is not a problem while there is one region.
    assert estimate.due_total is not None


def test_two_regions_split_the_base_by_pay(db_session: Session) -> None:
    entity = _srl(db_session)
    _books(db_session, entity)
    site = _site(db_session, entity, VENETO, "Padova")
    _employee(db_session, entity, "Giulia Bianchi", at_registered_office=True)
    _director(db_session, entity, at_registered_office=True)
    _employee(db_session, entity, "Luca Verdi", site_id=site)
    estimate, irap = _irap(db_session, entity)

    assert irap.missing is None and irap.base == M("72000.00")
    assert irap.rate is None and irap.region is None
    assert [
        (part.region, part.name, part.pay, part.share, part.base, part.rate, part.amount)
        for part in irap.regions
    ] == [
        (LOMBARDIA, "Lombardia", M("36000.00"), M("60.00"), M("43200.00"), M("3.90"), M("1684.80")),
        (VENETO, "Veneto", M("24000.00"), M("40.00"), M("28800.00"), M("4.08"), M("1175.04")),
    ]
    assert irap.amount == M("2859.84")
    # IRES: 100,000 - 20,000 - 31,200 - 31,200 - 12,000 = 5,600, less the IRAP deducted in full
    # (2,859.84) = 2,740.16; 24% = 657.64.
    assert estimate.due_total == M("2859.84") + M("657.64")
    assert any("art. 4 c. 2" in text for text in irap.assumptions)
    assert not any("una sola sede" in text for text in irap.assumptions)
    # Each region's rate says where it comes from, as the single-region one does.
    assert any("regione (Veneto)" in text for text in irap.assumptions)


def _state_ateco(session: Session, entity: LegalEntity, ateco_2007: str | None) -> None:
    profile = session.scalars(
        select(FiscalProfile).where(FiscalProfile.legal_entity_id == entity.id)
    ).one()
    profile.ateco_2007_code = ateco_2007
    profile.ateco_2025_code = ateco_2007
    session.flush()


def test_each_region_rate_follows_the_activity_the_company_states(db_session: Session) -> None:
    """The rate of every part comes from `IrapLaw.rate_for` with the company's ATECO code: a
    software company pays Toscana's code 007 (4.82%) on the Tuscan part and Lombardia's ordinary
    rate on the rest."""
    entity = _srl(db_session)
    _state_ateco(db_session, entity, "62.01.00")
    _books(db_session, entity)
    site = _site(db_session, entity, TOSCANA, "Firenze")
    _employee(db_session, entity, "Giulia Bianchi", at_registered_office=True)
    _employee(db_session, entity, "Luca Verdi", site_id=site)
    _, irap = _irap(db_session, entity)

    assert irap.missing is None
    # 72,000 split 50/50: 36,000 x 3.90% = 1,404.00 and 36,000 x 4.82% = 1,735.20.
    assert [(part.region, part.base, part.rate, part.amount) for part in irap.regions] == [
        (LOMBARDIA, M("36000.00"), M("3.90"), M("1404.00")),
        (TOSCANA, M("36000.00"), M("4.82"), M("1735.20")),
    ]
    assert irap.amount == M("3139.20")
    assert any("riga 007" in text for text in irap.assumptions)


def test_a_region_that_needs_the_activity_withholds_the_figure_until_it_is_stated(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _state_ateco(db_session, entity, None)
    _books(db_session, entity)
    site = _site(db_session, entity, TOSCANA)
    _employee(db_session, entity, "Giulia Bianchi", at_registered_office=True)
    _employee(db_session, entity, "Luca Verdi", site_id=site)
    _, irap = _irap(db_session, entity)

    assert irap.amount is None
    assert irap.missing is not None and "ATECO 2007" in irap.missing and "Toscana" in irap.missing


def test_a_person_with_no_place_is_named_and_gives_no_regional_figure(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _books(db_session, entity)
    site = _site(db_session, entity, VENETO)
    _employee(db_session, entity, "Giulia Bianchi", at_registered_office=True)
    _employee(db_session, entity, "Luca Verdi", site_id=site)
    _employee(db_session, entity, "Anna Neri")
    estimate, irap = _irap(db_session, entity)

    assert irap.amount is None and irap.regions == []
    assert irap.missing is not None
    assert "Anna Neri" in irap.missing
    assert "Luca Verdi" not in irap.missing and "Giulia Bianchi" not in irap.missing
    assert estimate.due_total is None


def test_a_site_with_staff_for_less_than_three_months_does_not_take_a_share(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _books(db_session, entity)
    site = _site(db_session, entity, VENETO)
    _employee(db_session, entity, "Giulia Bianchi", at_registered_office=True)
    _employee(db_session, entity, "Luca Verdi", site_id=site, start=D(YEAR, 11, 1))
    _, irap = _irap(db_session, entity)

    assert [(part.region, part.base) for part in irap.regions] == [(LOMBARDIA, M("72000.00"))]
    assert irap.amount == M("2808.00")
    assert any("Luca Verdi" in text for text in irap.assumptions)


# ---- the sites and the assignment ----------------------------------------------------------------


def test_a_site_takes_only_a_region_the_pack_has(db_session: Session) -> None:
    entity = _srl(db_session)
    service = LegalEntitySiteService(db_session)
    for region in ("04", "XX", "00"):
        with pytest.raises(ValidationFailed):
            service.add_legal_entity_site(entity.id, LegalEntitySiteCreate(region=region), ADMIN)
    created = service.add_legal_entity_site(
        entity.id, LegalEntitySiteCreate(region="BZ", city="Bolzano", address="Via Roma 1"), ADMIN
    )
    assert created.region_name == "Provincia autonoma di Bolzano" and created.staff_count == 0


def test_a_site_is_updated_listed_and_removed_by_an_administrator_only(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    service = LegalEntitySiteService(db_session)
    created = service.add_legal_entity_site(entity.id, LegalEntitySiteCreate(region=VENETO), ADMIN)
    for actor, refusal in ((COLLABORATOR, PermissionDenied), (AGENT_ADMIN_FULL, AgentForbidden)):
        with pytest.raises(refusal):
            service.add_legal_entity_site(entity.id, LegalEntitySiteCreate(region=VENETO), actor)

    updated = service.update_legal_entity_site(
        entity.id, created.id, LegalEntitySiteUpdate(city="Padova", region=TOSCANA), ADMIN
    )
    assert (updated.region, updated.city) == (TOSCANA, "Padova")
    cleared = service.update_legal_entity_site(
        entity.id, created.id, LegalEntitySiteUpdate(city=None), ADMIN
    )
    assert cleared.city is None and cleared.region == TOSCANA
    with pytest.raises(ValidationFailed):
        service.update_legal_entity_site(
            entity.id, created.id, LegalEntitySiteUpdate(region=None), ADMIN
        )

    assert [row.id for row in service.list_legal_entity_sites(entity.id, ADMIN)] == [created.id]
    service.remove_legal_entity_site(entity.id, created.id, ADMIN)
    assert service.list_legal_entity_sites(entity.id, ADMIN) == []
    with pytest.raises(NotFound):
        service.remove_legal_entity_site(entity.id, created.id, ADMIN)


def test_a_site_with_staff_is_not_removed_until_they_are_moved(db_session: Session) -> None:
    entity = _srl(db_session)
    service = LegalEntitySiteService(db_session)
    site = _site(db_session, entity, VENETO)
    member = _employee(db_session, entity, "Luca Verdi", site_id=site)

    assert service.list_legal_entity_sites(entity.id, ADMIN)[0].staff_count == 1
    with pytest.raises(Conflict) as refused:
        service.remove_legal_entity_site(entity.id, site, ADMIN)
    assert refused.value.details["staff_count"] == 1

    StaffService(db_session).update_staff_member(
        member, StaffMemberUpdate(at_registered_office=True), ADMIN
    )
    service.remove_legal_entity_site(entity.id, site, ADMIN)


def test_a_person_is_assigned_to_a_site_or_the_registered_office_never_both(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    other = _srl(db_session, "Padova", "PD")
    site = _site(db_session, entity, VENETO)
    foreign_site = _site(db_session, other, TOSCANA)
    staff = StaffService(db_session)

    with pytest.raises(ValueError):
        EmployeeCreate(
            legal_entity_id=entity.id,
            name="X",
            start_date=D(YEAR, 1, 1),
            gross_annual_pay=M("1000"),
            monthly_payments=13,
            site_id=site,
            at_registered_office=True,
        )
    with pytest.raises(ValidationFailed):
        _employee(db_session, entity, "Y", site_id=foreign_site)

    member = _employee(db_session, entity, "Luca Verdi", at_registered_office=True)
    moved = staff.update_staff_member(member, StaffMemberUpdate(site_id=site), ADMIN)
    assert (moved.site_id, moved.at_registered_office) == (site, False)
    back = staff.update_staff_member(member, StaffMemberUpdate(at_registered_office=True), ADMIN)
    assert (back.site_id, back.at_registered_office) == (None, True)
    cleared = staff.update_staff_member(member, StaffMemberUpdate(at_registered_office=None), ADMIN)
    assert (cleared.site_id, cleared.at_registered_office) == (None, False)
    with pytest.raises(ValidationFailed):
        staff.update_staff_member(member, StaffMemberUpdate(site_id=foreign_site), ADMIN)
    row = db_session.get(StaffMember, member)
    assert row is not None and row.site_id is None
