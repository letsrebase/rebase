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
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.db import escape_like
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.gmail.query import customer_domain, is_provider_domain
from pigrocrm.core.organizations.models import Organization
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
        """Every email point of a live Person or Organization, lowercased, deduplicated and
        sorted: the primary and every other address of each, so mail from a personal address
        lands on the person too. Sorted because the batching in `gmail/query.py` must be
        stable: an unstable order means two consecutive syncs issue different `q` strings for
        the same data, and the request-inspecting test in B1-7 could then pass by luck."""
        people = (
            select(ContactPoint.normalized)
            .join(Person, Person.id == ContactPoint.person_id)
            .where(ContactPoint.kind == "email", Person.deleted_at.is_(None))
        )
        organizations = (
            select(ContactPoint.normalized)
            .join(Organization, Organization.id == ContactPoint.customer_id)
            .where(ContactPoint.kind == "email", Organization.deleted_at.is_(None))
        )
        rows = self.session.execute(people.union(organizations)).scalars().all()
        # `if address` drops the empty string: a point saved with "" is not an address, and an
        # empty clause in the Gmail `q` would match on nothing while still costing length budget.
        return tuple(sorted(address for address in rows if address))

    def known_among(self, addresses: Iterable[str]) -> frozenset[str]:
        """Which of these candidate addresses are an email point of a live Person or
        Organization, lowercased.

        What a caller that already holds its candidates asks, instead of loading the whole
        roster (`known_addresses`) to test a handful of them: the lookup is
        `normalized IN (...)` on `(kind, normalized)`, the pair `ix_contact_points_kind_normalized`
        is built on."""
        needles = _needles(addresses)
        if not needles:
            return frozenset()
        people = (
            select(ContactPoint.normalized)
            .join(Person, Person.id == ContactPoint.person_id)
            .where(
                ContactPoint.kind == "email",
                ContactPoint.normalized.in_(needles),
                Person.deleted_at.is_(None),
            )
        )
        organizations = (
            select(ContactPoint.normalized)
            .join(Organization, Organization.id == ContactPoint.customer_id)
            .where(
                ContactPoint.kind == "email",
                ContactPoint.normalized.in_(needles),
                Organization.deleted_at.is_(None),
            )
        )
        return frozenset(self.session.execute(people.union(organizations)).scalars().all())

    def domains_taken(self, domains: Iterable[str]) -> frozenset[str]:
        """Which of these candidate domains the CRM already files under a customer: each
        live customer's own (its website's and any non-webmail email point's) and the address
        domain of each email point of each live person attached to a live customer. What a
        customer proposal (REB-223) must not offer again, and what an import of one refuses to
        duplicate. Webmail domains never appear: they name nobody in particular.

        The database narrows to the rows that can name one of the candidates (a website
        or an email containing it) and Python applies the rule, so the whole of the
        customer and people tables is never read into memory."""
        candidates = {domain.strip().lower() for domain in domains if domain and domain.strip()}
        if not candidates:
            return frozenset()
        websites = self.session.execute(
            select(Organization.website).where(
                Organization.deleted_at.is_(None),
                or_(
                    *(
                        Organization.website.ilike(f"%{escape_like(domain)}%", escape="\\")
                        for domain in candidates
                    )
                ),
            )
        ).scalars()
        customer_emails = self.session.execute(
            select(ContactPoint.normalized)
            .join(Organization, Organization.id == ContactPoint.customer_id)
            .where(
                ContactPoint.kind == "email",
                Organization.deleted_at.is_(None),
                or_(
                    *(
                        ContactPoint.normalized.like(f"%{escape_like(domain)}%", escape="\\")
                        for domain in candidates
                    )
                ),
            )
        ).scalars()
        people_emails = self.session.execute(
            select(ContactPoint.normalized)
            .join(Person, Person.id == ContactPoint.person_id)
            .join(Organization, Organization.id == Person.customer_id)
            .where(
                ContactPoint.kind == "email",
                Person.deleted_at.is_(None),
                Organization.deleted_at.is_(None),
                or_(
                    *(
                        ContactPoint.normalized.like(f"%@{escape_like(domain)}", escape="\\")
                        for domain in candidates
                    )
                ),
            )
        ).scalars()
        return _domains_of(websites, customer_emails, people_emails) & candidates

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

        person_rows = self.session.execute(
            select(ContactPoint.normalized, Person.id, Person.customer_id)
            .join(Person, Person.id == ContactPoint.person_id)
            .where(
                ContactPoint.kind == "email",
                ContactPoint.normalized.in_(needles),
                Person.deleted_at.is_(None),
            )
        ).all()
        direct_rows = self.session.execute(
            select(ContactPoint.normalized, Organization.id)
            .join(Organization, Organization.id == ContactPoint.customer_id)
            .where(
                ContactPoint.kind == "email",
                ContactPoint.normalized.in_(needles),
                Organization.deleted_at.is_(None),
            )
        ).all()
        # A live person may point at a soft-deleted customer, so the organizations reached
        # through `people.customer_id` are re-checked here rather than trusted: filing
        # a message against an archived row would produce a link nothing can display.
        via_people = {customer_id for _, _, customer_id in person_rows if customer_id is not None}
        live_via_people = (
            set(
                self.session.execute(
                    select(Organization.id).where(
                        Organization.id.in_(via_people), Organization.deleted_at.is_(None)
                    )
                )
                .scalars()
                .all()
            )
            if via_people
            else set()
        )

        persons: dict[str, list[UUID]] = {}
        organizations: dict[str, set[UUID]] = {}
        for needle, person_id, customer_id in person_rows:
            persons.setdefault(needle, []).append(person_id)
            if customer_id in live_via_people:
                organizations.setdefault(needle, set()).add(customer_id)
        for needle, customer_id in direct_rows:
            organizations.setdefault(needle, set()).add(customer_id)

        all_organizations = {customer_id for ids in organizations.values() for customer_id in ids}
        deals: dict[UUID, list[UUID]] = {}
        if all_organizations:
            for customer_id, deal_id in self.session.execute(
                select(Deal.customer_id, Deal.id).where(
                    Deal.customer_id.in_(all_organizations), Deal.deleted_at.is_(None)
                )
            ).all():
                deals.setdefault(customer_id, []).append(deal_id)

        resolved: dict[str, tuple[EntityRef, ...]] = {}
        for needle in persons.keys() | organizations.keys():
            refs = [EntityRef("person", person_id) for person_id in persons.get(needle, [])]
            customer_ids = sorted(organizations.get(needle, ()))
            refs.extend(EntityRef("organization", customer_id) for customer_id in customer_ids)
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


def _domains_of(
    websites: Iterable[str | None],
    customer_emails: Iterable[str],
    people_emails: Iterable[str],
) -> frozenset[str]:
    """The domains a set of organizations' websites, organizations' addresses and people's addresses
    stand for."""
    domains: set[str] = set()
    # Both a website and an address: a customer whose website is acme.com and whose email is
    # at acme.it is at both, and proposing the other one would make a second Acme.
    for website in websites:
        if (domain := customer_domain(website=website, email=None)) is not None:
            domains.add(domain)
    for email in customer_emails:
        if (domain := customer_domain(website=None, email=email)) is not None:
            domains.add(domain)
    for email in people_emails:
        domain = email.strip().lower().rpartition("@")[2]
        if domain and not is_provider_domain(domain):
            domains.add(domain)
    return frozenset(domains)
