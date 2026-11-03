"""Emission: the one irreversible creation in the product.

Everything here runs on the shared savepoint-backed `db_session`, which is fine for
the rules. The *race* is a separate file, because a single connection with a savepoint
cannot produce concurrency and a test that pretends otherwise proves nothing.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from frozen_clock import freeze_today
from lxml import etree
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import (
    Conflict,
    ImmutableField,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.due_date import due_date_from_terms
from pigrocrm.core.invoices.schemas import (
    SNAPSHOT_VERSION,
    InvoiceCreate,
    InvoiceIssue,
    InvoiceLineIn,
    InvoiceUpdate,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATOR = Actor(id=None, type="user", role="collaborator")
# `today_local()`, not `date.today()`: this test asserts the service's own output
# against this constant, and the two must agree regardless of the host running this
# suite -- a bare `date.today()` here would only coincidentally match the service if
# the test machine happened to be configured for Europe/Rome, which nothing enforces.
TODAY = today_local()


@pytest.fixture(autouse=True)
def _pinned_today(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze_today(monkeypatch, TODAY)


# The other end of the legal issue window. `issue()` refuses a `issue_date` after
# today *and* one before 1 January, so `NEW_YEARS_DAY` and `TODAY` are the only pair of dates
# guaranteed to be accepted whatever day the suite runs on. The obvious "some date in
# this year" -- `date(TODAY.year, 6, 1)`, which these tests used to spell -- is in the
# future from 1 January to 31 May and is refused by the *other* limit, so writing it that
# way only moves the annual failure from one January to five months of winter.
NEW_YEARS_DAY = date(TODAY.year, 1, 1)
# On 1 January those two are the same date: the register is one day old and the whole
# legal window is a single day, so there is no *pair* of dates for a rule about ordering
# between them to be about. The two tests that need two are skipped on that one day
# rather than left to assert the wrong thing -- a back-date to 31 December is refused by
# the closed-year check, which reports the same `field`, so the assertions below would
# still pass while proving nothing at all.
DUE_DATE_LEGAL = pytest.mark.skipif(
    TODAY == NEW_YEARS_DAY,
    reason="il 1 gennaio l'unica data di emissione ammessa e' quella di oggi",
)


@pytest.fixture
def service(db_session: Session, tmp_path) -> InvoiceService:  # type: ignore[no-untyped-def]
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            cap="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    return InvoiceService(db_session, LocalFileStorage(tmp_path / "documents"))


def _customer(db_session: Session, **overrides: object) -> UUID:
    payload: dict[str, object] = {
        "legal_name": "Acme S.r.l.",
        "vat_number": "12345678901",
        "sdi_code": "ABCDEFG",
        "address": "Corso Italia 5",
        "cap": "00100",
        "city": "Roma",
        "province": "RM",
        "country": "IT",
    }
    payload.update(overrides)
    customer = Customer(**payload)  # type: ignore[arg-type]
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _draft(service: InvoiceService, customer_id: UUID, price: str = "1000.00") -> UUID:
    return service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal(price))],
        ),
        ADMIN,
    ).id


# --- the number ---------------------------------------------------------------------


def test_the_first_invoice_of_the_year_is_number_one(
    service: InvoiceService, db_session: Session
) -> None:
    invoice = service.issue(_draft(service, _customer(db_session)), InvoiceIssue(), ADMIN)
    assert invoice.status == "issued"
    assert invoice.year == TODAY.year
    assert invoice.number == 1
    assert invoice.issue_date == TODAY


def test_numbers_are_consecutive(service: InvoiceService, db_session: Session) -> None:
    customer_id = _customer(db_session)
    numbers = [
        service.issue(_draft(service, customer_id), InvoiceIssue(), ADMIN).number for _ in range(3)
    ]
    assert numbers == [1, 2, 3]


def test_the_counter_row_is_created_on_first_use_and_then_incremented(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    service.issue(_draft(service, customer_id), InvoiceIssue(), ADMIN)
    service.issue(_draft(service, customer_id), InvoiceIssue(), ADMIN)
    stored = db_session.execute(
        text("SELECT last_number FROM invoice_counters WHERE year = :year"),
        {"year": TODAY.year},
    ).scalar_one()
    assert stored == 2


def test_a_failed_emission_consumes_no_number(service: InvoiceService, db_session: Session) -> None:
    """The property a `SEQUENCE` cannot give: `nextval()` is non-transactional and
    does not roll back, so every aborted transaction would leave a permanent gap."""
    customer_id = _customer(db_session)
    service.issue(_draft(service, customer_id), InvoiceIssue(), ADMIN)

    broken = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)  # no lines
    with pytest.raises(ValidationFailed):
        service.issue(broken.id, InvoiceIssue(), ADMIN)

    assert service.issue(_draft(service, customer_id), InvoiceIssue(), ADMIN).number == 2


def test_a_draft_that_failed_to_issue_is_still_a_draft(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    broken = service.create(InvoiceCreate(customer_id=customer_id), ADMIN)
    with pytest.raises(ValidationFailed):
        service.issue(broken.id, InvoiceIssue(), ADMIN)
    again = service.get(broken.id, ADMIN)
    assert again.status == "draft"
    assert again.number is None


# --- the date -----------------------------------------------------------------------


def test_the_issue_date_is_a_date_in_the_issuer_s_own_calendar(
    service: InvoiceService, db_session: Session
) -> None:
    """Never a UTC projection of an instant: `toISOString()` on 31 December at
    23:30 CET yields 1 January, i.e. the wrong fiscal year on an immutable
    document. `today_local()` is the local civil date in Europe/Rome regardless of
    the process's own system timezone, and `year` is derived from it, so the year in
    the number and the year on the document cannot disagree."""
    invoice = service.issue(_draft(service, _customer(db_session)), InvoiceIssue(), ADMIN)
    assert invoice.issue_date == TODAY
    assert invoice.year == invoice.issue_date.year


@DUE_DATE_LEGAL
def test_back_dating_inside_the_current_year_is_allowed(
    service: InvoiceService, db_session: Session
) -> None:
    invoice = service.issue(
        _draft(service, _customer(db_session)), InvoiceIssue(issue_date=NEW_YEARS_DAY), ADMIN
    )
    assert invoice.issue_date == NEW_YEARS_DAY
    assert invoice.year == TODAY.year


def test_a_future_date_is_refused(service: InvoiceService, db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.issue(
            _draft(service, _customer(db_session)),
            InvoiceIssue(issue_date=TODAY + timedelta(days=1)),
            ADMIN,
        )
    assert caught.value.details["field"] == "issue_date"


def test_a_date_before_the_first_of_january_is_refused(
    service: InvoiceService, db_session: Session
) -> None:
    """A closed year is closed. It is also the reason the fiscal profile is not
    historicised: no emission ever needs a previous period's parameters."""
    with pytest.raises(ValidationFailed) as caught:
        service.issue(
            _draft(service, _customer(db_session)),
            InvoiceIssue(issue_date=date(TODAY.year - 1, 12, 31)),
            ADMIN,
        )
    assert caught.value.details["field"] == "issue_date"


