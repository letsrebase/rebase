"""What a subject hangs on, and who owns it (spec 2026-10-26 §4).

A subject's owners are **its deal's owner and its customer's owner**: for a record linked to
a deal (an task, a timer, the deal itself) the deal's, and the owner of the customer
that deal belongs to; for one linked to a customer, a person or an invoice, the customer's
(and an invoice's deal's). The customer's owner is the field (REB-773), never a guess from
its deals. Both are looked up in bulk, once per detector run, never once per subject.

**The recipient rule, for every detector.** A subject that can hang on a deal is told to
`Links.owners`, the deal's owner and the customer's: the task, timer, deal and hours
types, and the invoice types of `billing.py` (with `invoice_days_unlinked`). A contract and
one of its days have no deal, so the types about them (`contracts.py`, `days.py` and the
contract types of `billing_contracts.py`) are told to the customer's owner alone,
`Links.customer_owner_id`. Either way `Directory.recipients` then scopes the set to the
subject's legal entity and falls back to the space's owner.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.deals.models import Deal
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.people.models import Person


@dataclass(frozen=True)
class Links:
    """The records a subject hangs on and what its recipients and its text need of them."""

    customer_id: UUID | None = None
    customer_name: str | None = None
    customer_owner_id: UUID | None = None
    deal_id: UUID | None = None
    deal_owner_id: UUID | None = None
    legal_entity_id: UUID | None = None

    @property
    def owners(self) -> tuple[UUID | None, UUID | None]:
        return self.deal_owner_id, self.customer_owner_id


@dataclass(frozen=True)
class _Organization:
    id: UUID
    name: str
    owner_id: UUID | None
    legal_entity_id: UUID


@dataclass(frozen=True)
class _Deal:
    id: UUID
    customer_id: UUID
    owner_id: UUID | None
    legal_entity_id: UUID


class LinkResolver:
    """Resolves `(customer, person, deal, invoice)` ids, any of them absent, to `Links`."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._organizations: dict[UUID, _Organization] = {}
        self._deals: dict[UUID, _Deal] = {}
        self._people: dict[UUID, UUID | None] = {}
        self._invoices: dict[UUID, tuple[UUID, UUID | None, UUID]] = {}

    def preload(
        self,
        *,
        organizations: Iterable[UUID | None] = (),
        people: Iterable[UUID | None] = (),
        deals: Iterable[UUID | None] = (),
        invoices: Iterable[UUID | None] = (),
    ) -> None:
        invoice_ids = {value for value in invoices if value is not None}
        if invoice_ids:
            for invoice_id, customer_id, deal_id, legal_entity_id in self.session.execute(
                select(
                    Invoice.id, Invoice.customer_id, Invoice.deal_id, Invoice.legal_entity_id
                ).where(Invoice.id.in_(invoice_ids))
            ):
                self._invoices[invoice_id] = (customer_id, deal_id, legal_entity_id)
        person_ids = {value for value in people if value is not None}
        if person_ids:
            for person_id, person_customer in self.session.execute(
                select(Person.id, Person.customer_id).where(Person.id.in_(person_ids))
            ):
                self._people[person_id] = person_customer
        deal_ids = {value for value in deals if value is not None} | {
            deal_id for _, deal_id, _ in self._invoices.values() if deal_id is not None
        }
        if deal_ids:
            for deal in self.session.execute(
                select(Deal.id, Deal.customer_id, Deal.owner_id, Deal.legal_entity_id).where(
                    Deal.id.in_(deal_ids)
                )
            ):
                self._deals[deal.id] = _Deal(
                    deal.id, deal.customer_id, deal.owner_id, deal.legal_entity_id
                )
        customer_ids = (
            {value for value in organizations if value is not None}
            | {value for value in self._people.values() if value is not None}
            | {deal.customer_id for deal in self._deals.values()}
            | {customer_id for customer_id, _, _ in self._invoices.values()}
        )
        if customer_ids:
            for row in self.session.execute(
                select(
                    Organization.id,
                    Organization.legal_name,
                    Organization.owner_id,
                    Organization.legal_entity_id,
                ).where(Organization.id.in_(customer_ids))
            ):
                self._organizations[row.id] = _Organization(
                    row.id, row.legal_name, row.owner_id, row.legal_entity_id
                )

    def resolve(
        self,
        *,
        customer_id: UUID | None = None,
        person_id: UUID | None = None,
        deal_id: UUID | None = None,
        invoice_id: UUID | None = None,
    ) -> Links:
        """Needs `preload` to have been given these ids."""
        legal_entity_id: UUID | None = None
        if invoice_id is not None and invoice_id in self._invoices:
            invoice_customer, invoice_deal, invoice_legal_entity = self._invoices[invoice_id]
            legal_entity_id = invoice_legal_entity
            customer_id = customer_id or invoice_customer
            deal_id = deal_id or invoice_deal
        if person_id is not None:
            customer_id = customer_id or self._people.get(person_id)
        deal = self._deals.get(deal_id) if deal_id is not None else None
        if deal is not None:
            customer_id = customer_id or deal.customer_id
            legal_entity_id = legal_entity_id or deal.legal_entity_id
        customer = self._organizations.get(customer_id) if customer_id is not None else None
        if customer is not None:
            legal_entity_id = legal_entity_id or customer.legal_entity_id
        return Links(
            customer_id=customer.id if customer is not None else customer_id,
            customer_name=customer.name if customer is not None else None,
            customer_owner_id=customer.owner_id if customer is not None else None,
            deal_id=deal.id if deal is not None else deal_id,
            deal_owner_id=deal.owner_id if deal is not None else None,
            legal_entity_id=legal_entity_id,
        )
