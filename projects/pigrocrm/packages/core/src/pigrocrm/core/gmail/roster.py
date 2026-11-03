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

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import escape_like
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
        # `func.lower(...)` is untyped (`Function[Any]`), and a select of it is a select
        # of unknown shape whose `scalars()` SQLAlchemy 2.1 cannot type: the annotation
        # says what the function returns, and the union's rows are `str` from there.
        # `str`, not `str | None`, although both columns are nullable: each `WHERE` below
        # keeps only the rows that have an address, and the annotation goes with that filter.
        person_email: ColumnElement[str] = func.lower(Person.email)
        customer_email: ColumnElement[str] = func.lower(Customer.email)
        people = select(person_email).where(Person.email.is_not(None), Person.deleted_at.is_(None))
        customers = select(customer_email).where(
            Customer.email.is_not(None), Customer.deleted_at.is_(None)
        )
        rows = self.session.execute(people.union(customers)).scalars().all()
        # `if address` drops the empty string as well as any NULL the union let past:
        # a row saved with "" instead of NULL is not an address, and an empty clause in
        # the Gmail `q` would match on nothing while still costing length budget.
        return tuple(sorted(address for address in rows if address))

    def known_among(self, addresses: Iterable[str]) -> frozenset[str]:
        """Which of these candidate addresses are on a live Person or Customer, lowercased.

        What a caller that already holds its candidates asks, instead of loading the whole
        roster (`known_addresses`) to test a handful of them: the lookup is
        `lower(email) IN (...)`, the expression the `ix_people_email_lower` and
        `ix_customers_email_lower` indexes are built on."""
        needles = _needles(addresses)
        if not needles:
            return frozenset()
        person_email: ColumnElement[str] = func.lower(Person.email)
        customer_email: ColumnElement[str] = func.lower(Customer.email)
        people = select(person_email).where(person_email.in_(needles), Person.deleted_at.is_(None))
        customers = select(customer_email).where(
            customer_email.in_(needles), Customer.deleted_at.is_(None)
        )
        return frozenset(self.session.execute(people.union(customers)).scalars().all())

    def domains_taken(self, domains: Iterable[str]) -> frozenset[str]:
        """Which of these candidate domains the CRM already files under a customer: each
        live customer's own (its website's and its non-webmail email's) and the address
        domain of each live person attached to a live customer. What a customer proposal
        (REB-223) must not offer again, and what an import of one refuses to duplicate.
        Webmail domains never appear: they name nobody in particular.

        The database narrows to the rows that can name one of the candidates (a website
        or an email containing it) and Python applies the rule, so the whole of the
        customer and people tables is never read into memory."""
        candidates = {domain.strip().lower() for domain in domains if domain and domain.strip()}
        if not candidates:
            return frozenset()
        customer_hits = or_(
            *(
                clause
                for domain in candidates
                for clause in (
                    Customer.website.ilike(f"%{escape_like(domain)}%", escape="\\"),
                    Customer.email.ilike(f"%{escape_like(domain)}%", escape="\\"),
                )
            )
        )
        customers = self.session.execute(
            select(Customer.website, Customer.email).where(
                Customer.deleted_at.is_(None), customer_hits
            )
        ).all()
        person_hits = or_(
            *(Person.email.ilike(f"%@{escape_like(domain)}", escape="\\") for domain in candidates)
        )
        people = self.session.execute(
            select(Person.email)
            .join(Customer, Customer.id == Person.customer_id)
            .where(
                Person.email.is_not(None),
                Person.deleted_at.is_(None),
                Customer.deleted_at.is_(None),
                person_hits,
            )
        ).scalars()
        return _domains_of(customers, people) & candidates

    def resolve_many(self, addresses: Iterable[str]) -> dict[str, tuple[EntityRef, ...]]:
        """Every entity each of these addresses touches: the person, that person's
        customer, the customer itself, and that customer's live deals.

        Keyed by the lowercased, stripped address (`address_key`); an address that touches
        nothing is absent. A fixed number of queries however many addresses there are --
        four at most, where one `resolve` per address cost up to four *each* -- so a
        conversation is resolved in one go and a cycle's cost does not grow with the
        number of people copied on its threads.
        """
        needles = _needles(addresses)
        if not needles:
            # An empty needle is not merely an optimisation to skip: a message whose From
            # header failed to parse would otherwise run `lower(email) = ''` and file
            # itself against every row somebody saved with an empty string instead of NULL.
            return {}

        person_email: ColumnElement[str] = func.lower(Person.email)
        customer_email: ColumnElement[str] = func.lower(Customer.email)
        person_rows = self.session.execute(
            select(person_email, Person.id, Person.customer_id).where(
                person_email.in_(needles), Person.deleted_at.is_(None)
            )
        ).all()
        direct_rows = self.session.execute(
            select(customer_email, Customer.id).where(
                customer_email.in_(needles), Customer.deleted_at.is_(None)
            )
        ).all()
        # A live person may point at a soft-deleted customer, so the customers reached
        # through `people.customer_id` are re-checked here rather than trusted: filing
        # a message against an archived row would produce a link nothing can display.
        via_people = {customer_id for _, _, customer_id in person_rows if customer_id is not None}
        live_via_people = (
            set(
                self.session.execute(
                    select(Customer.id).where(
                        Customer.id.in_(via_people), Customer.deleted_at.is_(None)
                    )
                )
                .scalars()
                .all()
            )
            if via_people
            else set()
        )

        persons: dict[str, list[UUID]] = {}
        customers: dict[str, set[UUID]] = {}
        for needle, person_id, customer_id in person_rows:
            persons.setdefault(needle, []).append(person_id)
            if customer_id in live_via_people:
                customers.setdefault(needle, set()).add(customer_id)
        for needle, customer_id in direct_rows:
            customers.setdefault(needle, set()).add(customer_id)

        all_customers = {customer_id for ids in customers.values() for customer_id in ids}
        deals: dict[UUID, list[UUID]] = {}
        if all_customers:
            for customer_id, deal_id in self.session.execute(
                select(Deal.customer_id, Deal.id).where(
                    Deal.customer_id.in_(all_customers), Deal.deleted_at.is_(None)
                )
            ).all():
                deals.setdefault(customer_id, []).append(deal_id)

        resolved: dict[str, tuple[EntityRef, ...]] = {}
        for needle in persons.keys() | customers.keys():
            refs = [EntityRef("person", person_id) for person_id in persons.get(needle, [])]
            customer_ids = sorted(customers.get(needle, ()))
            refs.extend(EntityRef("customer", customer_id) for customer_id in customer_ids)
            for customer_id in customer_ids:
                refs.extend(EntityRef("deal", deal_id) for deal_id in deals.get(customer_id, []))
            # Deduplicated because a customer reached through two of its own people is one
            # customer. Order is not part of the contract; the test compares as a set.
            resolved[needle] = tuple(dict.fromkeys(refs))
        return resolved


def address_key(address: str) -> str:
    """How an address is looked up: stripped and lowercased, as the roster stores it."""
    return address.strip().lower()


def _needles(addresses: Iterable[str]) -> list[str]:
    return sorted({key for address in addresses if (key := address_key(address))})


def _domains_of(customers: Iterable[Any], people_emails: Iterable[str | None]) -> frozenset[str]:
    """The domains a set of customer rows and of their people's addresses stand for."""
    domains: set[str] = set()
    for website, email in customers:
        # Both of them: a customer whose website is acme.com and whose email is at
        # acme.it is at both, and proposing the other one would make a second Acme.
        for domain in (
            customer_domain(website=website, email=None),
            customer_domain(website=None, email=email),
        ):
            if domain is not None:
                domains.add(domain)
    for email in people_emails:
        domain = (email or "").strip().lower().rpartition("@")[2]
        if domain and not is_provider_domain(domain):
            domains.add(domain)
    return frozenset(domains)
