"""Who the CRM already knows, by email address.

This module *is* the relevance rule of spec 4.2. There are no rules to configure, no
filters to maintain and no domain lists to curate: the set of relevant addresses is
the address book, and keeping it current is work the user was doing anyway. The good
consequence is that making a conversation appear means adding the person to the CRM,
which is the action they wanted to take regardless.

The rule this module must never break: an address discovered *inside* a thread does
not enter the roster (spec 4.3). If it did, relevance would widen by itself on every
cycle, which is exactly the failure mode spec 4 exists to prevent.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.gmail.query import customer_domain, is_provider_domain
from pigrocrm.core.people.models import Person


@dataclass(frozen=True)
class EntityRef:
    """A CRM entity an email concerns. `entity_type` is a plain `str` and not
    `fields.EntityType`: that literal is the *custom-field* entity type and Gmail adds
    no custom fields to anything. Widening it here would offer administrators custom
    fields on a credential row."""

    entity_type: str
    entity_id: UUID


class AddressRoster:
    def __init__(self, session: Session) -> None:
        self.session = session

    def known_addresses(self) -> tuple[str, ...]:
        """Every non-null email on a live Person or Customer, lowercased, deduplicated
        and sorted. Sorted because the batching in `gmail/query.py` must be stable: an
        unstable order means two consecutive syncs issue different `q` strings for the
        same data, and the request-inspecting test in B1-7 could then pass by luck."""
        people = select(func.lower(Person.email)).where(
            Person.email.is_not(None), Person.deleted_at.is_(None)
        )
        customers = select(func.lower(Customer.email)).where(
            Customer.email.is_not(None), Customer.deleted_at.is_(None)
        )
        rows = self.session.execute(people.union(customers)).scalars().all()
        # `if address` drops the empty string as well as any NULL the union let past:
        # a row saved with "" instead of NULL is not an address, and an empty clause in
        # the Gmail `q` would match on nothing while still costing length budget.
        return tuple(sorted(address for address in rows if address))

    def customer_domains(self) -> frozenset[str]:
        """The domains the CRM already files under a customer: each live customer's own
        (its website's and its non-webmail email's) and the address domain
        of each live person attached to a live customer. What a customer proposal
        (REB-223) must not offer again, and what an import of one refuses to duplicate.
        Webmail domains never appear: they name nobody in particular."""
        customers = self.session.execute(
            select(Customer.sito_web, Customer.email).where(Customer.deleted_at.is_(None))
        ).all()
        # Both of them: a customer whose website is acme.com and whose email is at
        # acme.it is at both, and proposing the other one would make a second Acme.
        domains: set[str] = set()
        for sito_web, email in customers:
            for domain in (
                customer_domain(sito_web=sito_web, email=None),
                customer_domain(sito_web=None, email=email),
            ):
                if domain is not None:
                    domains.add(domain)
        people = self.session.execute(
            select(Person.email)
            .join(Customer, Customer.id == Person.customer_id)
            .where(
                Person.email.is_not(None),
                Person.deleted_at.is_(None),
                Customer.deleted_at.is_(None),
            )
        ).scalars()
        for email in people:
            domain = (email or "").strip().lower().rpartition("@")[2]
            if domain and not is_provider_domain(domain):
                domains.add(domain)
        return frozenset(domains)

    def resolve(self, address: str) -> tuple[EntityRef, ...]:
        """Every entity one address touches: the person, that person's customer, the
        customer itself, and that customer's live deals."""
        needle = address.strip().lower()
        if not needle:
            # Not merely an optimisation: a message whose From header failed to parse
            # would otherwise run `lower(email) = ''` and file itself against every row
            # somebody saved with an empty string instead of NULL.
            return ()

        refs: list[EntityRef] = []
        customer_ids: set[UUID] = set()

        person_rows = self.session.execute(
            select(Person.id, Person.customer_id).where(
                func.lower(Person.email) == needle, Person.deleted_at.is_(None)
            )
        ).all()
        for person_id, customer_id in person_rows:
            refs.append(EntityRef("person", person_id))
            if customer_id is not None:
                customer_ids.add(customer_id)

        direct = (
            self.session.execute(
                select(Customer.id).where(
                    func.lower(Customer.email) == needle, Customer.deleted_at.is_(None)
                )
            )
            .scalars()
            .all()
        )
        # A live person may point at a soft-deleted customer, so the customers reached
        # through `people.customer_id` are re-checked here rather than trusted: filing
        # a message against an archived row would produce a link nothing can display.
        reachable = (
            set(
                self.session.execute(
                    select(Customer.id).where(
                        Customer.id.in_(customer_ids), Customer.deleted_at.is_(None)
                    )
                )
                .scalars()
                .all()
            )
            if customer_ids
            else set()
        )
        customer_ids = reachable | set(direct)

        for customer_id in customer_ids:
            refs.append(EntityRef("customer", customer_id))

        if customer_ids:
            deal_ids = (
                self.session.execute(
                    select(Deal.id).where(
                        Deal.customer_id.in_(customer_ids), Deal.deleted_at.is_(None)
                    )
                )
                .scalars()
                .all()
            )
            refs.extend(EntityRef("deal", deal_id) for deal_id in deal_ids)

        # Deduplicated because a customer reached through two of its own people is one
        # customer. Order is not part of the contract; the test compares as a set.
        return tuple(dict.fromkeys(refs))
