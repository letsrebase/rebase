from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

# Wide enough for a foreign VAT number kept in its own shape (REB-619, spec 2026-10-03
# §1.3): an EU one runs to fourteen characters with its prefix (`NL123456789B01`), a
# Swiss one to sixteen once its punctuation is stripped. An Italian P.IVA is still
# exactly eleven digits, which `LegalEntityService` checks before anything reaches the
# column; the width only stops a foreign code from dying as a `DataError` at flush.
VAT_NUMBER_WIDTH = 20


def _name_from_legal_name(context: Any) -> str:
    params = context.get_current_parameters()
    return str(params.get("legal_name") or "")[:80]


def default_legal_entity_id(context: Any) -> UUID | None:
    """The space's default legal entity, for a row inserted without naming one.

    A column default on `invoices.legal_entity_id`, `invoice_counters.legal_entity_id` and
    `invoice_register_gaps.legal_entity_id` (REB-619): the services name the legal entity on
    every row they write, so in production this runs for no row at all; it is what a
    row built by hand, which the test suite does in fifty places and a migration's
    planted fixture may, gets instead of a NOT NULL violation. Until the customer chain
    of milestone 3 decides it, the default legal entity is also the only answer there is.
    Context-sensitive on purpose (`context.connection` is the inserting connection, so
    a default seeded in the caller's own transaction is visible); `None` when the space
    has no default at all, which the NOT NULL then refuses loudly rather than this
    function inventing one.
    """
    found: UUID | None = context.connection.execute(
        select(LegalEntity.id).where(LegalEntity.is_default.is_(True))
    ).scalar_one_or_none()
    return found