@DUE_DATE_LEGAL
def test_the_register_must_stay_chronologically_monotonic(
    service: InvoiceService, db_session: Session
) -> None:
    """Read inside the locked transaction, where "the date of the previous number" is
    a safe thing to read."""
    customer_id = _customer(db_session)
    service.issue(_draft(service, customer_id), InvoiceIssue(issue_date=TODAY), ADMIN)
    with pytest.raises(ValidationFailed) as caught:
        service.issue(
            _draft(service, customer_id),
            InvoiceIssue(issue_date=NEW_YEARS_DAY),
            ADMIN,
        )
    assert caught.value.details["field"] == "issue_date"
    # The message, not only the field: `NEW_YEARS_DAY` is a legal date on its own, so the
    # only refusal it can draw is this one. Asserting the field alone would also accept
    # the closed-year refusal, which is what a back-date across 31 December would raise.
    assert "cronologicamente monotono" in caught.value.message


def test_the_same_date_as_the_previous_invoice_is_allowed(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    same = NEW_YEARS_DAY
    service.issue(_draft(service, customer_id), InvoiceIssue(issue_date=same), ADMIN)
    assert (
        service.issue(_draft(service, customer_id), InvoiceIssue(issue_date=same), ADMIN).number
        == 2
    )


def test_the_due_date_comes_from_the_profile(service: InvoiceService, db_session: Session) -> None:
    invoice = service.issue(_draft(service, _customer(db_session)), InvoiceIssue(), ADMIN)
    assert invoice.due_date == invoice.issue_date + timedelta(days=30)


def test_the_due_date_follows_the_customer_terms(
    service: InvoiceService, db_session: Session
) -> None:
    """REB-326: the profile's days are the default, the customer's terms win. Issued on
    22/09 to a customer at «30 giorni fine mese», 2026/21 was due 17/10 (the profile's
    25 days) and the scadenziario, the digest and the reminders all read that date."""
    emisfera = _customer(db_session, payment_days=30, payment_end_of_month=True)
    invoice = service.issue(_draft(service, emisfera), InvoiceIssue(), ADMIN)
    assert invoice.due_date == due_date_from_terms(TODAY, 30, month_end=True)
    assert invoice.due_date != TODAY + timedelta(days=30)

    # Days without the switch: the profile's rule with the customer's number.
    a_sessanta = _customer(db_session, vat_number="12345678902", payment_days=60)
    invoice = service.issue(_draft(service, a_sessanta), InvoiceIssue(), ADMIN)
    assert invoice.due_date == TODAY + timedelta(days=60)


def test_a_due_date_written_on_the_draft_wins_over_the_terms(
    service: InvoiceService, db_session: Session
) -> None:
    """A person who typed a date meant it. On a proforma it travels to the invoice born
    from it; on a draft invoice it stays on the row. Cleared, the terms decide again."""
    emisfera = _customer(db_session, payment_days=30, payment_end_of_month=True)
    proforma = service.create(
        InvoiceCreate(
            customer_id=emisfera,
            type="proforma",
            lines=[InvoiceLineIn(description="Saldo", unit_price=Decimal("4400.00"))],
        ),
        ADMIN,
    )
    service.update(proforma.id, InvoiceUpdate(due_date=date(2026, 11, 15)), ADMIN)
    service.confirm_proforma(proforma.id, ADMIN)
    issued = service.issue(proforma.id, InvoiceIssue(), ADMIN)
    assert issued.due_date == date(2026, 11, 15)

    draft = _draft(service, emisfera)
    service.update(draft, InvoiceUpdate(due_date=date(2026, 12, 1)), ADMIN)
    service.update(draft, InvoiceUpdate(due_date=None), ADMIN)
    issued = service.issue(draft, InvoiceIssue(), ADMIN)
    assert issued.due_date == due_date_from_terms(TODAY, 30, month_end=True)

    # Frozen with the rest of the header once issued.
    with pytest.raises(ImmutableField):
        service.update(issued.id, InvoiceUpdate(due_date=date(2026, 12, 31)), ADMIN)


def test_an_editable_document_says_when_it_would_be_due(
    service: InvoiceService, db_session: Session
) -> None:
    """`expected_due_date`: the date «Emetti» would print if pressed today, so a wrong
    term is caught on the page before the XML leaves. An issued row has `due_date`
    and no forecast."""
    emisfera = _customer(db_session, payment_days=30, payment_end_of_month=True)
    draft = service.get(_draft(service, emisfera), ADMIN)
    assert draft.due_date is None
    assert draft.expected_due_date == due_date_from_terms(TODAY, 30, month_end=True)

    service.update(draft.id, InvoiceUpdate(due_date=date(2026, 12, 1)), ADMIN)
    assert service.get(draft.id, ADMIN).expected_due_date == date(2026, 12, 1)

    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert issued.expected_due_date is None
    assert issued.due_date == date(2026, 12, 1)


# --- the refusals, each naming the field (criterion 9) ------------------------------


def test_an_invoice_with_no_lines_is_refused(service: InvoiceService, db_session: Session) -> None:
    empty = service.create(InvoiceCreate(customer_id=_customer(db_session)), ADMIN)
    with pytest.raises(ValidationFailed) as caught:
        service.issue(empty.id, InvoiceIssue(), ADMIN)
    assert caught.value.details["field"] == "lines"


def test_a_total_of_zero_is_refused(service: InvoiceService, db_session: Session) -> None:
    """A TD01 at zero or below is not an invoice."""
    with pytest.raises(ValidationFailed) as caught:
        service.issue(_draft(service, _customer(db_session), price="0.00"), InvoiceIssue(), ADMIN)
    assert caught.value.details["field"] == "total"


def test_a_negative_total_is_refused(service: InvoiceService, db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.issue(_draft(service, _customer(db_session), price="-10.00"), InvoiceIssue(), ADMIN)
    assert caught.value.details["field"] == "total"


def test_to_customer_with_neither_sdi_nor_certified_email_is_refused_before_the_number_is_taken(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session, sdi_code=None, certified_email=None)
    with pytest.raises(ValidationFailed) as caught:
        service.issue(_draft(service, customer_id), InvoiceIssue(), ADMIN)
    assert caught.value.details["entity"] == "customer"
    assert caught.value.details["field"] == "sdi_code"
    # And nothing was consumed: the counter row does not even exist yet.
    assert (
        db_session.execute(
            text("SELECT count(*) FROM invoice_counters WHERE year = :year"),
            {"year": TODAY.year},
        ).scalar_one()
        == 1
    )
    assert (
        db_session.execute(
            text("SELECT last_number FROM invoice_counters WHERE year = :year"),
            {"year": TODAY.year},
        ).scalar_one()
        == 0
    )


@pytest.mark.parametrize("field", ["cap", "city", "address", "province"])
def test_a_missing_address_part_is_refused_by_name(
    service: InvoiceService, db_session: Session, field: str
) -> None:
    customer_id = _customer(db_session, **{field: None})
    with pytest.raises(ValidationFailed) as caught:
        service.issue(_draft(service, customer_id), InvoiceIssue(), ADMIN)
    assert caught.value.details["field"] == field


def test_a_foreign_customer_is_issued_rather_than_refused_by_name(
    service: InvoiceService, db_session: Session
) -> None:
    """Residual R12, closed. This test used to assert the opposite and was right to:
    `customers.vat_number` accepted only eleven digits, so a foreign VAT could not even
    be stored, and the slice declared foreign customers out of scope rather than issuing
    a file the SdI would reject.

    All four of those layers are gone -- the Italian shape rule now applies only to an
    Italian customer, `check_party_exportable` no longer refuses on `country`, `Provincia`
    is omitted where there is none, and `IdPaese` is read from the customer instead of
    being hard-coded to `IT`. Inverted rather than deleted, so that the change of decision
    is visible in the file that recorded the old one.

    A German customer with no SDI code and no PEC: exactly the shape that was
    unrepresentable, and exactly the shape a European client has.
    """
    customer_id = _customer(
        db_session, country="DE", sdi_code=None, certified_email=None, province=""
    )
    issued = service.issue(_draft(service, customer_id), InvoiceIssue(), ADMIN)

    assert issued.status == "issued"
    assert issued.number is not None, "a foreign invoice consumes a number like any other"


def test_a_draft_whose_customer_changed_country_is_refused_before_the_number_is_spent(
    service: InvoiceService, db_session: Session
) -> None:
    """ORB-32, second half. The lines were computed for an Italian customer and carry
    `N2.2`; the customer is then corrected to GB. Issuing must not copy the stale pair
    into the register: the regime is asked again for every stored line and a
    disagreement refuses by field name, before the counter moves. Replacing the lines
    is the remedy, and after it the same draft issues with `N2.1`."""
    customer_id = _customer(db_session)
    invoice_id = _draft(service, customer_id)
    customer = db_session.get(Customer, customer_id)
    assert customer is not None
    customer.country = "GB"
    customer.province = ""
    customer.cap = "00000"
    customer.sdi_code = None
    db_session.flush()

    with pytest.raises(ValidationFailed) as caught:
        service.issue(invoice_id, InvoiceIssue(), ADMIN)
    assert caught.value.details["field"] == "lines"
    assert "sostituisci le righe" in str(caught.value)
    assert (
        db_session.execute(
            text("SELECT count(*) FROM invoices WHERE number IS NOT NULL AND year = :year"),
            {"year": TODAY.year},
        ).scalar_one()
        == 0
    ), "a refused emission must not have consumed a number"

    service.replace_lines(
        invoice_id,
        [InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ADMIN,
    )
    issued = service.issue(invoice_id, InvoiceIssue(), ADMIN)
    assert issued.status == "issued"
    (line,) = service.lines(issued.id, ADMIN)
    assert line.vat_nature == "N2.1"


def test_issuing_requires_admin_not_merely_write(
    service: InvoiceService, db_session: Session
) -> None:
    """Spec 11: `collaborator` is "writing entities, excluding configuration", and
    consuming a number of the fiscal register sits closer to configuration."""
    with pytest.raises(PermissionDenied) as caught:
        service.issue(_draft(service, _customer(db_session)), InvoiceIssue(), COLLABORATOR)
    assert caught.value.details["required_roles"] == ["admin"]


# --- the freeze ---------------------------------------------------------------------


def test_the_snapshot_freezes_both_parties_and_the_fiscal_parameters(
    service: InvoiceService, db_session: Session
) -> None:
    invoice_id = _draft(service, _customer(db_session))
    issued = service.issue(invoice_id, InvoiceIssue(), ADMIN)
    assert issued.snapshot_version == SNAPSHOT_VERSION
    stored = db_session.execute(
        text("SELECT snapshot FROM invoices WHERE id = :id"), {"id": invoice_id}
    ).scalar_one()
    assert stored["version"] == SNAPSHOT_VERSION
    assert stored["customer"]["legal_name"] == "Acme S.r.l."
    assert stored["legal_entity"]["legal_name"] == "Studio Rossi"
    assert stored["tax"]["regime_code"] == "RF19"


def test_a_customer_who_moves_does_not_rewrite_an_issued_invoice(
    service: InvoiceService, db_session: Session
) -> None:
    """Spec 8.3, the whole point of the snapshot."""
    customer_id = _customer(db_session)
    invoice_id = _draft(service, customer_id)
    service.issue(invoice_id, InvoiceIssue(), ADMIN)
    db_session.execute(
        text("UPDATE customers SET city = 'Torino' WHERE id = :id"), {"id": customer_id}
    )
    stored = db_session.execute(
        text("SELECT snapshot FROM invoices WHERE id = :id"), {"id": invoice_id}
    ).scalar_one()
    assert stored["customer"]["city"] == "Roma"


def test_issuing_records_an_activity_naming_the_number(
    service: InvoiceService, db_session: Session
) -> None:
    invoice = service.issue(_draft(service, _customer(db_session)), InvoiceIssue(), ADMIN)
    entries = ActivityService(db_session).timeline("invoice", invoice.id)
    issued = [entry for entry in entries if entry.kind == "issued"]
    assert issued and issued[0].payload["number"] == 1


# --- issuing from a proforma --------------------------------------------------------


def test_issuing_a_confirmed_proforma_creates_a_new_row_and_consumes_the_proforma(
    service: InvoiceService, db_session: Session
) -> None:
    """Spec 5: not a state change in place. Two rows -- one always mutable, one always
    frozen -- so "immutable after emission" is a property of something that was never
    mutable, rather than one verifiable only by reconstructing the history."""
    customer_id = _customer(db_session)
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            type="proforma",
            memo="Consulenza agosto",
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("500.00"))],
        ),
        ADMIN,
    )
    service.confirm_proforma(proforma.id, ADMIN)

    issued = service.issue(proforma.id, InvoiceIssue(), ADMIN)
    assert issued.id != proforma.id
    assert issued.type == "invoice"
    assert issued.status == "issued"
    assert issued.number == 1
    assert issued.origin_proforma_id == proforma.id
    assert issued.memo == "Consulenza agosto"
    assert [r.description for r in service.lines(issued.id, ADMIN)] == ["Consulenza"]
    assert issued.total == Decimal("500.00")

    consumed = service.get(proforma.id, ADMIN)
    assert consumed.status == "consumed"
    assert consumed.number is None
    assert [r.description for r in service.lines(proforma.id, ADMIN)] == ["Consulenza"]


