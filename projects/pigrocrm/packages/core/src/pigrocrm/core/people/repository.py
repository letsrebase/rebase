from collections.abc import Collection
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import decode_cursor, escape_like, keyset_predicate, order_by
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.schemas import PERSON_SORTS, PersonListQuery


class PersonRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, person_id: UUID, *, include_deleted: bool = False) -> Person | None:
        person = self.session.get(Person, person_id)
        if person is None:
            return None
        if person.deleted_at is not None and not include_deleted:
            return None
        return person

    def customer_names(self, customer_ids: Collection[UUID]) -> dict[UUID, str]:
        """The `legal_name` of each given customer, in one query.

        One statement for a whole page, not one per row: `PersonService` reads this
        once with every distinct `customer_id` on the page it is about to return, so a
        50-row list costs two queries and never fifty-one. The same shape, and the same
        reason, as `SearchRepository._with_customer_names` -- a label lookup for rows
        already chosen, run after the limit.

        No `deleted_at` filter, deliberately: a soft-deleted customer is still the
        company the person belongs to, and archiving it did not dissolve the
        association. Blanking the name would make an archived customer read exactly
        like a person who never had one -- and it is `PersonService.restore`'s own
        conflict ("il cliente e' archiviato") that reports the customer is gone, not an
        empty cell in the Persone list.

        An empty input short-circuits: `IN ()` is a query with no possible rows, and the
        common case (see `Person`'s docstring) is a page where nobody has a customer.
        """
        if not customer_ids:
            return {}
        rows = self.session.execute(
            select(Customer.id, Customer.legal_name).where(Customer.id.in_(customer_ids))
        ).all()
        return {row[0]: row[1] for row in rows}

    def add(self, person: Person) -> Person:
        self.session.add(person)
        self.session.flush()
        return person

    def list(self, query: PersonListQuery) -> list[Person]:
        stmt = select(Person).where(Person.deleted_at.is_(None))

        if query.search:
            # escape_like neutralizes "%"/"_"/"\" in the *user's* term before it is
            # wrapped in the wildcard "%...%" this method builds -- otherwise a
            # literal "_" in the search box matches "any one character" and a
            # trailing "\" combines with the wildcard just after it into an
            # accidental escape sequence that swallows the match entirely. escape="\\"
            # states explicitly which character escape_like used, rather than relying
            # on ILIKE's default. Mirrors CustomerRepository.list exactly.
            like = f"%{escape_like(query.search.lower())}%"
            stmt = stmt.where(
                or_(
                    Person.first_name.ilike(like, escape="\\"),
                    Person.last_name.ilike(like, escape="\\"),
                    Person.email.ilike(like, escape="\\"),
                )
            )
        if query.customer_id:
            stmt = stmt.where(Person.customer_id == query.customer_id)
        if query.custom:
            # JSONB containment, served by the GIN index.
            stmt = stmt.where(Person.custom_fields.contains(query.custom))

        # Residuo R9 -- see `CustomerRepository.list` for the reasoning. `last_name` is
        # the one nullable column in any of the four whitelists, so this is the `list`
        # where `keyset_predicate`'s `OR col IS NULL` arm actually fires: without it a
        # scan ordered by `last_name` stops at the last surname and never returns the
        # people who have none.
        spec = PERSON_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )
