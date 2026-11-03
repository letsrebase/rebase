"""A customer built as a row, for tests that need one and not the customer service.

A module under `fakes/` and not a `conftest.py` helper, for the reason
`invoice_fixtures.py` states: `fakes.customer_fixtures` is an unambiguous import name
however the three test roots are combined.

The name is unique by default, so two customers in one test never collide on a name a
search or a duplicate check would see.
"""

from uuid import uuid4

from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer


def make_customer(session: Session, **overrides: object) -> Customer:
    """A flushed `Customer`; `overrides` are the columns a test cares about."""
    values: dict[str, object] = {"legal_name": f"ACME {uuid4()}"}
    values.update(overrides)
    customer = Customer(**values)
    session.add(customer)
    session.flush()
    return customer


def make_customer_with_address(session: Session, **overrides: object) -> Customer:
    """A flushed Italian `Customer` with the address an exported invoice requires."""
    values: dict[str, object] = {
        "legal_name": f"Cliente {uuid4()}",
        "address": "Piazza dei Modelli 4",
        "cap": "00187",
        "city": "Roma",
        "province": "RM",
        "country": "IT",
    }
    values.update(overrides)
    return make_customer(session, **values)