# --- ORB-56: what the writer would refuse is refused here, before the number ---------


def _nothing_consumed(db_session: Session) -> None:
    """The counter row exists, because the lock creates it, and it still says zero."""
    assert (
        db_session.execute(
            text("SELECT last_number FROM invoice_counters WHERE year = :year"),
            {"year": TODAY.year},
        ).scalar_one()
        == 0
    )


def test_a_name_outside_latin_1_is_refused_before_the_number_is_taken(
    service: InvoiceService, db_session: Session
) -> None:
    """`_text` refuses a code point outside Basic Latin and Latin-1 Supplement when it
    writes `Denominazione`; until ORB-56 the pre-check did not, so this customer was
    issued, owned a register number, and could never be exported. Now the refusal comes
    from `issue`, names the field, and the counter has not moved."""
    customer_id = _customer(db_session, legal_name="Акме ООО")
    draft = _draft(service, customer_id)
    with pytest.raises(ValidationFailed) as caught:
        service.issue(draft, InvoiceIssue(), ADMIN)
    assert caught.value.details["entity"] == "customer"
    assert caught.value.details["field"] == "legal_name"
    _nothing_consumed(db_session)
    again = service.get(draft, ADMIN)
    assert again.status == "draft"
    assert again.number is None


def test_to_certified_email_pasted_from_to_mail_client_is_refused_before_the_number_is_taken(
    service: InvoiceService, db_session: Session
) -> None:
    """`PECDestinatario` is `EmailType` in the vendored schema, a plain `local@domain`
    address; until REB-227 the pre-check measured only its width, so a PEC copied out
    of a mail client with the display name and angle brackets still attached passed
    `issue`, spent a register number, and could never be exported. Now the refusal
    comes from `issue`, names the field, and the counter has not moved."""
    customer_id = _customer(
        db_session, sdi_code=None, certified_email="Studio Rossi <pec@studiorossi.it>"
    )
    draft = _draft(service, customer_id)
    with pytest.raises(ValidationFailed) as caught:
        service.issue(draft, InvoiceIssue(), ADMIN)
    assert caught.value.details["entity"] == "customer"
    assert caught.value.details["field"] == "certified_email"
    _nothing_consumed(db_session)
    again = service.get(draft, ADMIN)
    assert again.status == "draft"
    assert again.number is None


