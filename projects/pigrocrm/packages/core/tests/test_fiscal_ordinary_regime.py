"""The ordinary regime has a pack of its own (REB-760).

A legal entity on `it-standard` is held to no flat-rate ceiling, computes none of the
flat-rate taxes and issues invoices with VAT: the rate it enters or the profile's
22%, no `Natura`, no stamp duty, `RF01` in the XML. The pack is what says so, not a
branch on the regime code, and the pairing of a code with a pack is refused both ways.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
YEAR = today_local().year


@pytest.fixture
def service(db_session: Session, local_storage: LocalFileStorage) -> InvoiceService:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            name="rossi",
            legal_name="Studio Rossi S.r.l.",
            vat_number="09876543210",
            tax_code="09876543210",
            address="Via Vittorio Veneto 12",
            cap="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="info@example.com",
        ),
        ADMIN,
    )
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    return InvoiceService(db_session, local_storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(
        legal_name="Acme S.r.l.",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        cap="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def test_the_ordinary_pack_proposes_the_regime_and_the_standard_rate(db_session: Session) -> None:
    """A body that names only the pack gets `RF01`, 22%, no `Natura`, no stamp duty and none
    of the flat-rate regime's three income parameters, which are the pack's defaults and not
    the flat-rate regime's."""
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi"), ADMIN
    )
    profile = FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="it-standard"), ADMIN
    )
    assert (profile.pack_id, profile.regime_code) == ("it-standard", "RF01")
    assert profile.default_vat_rate == Decimal("22.00")
    assert profile.default_vat_nature is None and profile.legal_reference is None
    assert profile.apply_stamp_duty is False
    assert profile.profitability_coefficient is None
    assert profile.substitute_tax_rate is None
    assert profile.social_security_rate is None


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"regime_code": "RF19"}, "regime_code"),
        ({"default_vat_rate": Decimal("0.00")}, "default_vat_rate"),
        ({"apply_stamp_duty": True}, "apply_stamp_duty"),
        ({"profitability_coefficient": Decimal("78.00")}, "profitability_coefficient"),
        ({"social_security_rate": Decimal("26.07")}, "social_security_rate"),
        ({"default_vat_nature": "N2.2"}, "default_vat_nature"),
    ],
)
def test_the_ordinary_pack_refuses_what_is_not_its_own(
    db_session: Session, overrides: dict[str, object], field: str
) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi"), ADMIN
    )
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(
            FiscalProfileUpsert(pack_id="it-standard", **overrides), ADMIN
        )
    assert caught.value.details["field"] == field


def test_the_flat_rate_pack_refuses_the_ordinary_code(db_session: Session) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi"), ADMIN
    )
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(
            FiscalProfileUpsert(
                pack_id="it-flat-rate",
                regime_code="RF01",
                default_vat_rate=Decimal("22.00"),
                default_vat_nature=None,
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "regime_code"


def test_an_ordinary_legal_entity_issues_with_vat_and_no_stamp_duty(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Done when: «issues invoices with VAT». A line with no rate takes the profile's
    22%, one with a rate keeps it, the groups are taxed apart, nothing is due as bollo
    and the XML says `RF01` with no `Natura`."""
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[
                InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00")),
                InvoiceLineIn(
                    description="Formazione",
                    unit_price=Decimal("200.00"),
                    vat_rate=Decimal("10.00"),
                ),
            ],
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(issue_date=date(YEAR, 3, 1)), ADMIN)

    assert issued.taxable_amount == Decimal("1200.00")
    assert issued.vat_amount == Decimal("240.00")  # 22% of 1000 and 10% of 200
    assert issued.stamp_duty == Decimal("0.00")
    assert issued.total == Decimal("1440.00")
    row = db_session.get(Invoice, draft.id)
    assert row is not None and row.snapshot is not None
    assert row.snapshot["tax"]["pack_id"] == "it-standard"
    assert row.snapshot["tax"]["regime_code"] == "RF01"

    service.export_xml(draft.id, ADMIN)
    content, _, _ = service.download(draft.id, "xml", ADMIN)
    text = content.decode("utf-8")
    assert "<RegimeFiscale>RF01</RegimeFiscale>" in text
    assert "<AliquotaIVA>22.00</AliquotaIVA>" in text
    assert "<Natura>" not in text
    assert "<DatiBollo>" not in text


def test_an_ordinary_legal_entity_has_no_ceiling_and_no_flat_rate_estimate(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """No room under a threshold it is not subject to, for the headroom and the
    simulator alike, and no substitute tax or contributions invented from the
    forfettario's arithmetic."""
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("90000.00"))],
        ),
        ADMIN,
    )
    service.issue(draft.id, InvoiceIssue(issue_date=date(YEAR, 3, 1)), ADMIN)

    analytics = AnalyticsService(db_session)
    headroom = analytics.ceiling_headroom(YEAR, ADMIN)
    assert (headroom.pack_id, headroom.thresholds) == ("it-standard", [])
    estimate = analytics.get_fiscal_estimate(YEAR, ADMIN)
    assert estimate.revenue == Decimal("90000.00")
    assert estimate.taxable_amount is None
    assert estimate.substitute_tax is None
    assert estimate.contributions is None
    assert estimate.due_total is None


def test_the_economic_overview_draws_no_estimate_for_a_regime_without_one(
    service: InvoiceService, db_session: Session
) -> None:
    """The cash is shown and the flat-rate cards are not: an ordinary legal entity gets no
    card of zeros where the estimate would be, while the flat-rate one keeps its own."""
    overview = AnalyticsService(db_session).economic_overview(YEAR, ADMIN)
    assert overview.tax is None and overview.projected_tax is None
    assert overview.actual_net is None

    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    assert AnalyticsService(db_session).economic_overview(YEAR, ADMIN).tax is not None
