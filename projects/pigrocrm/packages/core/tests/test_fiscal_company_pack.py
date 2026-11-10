"""The `it-company` pack, an srl's (REB-1005, taxes design § 1).

It invoices exactly like `it-standard` (RF01, the same regime strategy) and differs in what
it owes: the two rates it carries are verified law. An existing srl moves to it from the
next 1 January, never mid-year, and a year that spans the move reads as one `RF01` row in
the register.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK, IT_STANDARD_PACK, resolve_pack
from pigrocrm.core.fiscal.regime import ORDINARY, frozen_pack_id, resolve_regime
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.fiscal.year_roll import FiscalYearRoll, is_srl_name
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
YEAR = 2026


@pytest.fixture
def service(db_session: Session, local_storage: LocalFileStorage) -> InvoiceService:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            name="rossi",
            legal_name="Studio Rossi S.r.l.",
            vat_number="09876543210",
            tax_code="09876543210",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="info@example.com",
        ),
        ADMIN,
    )
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-company"), ADMIN)
    return InvoiceService(db_session, local_storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Organization(
        legal_name="Acme S.r.l.",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        postal_code="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _issue(service: InvoiceService, customer_id: UUID, day: date) -> UUID:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    service.issue(draft.id, InvoiceIssue(issue_date=day), ADMIN)
    return draft.id


def test_the_pack_carries_the_two_verified_rates_and_no_ceiling_or_charge() -> None:
    assert resolve_pack("it-company", "1") is IT_COMPANY_PACK
    assert IT_COMPANY_PACK.ceilings == () and IT_COMPANY_PACK.charges == ()
    law = IT_COMPANY_PACK.law_for(2026)
    assert law is not None and law.company_tax is not None
    assert law.company_tax.ires_rate == Decimal("24.00")
    assert law.company_tax.irap_ordinary_rate == Decimal("3.90")
    assert law.company_tax.irap_regional_variation_max_points == Decimal("0.92")
    # Nothing is invented before the verified entry, and no person's estimate is carried.
    assert IT_COMPANY_PACK.law_for(2025) is None
    assert not law.has_income_estimate
    assert IT_STANDARD_PACK.law_for(2026) is not None
    assert IT_STANDARD_PACK.law_for(2026).company_tax is None  # type: ignore[union-attr]


def test_rf01_belongs_to_both_ordinary_packs_and_to_no_other() -> None:
    assert resolve_regime("RF01", "it-standard") is ORDINARY
    assert resolve_regime("RF01", "it-company") is ORDINARY
    with pytest.raises(ValidationFailed) as flat_rate:
        resolve_regime("RF01", "it-flat-rate")
    assert "it-company" in flat_rate.value.message
    with pytest.raises(ValidationFailed):
        resolve_regime("RF19", "it-company")
    assert frozen_pack_id("RF01", "it-company") == "it-company"


def test_the_company_pack_proposes_the_ordinary_defaults(db_session: Session) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi S.r.l."), ADMIN
    )
    profile = FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="it-company"), ADMIN
    )
    assert (profile.pack_id, profile.regime_code) == ("it-company", "RF01")
    assert profile.default_vat_rate == Decimal("22.00")
    assert profile.default_vat_nature is None and profile.apply_stamp_duty is False
    assert profile.profitability_coefficient is None


def test_the_company_pack_refuses_a_zero_default_rate(db_session: Session) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi S.r.l."), ADMIN
    )
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(
            FiscalProfileUpsert(pack_id="it-company", default_vat_rate=Decimal("0.00")), ADMIN
        )
    assert caught.value.details["field"] == "default_vat_rate"


def test_an_srl_invoices_as_rf01_with_vat(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id, date(YEAR, 3, 1))
    row = db_session.get(Invoice, invoice_id)
    assert row is not None and row.snapshot is not None
    assert row.snapshot["tax"]["pack_id"] == "it-company"
    assert row.snapshot["tax"]["regime_code"] == "RF01"
    assert row.vat_amount == Decimal("220.00") and row.stamp_duty == Decimal("0.00")

    service.export_xml(invoice_id, ADMIN)
    content, _, _ = service.download(invoice_id, "xml", ADMIN)
    text = content.decode("utf-8")
    assert "<RegimeFiscale>RF01</RegimeFiscale>" in text
    assert "<AliquotaIVA>22.00</AliquotaIVA>" in text
    assert "<DatiBollo>" not in text


def test_a_year_that_spans_the_switch_reads_as_one_rf01_row(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    _issue(service, customer_id, date(YEAR, 3, 1))
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-company"), ADMIN)
    _issue(service, customer_id, date(YEAR, 9, 1))

    overview = AnalyticsService(db_session).year_overview(YEAR, ADMIN)

    assert [(r.regime_code, r.pack_id, r.invoices, r.revenue) for r in overview.regimes] == [
        ("RF01", "it-company", 2, Decimal("2000.00"))
    ]


def _pack_of_default_entity(session: Session) -> str:
    return session.scalars(select(FiscalProfile.pack_id)).one()


def _entity(session: Session, name: str, pack: str, *, active: bool = True) -> UUID:
    entity = LegalEntity(name=name[:80], legal_name=name, active=active)
    session.add(entity)
    session.flush()
    body = {"pack_id": pack, **({"regime_code": "RF19"} if pack == "it-flat-rate" else {})}
    FiscalProfileService(session).upsert(FiscalProfileUpsert.model_validate(body), ADMIN, entity.id)
    return entity.id


def _pack(session: Session, legal_entity_id: UUID) -> str:
    return FiscalProfileService(session).get(ADMIN, legal_entity_id).pack_id


@pytest.mark.parametrize(
    ("name", "is_srl"),
    [
        ("Studio Rossi S.r.l.", True),
        ("Rossi srl", True),
        ("Rossi S. R. L. S.", True),
        ("Rossi Societ\u00e0 a Responsabilit\u00e0 Limitata", True),
        ("Mario Rossi", False),
        ("Israel Consulting", False),
        ("Sorrel Ltd", False),
    ],
)
def test_the_denomination_says_whether_an_entity_is_an_srl(name: str, is_srl: bool) -> None:
    assert is_srl_name(name) is is_srl


def test_an_existing_srl_moves_on_the_next_first_of_january(db_session: Session) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi S.r.l."), ADMIN
    )
    FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="it-standard", iban="IT60X0542811101000000123456"), ADMIN
    )
    roll = FiscalYearRoll(db_session)

    for day in (date(2026, 11, 10), date(2026, 12, 31), date(2027, 1, 2), date(2027, 6, 30)):
        assert roll.run(today=day, dry_run=False).due is False
    assert _pack_of_default_entity(db_session) == "it-standard"

    rehearsal = roll.run(today=date(2027, 1, 1), dry_run=True)
    assert (rehearsal.due, rehearsal.moved) == (True, 1)
    assert _pack_of_default_entity(db_session) == "it-standard"

    report = roll.run(today=date(2027, 1, 1), dry_run=False)
    assert (report.due, report.moved, report.failed) == (True, 1, 0)
    profile = FiscalProfileService(db_session).get(ADMIN)
    assert (profile.pack_id, profile.regime_code) == ("it-company", "RF01")
    assert profile.iban == "IT60X0542811101000000123456"
    # Idempotent: the entity is on the new pack and a second pass finds nothing to move.
    assert roll.run(today=date(2027, 1, 1), dry_run=False).moved == 0


def test_only_active_srl_entities_on_it_standard_move(db_session: Session) -> None:
    """A person on the ordinary regime beside an srl in one space, an srl already on the
    company pack, a flat-rate one and a deactivated one all stay where they are."""
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Mario Rossi"), ADMIN
    )
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    moving = _entity(db_session, "Alfa S.r.l.", "it-standard")
    already = _entity(db_session, "Beta S.r.l.", "it-company")
    flat_rate = _entity(db_session, "Gamma S.r.l.", "it-flat-rate")
    inactive = _entity(db_session, "Delta S.r.l.", "it-standard", active=False)

    report = FiscalYearRoll(db_session).run(today=date(2027, 1, 1), dry_run=False)

    assert (report.moved, report.failed) == (1, 0)
    assert _pack_of_default_entity_named(db_session, "Mario Rossi") == "it-standard"
    assert _pack(db_session, moving) == "it-company"
    assert _pack(db_session, already) == "it-company"
    assert _pack(db_session, flat_rate) == "it-flat-rate"
    assert _pack(db_session, inactive) == "it-standard"


def _pack_of_default_entity_named(session: Session, legal_name: str) -> str:
    return session.scalars(
        select(FiscalProfile.pack_id)
        .join(LegalEntity, LegalEntity.id == FiscalProfile.legal_entity_id)
        .where(LegalEntity.legal_name == legal_name)
    ).one()


def test_one_refused_entity_does_not_keep_the_others_on_the_old_pack(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Alfa S.r.l."), ADMIN
    )
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    other = _entity(db_session, "Beta S.r.l.", "it-standard")
    real = FiscalProfileService.upsert

    def refusing(
        self: FiscalProfileService,
        data: FiscalProfileUpsert,
        actor: Actor,
        legal_entity_id: UUID | None = None,
    ) -> object:
        if legal_entity_id is None or legal_entity_id != other:
            raise ValidationFailed("fiscal_profile", "regime_code", "refused")
        return real(self, data, actor, legal_entity_id)

    monkeypatch.setattr(FiscalProfileService, "upsert", refusing)
    report = FiscalYearRoll(db_session).run(today=date(2027, 1, 1), dry_run=False)

    assert (report.moved, report.failed) == (1, 1)
    assert _pack(db_session, other) == "it-company"