def test_a_foreign_customer_with_no_usable_fiscal_identity_is_refused_before_the_number_is_taken(
    service: InvoiceService, db_session: Session
) -> None:
    """A foreign customer with no VAT number and a fiscal code that is not Italian would
    be written with neither `IdFiscaleIVA` nor `CodiceFiscale`: schema-valid, and refused
    by the SdI (control 00417) after the number was spent. Refused here instead, by the
    field where the foreign identifier belongs."""
    customer_id = _customer(
        db_session,
        vat_number=None,
        tax_code="DE12345678901",
        sdi_code=None,
        cap="EC1V 9HL",
        city="London",
        province=None,
        country="GB",
    )
    draft = _draft(service, customer_id)
    with pytest.raises(ValidationFailed) as caught:
        service.issue(draft, InvoiceIssue(), ADMIN)
    assert caught.value.details["entity"] == "customer"
    assert caught.value.details["field"] == "vat_number"
    _nothing_consumed(db_session)
    again = service.get(draft, ADMIN)
    assert again.status == "draft"
    assert again.number is None


def test_an_unconfirmed_proforma_cannot_be_issued(
    service: InvoiceService, db_session: Session
) -> None:
    proforma = service.create(
        InvoiceCreate(
            customer_id=_customer(db_session),
            type="proforma",
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("500.00"))],
        ),
        ADMIN,
    )
    with pytest.raises(Conflict):
        service.issue(proforma.id, InvoiceIssue(), ADMIN)


