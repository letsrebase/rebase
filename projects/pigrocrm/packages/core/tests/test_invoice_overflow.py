"""A derived amount that will not fit `Numeric(12, 2)` is refused, not flushed.

`invoices/schemas.py`'s header records that this project has paid for the same class of
defect several times: a value beyond a column's capacity reaches Postgres as
`NumericValueOutOfRange`, SQLAlchemy wraps it as `DataError` -- **not** `IntegrityError` --
and `apps/api/.../main.py` registers a handler for `DomainError` only. The result is an
unhandled 500 and, worse, a session left in a failed transaction, so every later statement
on it fails too.

Every previous instance was a column the caller wrote directly, and the sweep that found
them looked at exactly those. This one is the first on a *derived* column, which is why it
survived: `InvoiceLineIn.quantity` and `unit_price` correctly mirror
`Numeric(12, 6)`, and their **product** goes to `invoice_lines.total_price`,
`invoices.taxable_amount` and `invoices.total`, all `Numeric(12, 2)`. No bound on the two
factors can express a bound on the product -- `100000 x 100000` is two perfectly valid
six-digit factors and an eleven-digit result -- so the check cannot live in Pydantic and
belongs in the service, before the flush.

There are two arithmetic routes to the overflow and both are tested here: one line whose own
product is too large, and two hundred lines that each fit while their sum does not.
`InvoiceCreate.lines` allows 200 lines, so the second needs no exotic input at all.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import MAX_LINES, InvoiceCreate, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.invoices.totals import MONEY_MAX_EXCLUSIVE
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")

# The largest amount a `Numeric(12, 2)` column can hold, and the smallest it cannot.
# Written from `MONEY_MAX_EXCLUSIVE` rather than as a literal so the two cannot drift, and
# asserted against a literal below so a mistake in the derivation is not self-consistent.
_LARGEST_STORABLE = MONEY_MAX_EXCLUSIVE - Decimal("0.01")


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(tmp_path / "documents")


@pytest.fixture
def service(db_session: Session, storage: LocalFileStorage) -> InvoiceService:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    return InvoiceService(db_session, storage)


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


def _line(quantity: str, price: str, **kw: object) -> InvoiceLineIn:
    payload: dict[str, object] = {
        "description": "Consulenza",
        "quantity": Decimal(quantity),
        "unit_price": Decimal(price),
    }
    payload.update(kw)
    return InvoiceLineIn(**payload)  # type: ignore[arg-type]


def test_the_bound_is_the_one_postgres_states() -> None:
    """`A field with precision 12, scale 2 must round to an absolute value less than
    10^10`, quoted from the server's own `DETAIL`. Pinned as a literal here because
    everything else in this file derives from `MONEY_MAX_EXCLUSIVE`, and a derivation that
    is wrong in the same way as its uses proves nothing."""
    assert Decimal("10000000000") == MONEY_MAX_EXCLUSIVE
    assert Decimal("9999999999.99") == _LARGEST_STORABLE


def test_a_line_whose_product_overflows_is_refused_with_a_domain_error(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The measured case: both factors validate, their product does not fit.

    `ValidationFailed` and not `DataError`: only the first is a `DomainError`, and only a
    `DomainError` becomes a 422 with a named field instead of a 500.
    """
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(customer_id=customer_id, lines=[_line("100000", "100000")]), ADMIN
        )
    assert caught.value.details["field"] == "lines"
    # The line is named, because an invoice can carry two hundred of them and "one of your
    # lines is too large" is not a message anyone can act on.
    assert "1" in caught.value.details["reason"]


def test_the_refusal_leaves_the_session_usable(service: InvoiceService, customer_id: UUID) -> None:
    """The half of the defect that outlives the request.

    `NumericValueOutOfRange` arrives at flush time, which aborts the transaction: every
    subsequent statement on that session fails with `current transaction is aborted` until
    someone rolls back. A refusal raised *before* the flush leaves nothing to roll back,
    and the proof is that the very next call on the same service succeeds.
    """
    with pytest.raises(ValidationFailed):
        service.create(
            InvoiceCreate(customer_id=customer_id, lines=[_line("100000", "100000")]), ADMIN
        )

    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, lines=[_line("2", "500.00")]), ADMIN
    )
    assert invoice.total == Decimal("1000.00")


def test_a_negative_product_overflows_the_same_way(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The column's limit is on the absolute value, and a discount line is a negative
    amount by design (spec 6.1 rule 6) -- so a check written as `value > limit` would leave
    exactly half of the range open."""
    with pytest.raises(ValidationFailed) as caught:
        service.create(
            InvoiceCreate(customer_id=customer_id, lines=[_line("100000", "-100000")]), ADMIN
        )
    assert caught.value.details["field"] == "lines"


def test_the_largest_storable_line_is_still_accepted(
    service: InvoiceService, customer_id: UUID
) -> None:
    """A bound that refused a value the column holds would be a second defect on top of the
    first: it would make legitimate documents unrepresentable, and only the largest ones."""
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[_line("1", "999999.999999", discount_amount=Decimal("0.01"))],
        ),
        ADMIN,
    )
    assert invoice.total == Decimal("999999.99")

    # 100000 x 100000 is 10^10 exactly -- the first value the column refuses -- and one cent
    # of discount is what turns it into the last value the column accepts. The pair is
    # deliberately one cent apart from `test_a_line_whose_product_overflows_...`, because a
    # bound is only pinned by the two values that straddle it.
    at_the_boundary = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[_line("100000", "100000", discount_amount=Decimal("0.01"))],
        ),
        ADMIN,
    )
    assert at_the_boundary.total == _LARGEST_STORABLE


def test_two_hundred_lines_that_each_fit_can_still_overflow_the_total(
    service: InvoiceService, customer_id: UUID
) -> None:
    """Summation alone reaches the overflow, with no line larger than fifty million.

    This is the arm a per-line check on its own would miss, and `MAX_LINES = 200` is what
    makes it reachable: two hundred lines at 50 000 000.00 sum to 10^10 exactly, which is
    the first value the column cannot hold.

    The fifty million is spelled `100 x 500000` rather than as a unit price, and that is
    itself part of the finding: `unit_price` mirrors `Numeric(12, 6)` and so admits
    only six whole digits, which is exactly the bound that looks like it covers this case
    and does not. The product is where the money scale begins.
    """
    lines = [_line("100", "500000") for _ in range(MAX_LINES)]
    with pytest.raises(ValidationFailed) as caught:
        service.create(InvoiceCreate(customer_id=customer_id, lines=lines), ADMIN)
    assert caught.value.details["field"] in {"taxable_amount", "total"}


def test_replace_lines_refuses_on_the_same_arithmetic(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    """`create` is not the only door. `replace_lines` recomputes every total from the whole
    list, so it reaches the same columns by the same route and must refuse identically."""
    invoice = service.create(
        InvoiceCreate(customer_id=customer_id, lines=[_line("1", "100.00")]), ADMIN
    )
    with pytest.raises(ValidationFailed) as caught:
        service.replace_lines(invoice.id, [_line("100000", "100000")], ADMIN)
    assert caught.value.details["field"] == "lines"

    # And the draft is untouched: a refusal that had already replaced the lines would be a
    # partial write, which is what "one service method, one transaction" forbids.
    unchanged = service.get(invoice.id, ADMIN)
    assert unchanged.total == Decimal("100.00")
    # Read through the repository: `InvoiceRead` carries the totals but not the lines, and
    # the lines are the half `replace_lines` would have already cleared.
    lines = InvoiceRepository(db_session).lines(invoice.id)
    assert [r.total_price for r in lines] == [Decimal("100.00")]
