from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pigrocrm.core.contact_points.models import ContactPoint, primary_value
from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin
from pigrocrm.core.legal_entities.models import default_legal_entity_id
from pigrocrm.core.organizations.roles import (
    CUSTOMER_KINDS,
    ORGANIZATION_ROLES,
    SUPPLIER_AREAS,
    SUPPLIER_KINDS,
    SUPPLIER_SUPPLIES,
    VAT_NUMBER_MAX_LENGTH,
    VAT_UNIQUE_INDEX,
    country_key,
    vat_key,
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Organization(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """The first full business entity. `People` and `Deals` copy this file's shape.

    Fiscal fields (`vat_number`, `tax_code`, `sdi_code`, `certified_email`) are first-class
    columns rather than custom fields, so slice 3 can build FatturaPA invoicing directly
    on them. `custom_fields` carries everything else a tenant defines through
    `FieldDefinitionService`; the GIN index below is what keeps `list()`'s JSONB
    containment filter (`custom_fields @> {...}`) from ever needing a full table scan.
    """

    __tablename__ = "organizations"
    __table_args__ = (
        Index("ix_organizations_custom_fields", "custom_fields", postgresql_using="gin"),
        Index("ix_organizations_legal_name", "legal_name"),
        # Trigram indexes, one per column `OrganizationRepository.list` ORs together. All
        # four are needed, not just the name: a four-way OR is planned as a BitmapOr
        # over four bitmap index scans, and a single unindexed branch collapses the
        # whole thing back into one sequential scan.
        #
        # Partial on `deleted_at IS NULL` because that is the condition every search
        # carries (spec §8.3): the index is smaller and residuo R7 closes for this
        # table. No `lower()` in the expression -- `similarity()` normalises to lower
        # case internally, and wrapping the column would make ILIKE on the raw column
        # unable to use the index (spec §8.2).
        Index(
            "ix_organizations_legal_name_trgm",
            "legal_name",
            postgresql_using="gin",
            postgresql_ops={"legal_name": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_organizations_vat_number_trgm",
            "vat_number",
            postgresql_using="gin",
            postgresql_ops={"vat_number": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_organizations_tax_code_trgm",
            "tax_code",
            postgresql_using="gin",
            postgresql_ops={"tax_code": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Residuo R9. One `(column, id)` B-tree per key admitted by `ORGANIZATION_SORTS`:
        # the ordering contract is `ORDER BY <col> <dir>, id <dir>`, and the tie-break
        # following the direction is what lets a single ascending index serve `desc` as a
        # backward scan. None of these is nullable, so none costs a second index -- and,
        # for the same reason, `order_by` must not spell `NULLS LAST` on any of them: that
        # spelling matches no index here, which is what made `dir=desc` a sequential scan
        # until `test_sort_plan.py` was written (REB-90; the file went with the planner
        # corpus in REB-580, the rule stays). See `people/models.py` for the one column
        # that does carry a second index.
        #
        # Not partial on `deleted_at IS NULL`, unlike the trigram indexes above: those
        # serve a predicate that always carries that clause, while an ordering has to
        # remain usable for any listing, including one that asks for the deleted rows.
        #
        # `ix_organizations_legal_name` (slice 1, single column) stays: it is still the
        # cheaper index for an equality lookup, and dropping an index is a separate
        # decision from adding one.
        Index("ix_organizations_created_at_id", "created_at", "id"),
        Index("ix_organizations_updated_at_id", "updated_at", "id"),
        Index("ix_organizations_legal_name_id", "legal_name", "id"),
        # A role filter (REB-970, design 2026-11-09 suppliers § 1) is a containment test on
        # the array, which this GIN index serves.
        Index("ix_organizations_roles", "roles", postgresql_using="gin"),
        # Two live rows of one country never share a VAT number, compared in its normalised
        # form (`roles.normalise_vat`): one record per company, whatever it is to us. The
        # service reads the same key to refuse a second one with a sentence, and this index is
        # what holds when two requests race. A space whose existing rows already collide has
        # no index until a person resolves them (`duplicates.py`).
        Index(
            VAT_UNIQUE_INDEX,
            country_key(text("country")),
            vat_key(text("vat_number")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND vat_number IS NOT NULL"),
        ),
        CheckConstraint(
            "cardinality(roles) >= 1 AND roles <@ ARRAY["
            + ", ".join(repr(role) for role in ORGANIZATION_ROLES)
            + "]::varchar[]",
            name="ck_organizations_roles",
        ),
        CheckConstraint(
            f"supplier_kind IS NULL OR {_in('supplier_kind', SUPPLIER_KINDS)}",
            name="ck_organizations_supplier_kind",
        ),
        CheckConstraint(
            f"supplier_area IS NULL OR {_in('supplier_area', SUPPLIER_AREAS)}",
            name="ck_organizations_supplier_area",
        ),
        CheckConstraint(
            f"supplier_supplies IS NULL OR {_in('supplier_supplies', SUPPLIER_SUPPLIES)}",
            name="ck_organizations_supplier_supplies",
        ),
        CheckConstraint(
            f"customer_kind IS NULL OR {_in('customer_kind', CUSTOMER_KINDS)}",
            name="ck_organizations_customer_kind",
        ),
    )

    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The legal entity this customer is billed by (REB-623, spec 2026-10-03 §1.6): proposed
    # from `country` at creation, editable afterwards, and copied onto every deal,
    # contract, document and invoice born under the customer from then on; changing it
    # moves nothing already created (§1.7). The column default is the space's default
    # legal entity, for a row built without one; the service always names it.
    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"),
        nullable=False,
        index=True,
        default=default_legal_entity_id,
    )
    # Who answers for this customer (REB-773): the person the reminders about its invoices
    # and contracts go to, with the deal's own owner for what hangs on a deal. Whoever
    # creates the customer unless the request names someone else; `None` only for a
    # customer nobody owns (a system actor created it, or the owner was cleared), which
    # the reminders then give to the space's owner. Not `NOT NULL`: a user is archived,
    # never deleted, and a customer must not stop existing with them.
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    # What this organization is to us: any of `ORGANIZATION_ROLES`, at least one. Every row
    # that existed before roles is a customer, and so is a row built without saying.
    roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)),
        nullable=False,
        default=lambda: ["customer"],
        server_default=text("ARRAY['customer']::varchar[]"),
    )
    # Wide enough for a foreign number (`NL123456789B01`); the eleven digits of an Italian
    # one are checked by the service, not by the column.
    vat_number: Mapped[str | None] = mapped_column(
        String(VAT_NUMBER_MAX_LENGTH), default=None, index=True
    )
    tax_code: Mapped[str | None] = mapped_column(String(16), default=None)
    sdi_code: Mapped[str | None] = mapped_column(String(7), default=None)
    certified_email: Mapped[str | None] = mapped_column(String(320), default=None)
    address: Mapped[str | None] = mapped_column(String(255), default=None)
    postal_code: Mapped[str | None] = mapped_column(String(10), default=None)
    city: Mapped[str | None] = mapped_column(String(120), default=None)
    province: Mapped[str | None] = mapped_column(String(2), default=None)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="IT")
    website: Mapped[str | None] = mapped_column(String(255), default=None)
    status: Mapped[str | None] = mapped_column(String(40), default=None)
    # The payment terms agreed with this customer (REB-326): days after the invoice
    # date, and whether the due date then slides to the end of its month («30 giorni
    # data fattura fine mese»). `payment_days` null means the fiscal profile's
    # `due_days` applies; the switch is never null, since "unknown" and "no" would
    # print the same date. Read by `InvoiceService.issue` and by nothing else: an issued
    # invoice carries the date it was born with, and a term changed later does not move
    # it.
    payment_days: Mapped[int | None] = mapped_column(Integer, default=None)
    payment_end_of_month: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # What it grants us as a supplier (design § 1): the same two terms, in the other direction.
    # `payment_days` and `payment_end_of_month` above stay what it is given as a customer.
    supplier_payment_days: Mapped[int | None] = mapped_column(Integer, default=None)
    supplier_payment_end_of_month: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # What a payment to it involves: one of `SUPPLIER_KINDS`, read from the invoice XML when it
    # says so and set by a person otherwise. A `foreign` supplier also says where it is
    # (`supplier_area`) and what it supplies (`supplier_supplies`), which decide whether
    # reverse charge applies. Null: not said.
    supplier_kind: Mapped[str | None] = mapped_column(String(20), default=None)
    supplier_area: Mapped[str | None] = mapped_column(String(20), default=None)
    supplier_supplies: Mapped[str | None] = mapped_column(String(20), default=None)
    # What a sale to it involves: one of `CUSTOMER_KINDS` (null: not said), and whether it is a
    # withholding agent, which decides whether our professional invoices carry a withholding.
    # For a public body `sdi_code` is its 6-character IPA code.
    customer_kind: Mapped[str | None] = mapped_column(String(20), default=None)
    is_withholding_agent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # The ways to reach this customer (REB-939): see `Person.contact_points`.
    contact_points: Mapped[list[ContactPoint]] = relationship(
        lazy="selectin",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="ContactPoint.position",
    )

    @property
    def email(self) -> str | None:
        """The primary email, which is where the CRM's mail to this customer goes."""
        return primary_value(self.contact_points, "email")

    @property
    def phone(self) -> str | None:
        return primary_value(self.contact_points, "phone")