def test_a_consumed_proforma_cannot_be_issued_twice(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            type="proforma",
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("500.00"))],
        ),
        ADMIN,
    )
    service.confirm_proforma(proforma.id, ADMIN)
    service.issue(proforma.id, InvoiceIssue(), ADMIN)
    with pytest.raises(Conflict):
        service.issue(proforma.id, InvoiceIssue(), ADMIN)


def test_an_already_issued_invoice_cannot_be_issued_again(
    service: InvoiceService, db_session: Session
) -> None:
    invoice_id = _draft(service, _customer(db_session))
    service.issue(invoice_id, InvoiceIssue(), ADMIN)
    with pytest.raises(Conflict):
        service.issue(invoice_id, InvoiceIssue(), ADMIN)


def test_a_missing_invoice_is_not_found(service: InvoiceService) -> None:
    from uuid import uuid4

    with pytest.raises(NotFound):
        service.issue(uuid4(), InvoiceIssue(), ADMIN)


# --- the accrual period and the proforma date across the emission (ORB-61, ORB-63) -


def test_the_accrual_period_is_frozen_at_issue_like_the_memo(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            accrual_from=date(2026, 8, 1),
            accrual_to=date(2026, 8, 31),
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("500.00"))],
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert (issued.accrual_from, issued.accrual_to) == (date(2026, 8, 1), date(2026, 8, 31))
    with pytest.raises(ImmutableField):
        service.update(issued.id, InvoiceUpdate(accrual_to=date(2026, 9, 30)), ADMIN)
    # And the document keeps what it said.
    again = service.get(issued.id, ADMIN)
    assert (again.accrual_from, again.accrual_to) == (date(2026, 8, 1), date(2026, 8, 31))


