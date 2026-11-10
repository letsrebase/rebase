"""A customer built as a row, for tests that need one and not the customer service.

A module under `fakes/` and not a `conftest.py` helper, for the reason
`invoice_fixtures.py` states: `fakes.customer_fixtures` is an unambiguous import name
however the three test roots are combined.

The name is unique by default, so two organizations in one test never collide on a name a
search or a duplicate check would see.
"""

from uuid import uuid4

from sqlalchemy.orm import Session

from fakes.contacts import points
from pigrocrm.core.organizations.models import Organization


def make_customer(session: Session, **overrides: object) -> Organization:
    """A flushed `Organization`; `overrides` are the columns a test cares about, and `email` and
    `phone` are its primary contact points."""
    values: dict[str, object] = {"legal_name": f"ACME {uuid4()}"}
    values.update(overrides)
    contacts = {key: values.pop(key) for key in ("email", "phone") if key in values}
    if contacts:
        values["contact_points"] = points(**contacts)  # type: ignore[arg-type]
    customer = Organization(**values)
    session.add(customer)
    session.flush()
    return customer


def make_customer_with_address(session: Session, **overrides: object) -> Organization:
    """A flushed Italian `Organization` with the address an exported invoice requires."""
    values: dict[str, object] = {
        "legal_name": f"Cliente {uuid4()}",
        "address": "Piazza dei Modelli 4",
        "postal_code": "00187",
        "city": "Roma",
        "province": "RM",
        "country": "IT",
    }
    values.update(overrides)
    return make_customer(session, **values)