class LegalEntity(Base, PrimaryKeyMixin, TimestampMixin):
    """Who is issuing the document: one row per legal entity of the space.

    Until REB-615 this was `EmitterProfile`, «one row, ever», held to a single row by
    a `singleton` unique column. The table keeps its name, `legal_entities`, on
    purpose (spec 2026-10-03 §10): the routes, this class and every word the product
    says use «azienda», while the storage, the error label and the timeline rows keep
    the name they already carry. The fiscal columns mirror `organizations` exactly rather
    than being free text, because FatturaPA is built on them (slice 3).

    What the row gained: `name`, the short name the selector shows («studio rossi»,
    distinct from `legal_name`); `is_default`, exactly one `True` per space,
    enforced by the partial unique index below and not by a check in the service; and
    `active`, because a legal entity that issued an invoice is never deleted, only switched
    off. The two partial unique indexes on the upper-cased fiscal ids are what makes
    «an XML whose supplier matches two aziende» impossible to save in the first place
    (spec §1.5, §2 step 1); `LegalEntityService` writes both ids already normalised, so
    two spellings of one code cannot sit on two rows.
    """

    __tablename__ = "legal_entities"

    # Defaults to the first 80 characters of `legal_name` at insert time, which is
    # what the migration backfilled for every row that already existed and what the
    # service derives when a write names none: a row built without a short name is
    # not a row without a name.
    name: Mapped[str] = mapped_column(String(80), nullable=False, default=_name_from_legal_name)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    vat_number: Mapped[str | None] = mapped_column(String(VAT_NUMBER_WIDTH), default=None)
    tax_code: Mapped[str | None] = mapped_column(String(16), default=None)
    address: Mapped[str | None] = mapped_column(String(255), default=None)
    postal_code: Mapped[str | None] = mapped_column(String(10), default=None)
    city: Mapped[str | None] = mapped_column(String(120), default=None)
    province: Mapped[str | None] = mapped_column(String(2), default=None)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="IT")
    certified_email: Mapped[str | None] = mapped_column(String(320), default=None)
    sdi_code: Mapped[str | None] = mapped_column(String(7), default=None)
    phone: Mapped[str | None] = mapped_column(String(40), default=None)
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    website: Mapped[str | None] = mapped_column(String(255), default=None)
    # Storage keys, not filesystem paths: the logo and signature live in the same
    # DocumentStorage as everything else, so a Drive-backed install keeps them too.
    logo_key: Mapped[str | None] = mapped_column(String(255), default=None)
    signature_key: Mapped[str | None] = mapped_column(String(255), default=None)
    # A text block -- name and role of the person signing. NOT the same thing as
    # `signature_key` above, which is the storage key of a signature *image* used on the
    # PDF: an email does not attach an image of a signature, it wants text. The phone
    # number, the website and the company name are deliberately NOT repeated here; the
    # reminder template reads them from their own columns, so the number cannot diverge
    # between two places. The previous system hardcoded all of it, twice, verbatim, in two builders.
    email_signature: Mapped[str | None] = mapped_column(Text, default=None)
    tax_regime: Mapped[str | None] = mapped_column(String(200), default=None)
    # The person who signs for the entity: the `rebase-rappresentante` of the engagement letters
    # and framework agreements (collaborators design § 1, «The signer's side»). Free text, as the
    # company writes it («Mario Rossi, amministratore unico»).
    legal_representative: Mapped[str | None] = mapped_column(String(160), default=None)
    # REB-974, taxes design § 1: the last day of the fiscal year, 31 December unless the
    # company's statutes say otherwise. A legal entity's own fact and not the profile's: it
    # is the entity's financial year, which the company dates follow (balance and first
    # advance on the last day of the sixth month after it, A5 verified). Not nullable: a
    # calendar year is the honest default for every row that exists. A February 29 is
    # accepted; the constraint checks that the day exists in the month, never in a year.
    fiscal_year_end_month: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=12, server_default=text("12")
    )
    fiscal_year_end_day: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=31, server_default=text("31")
    )

    # REB-1100: the day the company applied for registration in the companies register. The chamber
    # fee of the year it falls in is the fixed first-year amount, due within 30 days of this
    # date and not on 30 June (`fiscal/law_chamber_fee.py`, `fiscal/tax_items.py`); every other
    # year reads the turnover. Optional: nothing else in the product needs it, and a company
    # whose date is unknown is read as an older one.
    registration_date: Mapped[date | None] = mapped_column(Date, default=None)

    __table_args__ = (
        # The default is what every implicit read resolves to, so it is never a row that
        # was switched off: enforced here, not only in `set_default`/`deactivate`, because
        # the two can race each other across transactions.
        CheckConstraint("NOT (is_default AND NOT active)", name="ck_legal_entities_default_active"),
        CheckConstraint(
            "(fiscal_year_end_month IN (1, 3, 5, 7, 8, 10, 12)"
            " AND fiscal_year_end_day BETWEEN 1 AND 31)"
            " OR (fiscal_year_end_month IN (4, 6, 9, 11)"
            " AND fiscal_year_end_day BETWEEN 1 AND 30)"
            " OR (fiscal_year_end_month = 2 AND fiscal_year_end_day BETWEEN 1 AND 29)",
            name="ck_legal_entities_fiscal_year_end",
        ),
        Index(
            "uq_legal_entities_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index(
            "uq_legal_entities_vat_number",
            func.upper(vat_number),
            unique=True,
            postgresql_where=text("vat_number IS NOT NULL"),
        ),
        Index(
            "uq_legal_entities_tax_code",
            func.upper(tax_code),
            unique=True,
            postgresql_where=text("tax_code IS NOT NULL"),
        ),
    )


class LegalEntitySite(Base, PrimaryKeyMixin, TimestampMixin):
    """A place of business of a legal entity beside its registered office (REB-1092): an
    establishment, yard, office or fixed base in a region (art. 4 c. 2 D.Lgs. 446/1997).

    `region` is the key of the IRAP rate table (an ISTAT region code, or `BZ` or `TN` for the two
    autonomous provinces), the one fact the tax needs and the only one required; `city` and
    `address` say where, for the person reading the list, and decide nothing. The registered
    office is not a row here: it is the legal entity's own address, and a staff member works at it
    by `staff_members.at_registered_office`. The row names nothing computed.

    `(id, legal_entity_id)` is unique so that a staff member's assignment can point at the pair and
    never at a site of another legal entity.
    """

    __tablename__ = "legal_entity_sites"
    __table_args__ = (
        UniqueConstraint("id", "legal_entity_id", name="uq_legal_entity_sites_id_legal_entity_id"),
        Index("ix_legal_entity_sites_legal_entity_id", "legal_entity_id"),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    region: Mapped[str] = mapped_column(String(2), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120), default=None)
    address: Mapped[str | None] = mapped_column(String(255), default=None)