def test_to_invoice_issued_from_to_proforma_inherits_the_period_and_takes_its_own_date(
    service: InvoiceService, db_session: Session
) -> None:
    """The period is a fact about the work and travels with it; the date is a fact
    about the register and is decided at issue (spec 6.2). A proforma dated last year
    becomes a invoice dated today, for that year's August work."""
    customer_id = _customer(db_session)
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            type="proforma",
            issue_date=date(2025, 9, 5),
            accrual_from=date(2025, 8, 1),
            accrual_to=date(2025, 8, 31),
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("500.00"))],
        ),
        ADMIN,
    )
    service.confirm_proforma(proforma.id, ADMIN)
    issued = service.issue(proforma.id, InvoiceIssue(), ADMIN)
    assert (issued.accrual_from, issued.accrual_to) == (date(2025, 8, 1), date(2025, 8, 31))
    assert issued.issue_date == today_local()
    assert issued.issue_date != date(2025, 9, 5)
    # The consumed proforma is frozen with the rest: its date is no longer the sender's
    # to move, and neither is its period.
    with pytest.raises(ImmutableField):
        service.update(proforma.id, InvoiceUpdate(issue_date=date(2025, 9, 6)), ADMIN)
    with pytest.raises(ImmutableField):
        service.update(proforma.id, InvoiceUpdate(accrual_to=date(2025, 9, 30)), ADMIN)
    assert service.get(proforma.id, ADMIN).issue_date == date(2025, 9, 5)


