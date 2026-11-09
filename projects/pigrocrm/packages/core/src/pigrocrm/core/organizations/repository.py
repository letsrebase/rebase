from uuid import UUID

from sqlalchemy import column, func, or_, select, table, text
from sqlalchemy.orm import Session

from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.contact_points.service import owners_matching
from pigrocrm.core.db import decode_cursor, escape_like, keyset_predicate, order_by
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.schemas import ORGANIZATION_SORTS, OrganizationListQuery

# `pg_advisory_xact_lock(int, int)`: a namespace of this project's own, as
# `gmail/repository.py` does for the sync, and one key per serialized operation.
ORGANIZATIONS_LOCK_NAMESPACE = 0x7092
_IMPORT_LOCK_KEY = 1


# The columns of `deals` this repository reads, without importing the model (see
# `count_active_deals`).
_DEALS = table("deals", column("customer_id"), column("deleted_at"))


class OrganizationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_imports(self) -> None:
        """Holds, until this transaction ends, the one lock every import of Gmail
        proposals takes (`OrganizationService.create_from_suggestions`, REB-223). Two
        imports of the same proposal at once, a double click or two tabs, then run one
        after the other, and the second reads the organizations the first committed and
        refuses the domain instead of creating it twice."""
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :key)"),
            {"ns": ORGANIZATIONS_LOCK_NAMESPACE, "key": _IMPORT_LOCK_KEY},
        )

    def get(self, customer_id: UUID, *, include_deleted: bool = False) -> Organization | None:
        customer = self.session.get(Organization, customer_id)
        if customer is None:
            return None
        if customer.deleted_at is not None and not include_deleted:
            return None
        return customer

    def add(self, customer: Organization) -> Organization:
        self.session.add(customer)
        self.session.flush()
        return customer

    def match_by_fiscal_id(
        self,
        *,
        vat_number: str | None,
        tax_code: str | None,
        legal_entity_id: UUID | None = None,
    ) -> Organization | None:
        """Exact match against a normalised `vat_number`/`tax_code` -- the same
        two columns `list`'s own fuzzy `ilike` search already reads (`:42-45` below),
        read here for an identity check instead of a text search (REB-365's review
        step: matching a parsed invoice's `customer` against an existing `Organization`).

        A match on *either* column counts, mirroring `import_direction.
        classify_direction`'s own two-channel comparison: neither identifier alone is
        reliable, since either side is free to have populated only one of the two.
        `None` when neither identifier is given (nothing to match on) or neither
        matches an active customer. `legal_entity_id` narrows the match to the organizations of one
        legal entity, which is what an invoice written on that legal entity's register can attach to
        (REB-783); `None` looks across all of them.
        """
        if vat_number is None and tax_code is None:
            return None
        conditions = []
        if vat_number is not None:
            conditions.append(Organization.vat_number == vat_number)
        if tax_code is not None:
            conditions.append(Organization.tax_code == tax_code)
        stmt = select(Organization).where(Organization.deleted_at.is_(None), or_(*conditions))
        if legal_entity_id is not None:
            stmt = stmt.where(Organization.legal_entity_id == legal_entity_id)
        return self.session.execute(stmt).scalars().first()

    def find_by_name(self, legal_name: str) -> Organization | None:
        """The live customer whose `legal_name` is exactly this one, oldest first:
        an identity check, as `match_by_fiscal_id` is for the VAT number (the
        engagements door finds the customer «rebase» this way when the body carries no
        VAT number, or one no customer has, spec 2026-09-25 § 2.3 step 4). Not `list`'s
        search, which is a case-folding `ilike` answered a page at a time: a first page
        is not the set.
        """
        stmt = (
            select(Organization)
            .where(Organization.deleted_at.is_(None), Organization.legal_name == legal_name)
            .order_by(Organization.created_at, Organization.id)
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def list(self, query: OrganizationListQuery) -> list[Organization]:
        stmt = select(Organization).where(Organization.deleted_at.is_(None))

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
                    Organization.legal_name.ilike(like, escape="\\"),
                    Organization.vat_number.ilike(like, escape="\\"),
                    Organization.id.in_(owners_matching(ContactPoint.customer_id, query.search)),
                    Organization.tax_code.ilike(like, escape="\\"),
                )
            )
        if query.status:
            stmt = stmt.where(Organization.status == query.status)
        if query.legal_entity_id:
            stmt = stmt.where(Organization.legal_entity_id == query.legal_entity_id)
        if query.custom:
            # JSONB containment, served by the GIN index.
            stmt = stmt.where(Organization.custom_fields.contains(query.custom))

        # Residuo R9: keyset pagination over a *whitelisted* column, ordered
        # `col <dir>, id <dir>` (`NULLS LAST` only where the column admits a null, which
        # none of these does). Still keyset and not offset -- offset re-reads and skips
        # rows under concurrent insertion, which is why slice 1 chose keyset and does not
        # stop being true because the sort column changed.
        #
        # `resolve` raises `ValidationFailed` on an unknown key, so a caller-supplied
        # column name never reaches `ORDER BY` and never reaches `getattr` either.
        spec = ORGANIZATION_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )

    def count_active_deals(self, customer_id: UUID) -> int:
        """The customer's live deals, whoever's legal entity they sit in: what `soft_delete`
        decides on. `deals` is named as a bare table and not imported as the model:
        `deals` imports this package's neighbours, which import this one."""
        # Past the row-level policies, through the definer function of migration 0048
        # (REB-634): a deal of a legal entity this session cannot see still keeps its
        # customer from being archived, or the invariant `soft_delete` guards would
        # hold only for the deals the archiver happens to see.
        return int(
            self.session.execute(
                text("SELECT active_deals_of_organization(:customer_id)"),
                {"customer_id": customer_id},
            ).scalar_one()
        )

    def count_visible_active_deals(self, customer_id: UUID) -> int:
        """The active deals of the customer this session may see: what a refusal may
        say out loud, where `count_active_deals` is what it decides on (a count of
        rows out of the caller's sight is a fact about another legal entity)."""
        stmt = (
            select(func.count())
            .select_from(_DEALS)
            .where(_DEALS.c.customer_id == customer_id, _DEALS.c.deleted_at.is_(None))
        )
        return int(self.session.execute(stmt).scalar_one())
