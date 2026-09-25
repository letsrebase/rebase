from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.schemas import CUSTOMER_SORTS, CustomerListQuery
from pigrocrm.core.db import Base, decode_cursor, escape_like, keyset_predicate, order_by

# `pg_advisory_xact_lock(int, int)`: a namespace of this project's own, as
# `gmail/repository.py` does for the sync, and one key per serialized operation.
CUSTOMERS_LOCK_NAMESPACE = 0x7092
_IMPORT_LOCK_KEY = 1


class CustomerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_imports(self) -> None:
        """Holds, until this transaction ends, the one lock every import of Gmail
        proposals takes (`CustomerService.create_from_suggestions`, REB-223). Two
        imports of the same proposal at once, a double click or two tabs, then run one
        after the other, and the second reads the customers the first committed and
        refuses the domain instead of creating it twice."""
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": CUSTOMERS_LOCK_NAMESPACE, "key": _IMPORT_LOCK_KEY},
        )

    def get(self, customer_id: UUID, *, include_deleted: bool = False) -> Customer | None:
        customer = self.session.get(Customer, customer_id)
        if customer is None:
            return None
        if customer.deleted_at is not None and not include_deleted:
            return None
        return customer

    def add(self, customer: Customer) -> Customer:
        self.session.add(customer)
        self.session.flush()
        return customer

    def match_by_fiscal_id(
        self, *, partita_iva: str | None, codice_fiscale: str | None
    ) -> Customer | None:
        """Exact match against a normalised `partita_iva`/`codice_fiscale` -- the same
        two columns `list`'s own fuzzy `ilike` search already reads (`:42-45` below),
        read here for an identity check instead of a text search (REB-365's review
        step: matching a parsed invoice's `cliente` against an existing `Customer`).

        A match on *either* column counts, mirroring `import_direction.
        classify_direction`'s own two-channel comparison: neither identifier alone is
        reliable, since either side is free to have populated only one of the two.
        `None` when neither identifier is given (nothing to match on) or neither
        matches an active customer.
        """
        if partita_iva is None and codice_fiscale is None:
            return None
        conditions = []
        if partita_iva is not None:
            conditions.append(Customer.partita_iva == partita_iva)
        if codice_fiscale is not None:
            conditions.append(Customer.codice_fiscale == codice_fiscale)
        stmt = select(Customer).where(Customer.deleted_at.is_(None), or_(*conditions))
        return self.session.execute(stmt).scalars().first()

    def find_by_name(self, ragione_sociale: str) -> Customer | None:
        """The live customer whose `ragione_sociale` is exactly this one, oldest first:
        an identity check, as `match_by_fiscal_id` is for the VAT number (the
        engagements door finds the customer «rebase» this way when the body carries no
        VAT number, spec 2026-09-25 § 2.3 step 4). Not `list`'s search, which is a
        case-folding `ilike` answered a page at a time: a first page is not the set.
        """
        stmt = (
            select(Customer)
            .where(Customer.deleted_at.is_(None), Customer.ragione_sociale == ragione_sociale)
            .order_by(Customer.created_at, Customer.id)
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def list(self, query: CustomerListQuery) -> list[Customer]:
        stmt = select(Customer).where(Customer.deleted_at.is_(None))

        if query.search:
            # escape_like neutralizes "%"/"_"/"\" in the *user's* term before it is
            # wrapped in the wildcard "%...%" this method builds -- otherwise a
            # literal "_" in the search box matches "any one character" and a
            # trailing "\" combines with the wildcard just after it into an
            # accidental escape sequence that swallows the match entirely. escape="\\"
            # states explicitly which character escape_like used, rather than relying
            # on ILIKE's default.
            like = f"%{escape_like(query.search.lower())}%"
            stmt = stmt.where(
                or_(
                    Customer.ragione_sociale.ilike(like, escape="\\"),
                    Customer.partita_iva.ilike(like, escape="\\"),
                    Customer.email.ilike(like, escape="\\"),
                    Customer.codice_fiscale.ilike(like, escape="\\"),
                )
            )
        if query.stato:
            stmt = stmt.where(Customer.stato == query.stato)
        if query.custom:
            # JSONB containment, served by the GIN index.
            stmt = stmt.where(Customer.custom_fields.contains(query.custom))

        # Residuo R9: keyset pagination over a *whitelisted* column, ordered
        # `col <dir>, id <dir>` (`NULLS LAST` only where the column admits a null, which
        # none of these does). Still keyset and not offset -- offset re-reads and skips
        # rows under concurrent insertion, which is why slice 1 chose keyset and does not
        # stop being true because the sort column changed.
        #
        # `resolve` raises `ValidationFailed` on an unknown key, so a caller-supplied
        # column name never reaches `ORDER BY` and never reaches `getattr` either.
        spec = CUSTOMER_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )

    def count_active_deals(self, customer_id: UUID) -> int:
        """Queries the deals table through `Base.metadata` rather than importing the
        model. Deals are written in Task 12: importing `pigrocrm.core.deals.models`
        here -- at module level or inside the function -- would raise
        `ModuleNotFoundError` in this task's own tests. Going through `Base.metadata`
        keeps the dependency one-directional (deals references customers, never the
        other way round) and lets this method start returning real counts the moment
        the deals model is registered, with no edit here.

        Mirrors `PipelineRepository.count_deals_in_stage`'s shape exactly: returning 0
        is only correct for "the `deals` table does not exist yet". If `deals` exists
        but does not expose `customer_id` -- the FK column, verified against the plan's
        Task 12 `Deal` model -- that is a bug in this code, not "no deals for this
        customer": returning 0 there would let `soft_delete` remove a customer that
        still has deals, so this raises instead of guessing.

        `deleted_at` is treated more leniently than `customer_id`: its presence narrows
        "active" to exclude an already-archived deal, but -- unlike `customer_id` --
        it was not the column this correction verified against the plan, so a `deals`
        table lacking it simply counts every matching deal rather than raising.
        """
        deals_table = Base.metadata.tables.get("deals")
        if deals_table is None:
            return 0  # i deal arrivano in un task successivo
        customer_id_column = deals_table.c.get("customer_id")
        if customer_id_column is None:
            # La tabella esiste ma non ha la colonna attesa: e' un errore di codice,
            # non l'assenza di deal. Restituire 0 qui permetterebbe di cancellare un
            # cliente che ha ancora deal collegati.
            raise RuntimeError(
                "la tabella deals non espone customer_id: aggiornare count_active_deals"
            )
        stmt = (
            select(func.count()).select_from(deals_table).where(customer_id_column == customer_id)
        )
        deleted_at_column = deals_table.c.get("deleted_at")
        if deleted_at_column is not None:
            stmt = stmt.where(deleted_at_column.is_(None))
        return int(self.session.execute(stmt).scalar_one())