# --- the document's own text is checked before the number, like the parties (ORB-140) ---


def test_to_memo_outside_latin_1_is_refused_before_the_number_is_taken(
    service: InvoiceService, db_session: Session
) -> None:
    """`_check_latin` covered the parties (ORB-56) and nothing covered the invoice's own
    text: a causale with a Cyrillic letter passed `issue`, spent a register number, and
    every `export_xml` after refused it by `invoice.memo`. Refused here instead, by
    the same field, with the counter untouched."""
    customer_id = _customer(db_session)
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            memo="Консультация agosto",
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    ).id
    with pytest.raises(ValidationFailed) as caught:
        service.issue(draft, InvoiceIssue(), ADMIN)
    assert caught.value.details["entity"] == "invoice"
    assert caught.value.details["field"] == "memo"
    _nothing_consumed(db_session)
    again = service.get(draft, ADMIN)
    assert again.status == "draft"
    assert again.number is None


def test_a_line_description_outside_latin_1_is_refused_before_the_number_is_taken(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Sviluppo \U0001f600", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    ).id
    with pytest.raises(ValidationFailed) as caught:
        service.issue(draft, InvoiceIssue(), ADMIN)
    assert caught.value.details["entity"] == "invoice_line"
    assert caught.value.details["field"] == "description"
    _nothing_consumed(db_session)
    assert service.get(draft, ADMIN).status == "draft"


def test_an_em_dash_in_the_memo_is_issued_and_its_xml_spells_to_hyphen(
    service: InvoiceService, db_session: Session
) -> None:
    """The case that opened ORB-140: a causale typed with an em dash. It is issued, the
    row keeps the text as typed, and the FatturaPA file carries the Latin-1 spelling the
    schema admits."""
    customer_id = _customer(db_session)
    memo = "Consulting services — August 2026"
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            memo=memo,
            lines=[InvoiceLineIn(description="Consulting — August", unit_price=Decimal("1000.00"))],
        ),
        ADMIN,
    ).id
    issued = service.issue(draft, InvoiceIssue(), ADMIN)
    assert issued.memo == memo
    service.export_xml(issued.id, ADMIN)
    data, _, _ = service.download(issued.id, "xml", ADMIN)
    root = etree.fromstring(data)
    assert root.findtext(".//Causale") == "Consulting services - August 2026"
    assert root.findtext(".//Descrizione") == "Consulting - August"


