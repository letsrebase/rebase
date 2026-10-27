"""The ordinary regime has a pack of its own (REB-760).

An azienda on `it-standard` is held to no forfettario ceiling, computes none of the
forfettario's taxes and issues invoices with VAT: the rate it enters or the profile's
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
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
ANNO = oggi_in_italia().year


@pytest.fixture
def service(db_session: Session, local_storage: LocalFileStorage) -> InvoiceService:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            nome="rossi",
            ragione_sociale="Studio Rossi S.r.l.",
            partita_iva="09876543210",
            codice_fiscale="09876543210",
            indirizzo="Via Vittorio Veneto 12",
            cap="20124",
            comune="Milano",
            provincia="MI",
            nazione="IT",
            email="info@example.com",
        ),
        ADMIN,
    )
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    return InvoiceService(db_session, local_storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    customer = Customer(
        ragione_sociale="Acme S.r.l.",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def test_the_ordinary_pack_proposes_the_regime_and_the_standard_rate(db_session: Session) -> None:
    """A body that names only the pack gets `RF01`, 22%, no `Natura`, no bollo and none
    of the forfettario's three income parameters, which are the pack's defaults and not
    the forfettario's."""
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(ragione_sociale="Studio Rossi"), ADMIN
    )
    profile = FiscalProfileService(db_session).upsert(
        FiscalProfileUpsert(pack_id="it-standard"), ADMIN
    )
    assert (profile.pack_id, profile.codice_regime) == ("it-standard", "RF01")
    assert profile.aliquota_iva_default == Decimal("22.00")
    assert profile.natura_default is None and profile.riferimento_normativo is None
    assert profile.applica_bollo is False
    assert profile.coefficiente_redditivita is None
    assert profile.aliquota_imposta_sostitutiva is None
    assert profile.aliquota_inps is None


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"codice_regime": "RF19"}, "codice_regime"),
        ({"aliquota_iva_default": Decimal("0.00")}, "aliquota_iva_default"),
        ({"applica_bollo": True}, "applica_bollo"),
        ({"coefficiente_redditivita": Decimal("78.00")}, "coefficiente_redditivita"),
        ({"aliquota_inps": Decimal("26.07")}, "aliquota_inps"),
        ({"natura_default": "N2.2"}, "natura_default"),
    ],
)
def test_the_ordinary_pack_refuses_what_is_not_its_own(
    db_session: Session, overrides: dict[str, object], field: str
) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(ragione_sociale="Studio Rossi"), ADMIN
    )
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(
            FiscalProfileUpsert(pack_id="it-standard", **overrides), ADMIN
        )
    assert caught.value.details["field"] == field


def test_the_forfettario_pack_refuses_the_ordinary_code(db_session: Session) -> None:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(ragione_sociale="Studio Rossi"), ADMIN
    )
    with pytest.raises(ValidationFailed) as caught:
        FiscalProfileService(db_session).upsert(
            FiscalProfileUpsert(
                pack_id="it-flat-rate",
                codice_regime="RF01",
                aliquota_iva_default=Decimal("22.00"),
                natura_default=None,
            ),
            ADMIN,
        )
    assert caught.value.details["field"] == "codice_regime"


def test_an_ordinary_azienda_issues_with_vat_and_no_stamp_duty(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """Done when: «issues invoices with VAT». A line with no rate takes the profile's
    22%, one with a rate keeps it, the groups are taxed apart, nothing is due as bollo
    and the XML says `RF01` with no `Natura`."""
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[
                InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1000.00")),
                InvoiceLineIn(
                    descrizione="Formazione",
                    prezzo_unitario=Decimal("200.00"),
                    aliquota_iva=Decimal("10.00"),
                ),
            ],
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(data_emissione=date(ANNO, 3, 1)), ADMIN)

    assert issued.imponibile == Decimal("1200.00")
    assert issued.imposta == Decimal("240.00")  # 22% of 1000 and 10% of 200
    assert issued.bollo == Decimal("0.00")
    assert issued.totale == Decimal("1440.00")
    row = db_session.get(Invoice, draft.id)
    assert row is not None and row.snapshot is not None
    assert row.snapshot["fiscale"]["pack_id"] == "it-standard"
    assert row.snapshot["fiscale"]["codice_regime"] == "RF01"

    service.export_xml(draft.id, ADMIN)
    content, _, _ = service.download(draft.id, "xml", ADMIN)
    text = content.decode("utf-8")
    assert "<RegimeFiscale>RF01</RegimeFiscale>" in text
    assert "<AliquotaIVA>22.00</AliquotaIVA>" in text
    assert "<Natura>" not in text
    assert "<DatiBollo>" not in text


def test_an_ordinary_azienda_has_no_ceiling_and_no_forfettario_estimate(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """No room under a threshold it is not subject to, for the headroom and the
    simulator alike, and no substitute tax or contributions invented from the
    forfettario's arithmetic."""
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("90000.00"))],
        ),
        ADMIN,
    )
    service.issue(draft.id, InvoiceIssue(data_emissione=date(ANNO, 3, 1)), ADMIN)

    analytics = AnalyticsService(db_session)
    headroom = analytics.ceiling_headroom(ANNO, ADMIN)
    assert (headroom.pack_id, headroom.soglie) == ("it-standard", [])
    estimate = analytics.get_fiscal_estimate(ANNO, ADMIN)
    assert estimate.ricavi == Decimal("90000.00")
    assert estimate.imponibile is None
    assert estimate.imposta_sostitutiva is None
    assert estimate.contributi is None
    assert estimate.totale_dovuto is None