# --- the purchase order across the emission (REB-693) --------------------------------


def test_the_purchase_order_is_frozen_at_issue_like_the_memo(
    service: InvoiceService, db_session: Session
) -> None:
    customer_id = _customer(db_session)
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            purchase_order_number="ODA-14",
            purchase_order_tender_code="Z0A1B2C3D4",
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("250.00"))],
        ),
        ADMIN,
    )
    issued = service.issue(draft.id, InvoiceIssue(), ADMIN)
    assert (issued.purchase_order_number, issued.purchase_order_tender_code) == (
        "ODA-14",
        "Z0A1B2C3D4",
    )
    with pytest.raises(ImmutableField):
        service.update(issued.id, InvoiceUpdate(purchase_order_tender_code="Z9Z9Z9Z9Z9"), ADMIN)
    assert service.get(issued.id, ADMIN).purchase_order_tender_code == "Z0A1B2C3D4"


def test_to_invoice_issued_from_to_proforma_answers_the_proforma_s_order(
    service: InvoiceService, db_session: Session
) -> None:
    """The order is the customer's and the invoice answers it, so it travels from the
    proforma like the period does; the consumed proforma is frozen with it."""
    customer_id = _customer(db_session)
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            type="proforma",
            purchase_order_number="ODA-14",
            purchase_order_date=date(2026, 10, 9),
            purchase_order_tender_code="Z0A1B2C3D4",
            purchase_order_project_code="J41E22000110005",
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("250.00"))],
        ),
        ADMIN,
    )
    service.confirm_proforma(proforma.id, ADMIN)
    issued = service.issue(proforma.id, InvoiceIssue(), ADMIN)
    assert issued.id != proforma.id
    assert (
        issued.purchase_order_number,
        issued.purchase_order_date,
        issued.purchase_order_tender_code,
        issued.purchase_order_project_code,
    ) == ("ODA-14", date(2026, 10, 9), "Z0A1B2C3D4", "J41E22000110005")
    for change in (
        InvoiceUpdate(purchase_order_number="ODA-15"),
        InvoiceUpdate(purchase_order_date=date(2026, 10, 10)),
        InvoiceUpdate(purchase_order_tender_code="Z9Z9Z9Z9Z9"),
        InvoiceUpdate(purchase_order_project_code=None),
    ):
        with pytest.raises(ImmutableField):
            service.update(proforma.id, change, ADMIN)


def test_an_order_number_the_xml_cannot_carry_is_refused_before_the_number_is_spent(
    service: InvoiceService, db_session: Session
) -> None:
    """A value written by another path than the service (a fix-up script, raw SQL) is
    measured with the writer's own rules before the counter moves, like the causale
    since ORB-140."""
    customer_id = _customer(db_session)
    draft_id = _draft(service, customer_id)
    db_session.execute(
        text("UPDATE invoices SET purchase_order_number = 'Ordine citt\u00e0 5' WHERE id = :id"),
        {"id": draft_id},
    )
    db_session.expire_all()
    with pytest.raises(ValidationFailed) as caught:
        service.issue(draft_id, InvoiceIssue(), ADMIN)
    assert caught.value.details["field"] == "purchase_order_number"
    assert service.get(draft_id, ADMIN).number is None
