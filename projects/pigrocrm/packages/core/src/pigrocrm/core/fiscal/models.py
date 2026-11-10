import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class FiscalProfile(Base, PrimaryKeyMixin, TimestampMixin):
    """The fiscal parameters of one legal entity. At most one row per legal entity.

    `legal_entities` (slice 2) holds the issuer's *identity*; this holds the numbers
    and codes the SdI validates. They are not duplicates, and
    `legal_entities.tax_regime` -- a `String(200)` of free text, already shipped
    and already read by `render/assets/header.typ.template` -- keeps its own meaning as
    a human-readable caption on the PDF. `regime_code` here is the machine value,
    `String(4)`, and the only input to the XML's `RegimeFiscale`.

    Not historicised, and the alternative was considered rather than overlooked: a
    regime changes on 1 January, but spec 6.2 forbids back-dating an invoice past the
    start of the current year, so no emission ever needs a previous period's
    parameters. A `valid_from`/`valid_to` pair would only answer a question the
    per-invoice `snapshot` already answers, and answers better. What an invoice never needed
    but a tax year does (REB-974) is the regime of a past year: the law is keyed by year in
    the pack (`fiscal/law.py`), and an income estimate freezes the pack and the parameters it
    was computed with (`FiscalEstimateReading`), so the profile itself stays one row.

    One per legal entity is enforced by the database (REB-615, spec 2026-10-03 §1.2):
    `legal_entity_id` is `unique=True`, so a second insert for the same legal entity fails on
    the constraint, which is what turns two concurrent first-time saves into one row
    and one `Conflict`. A space whose owner never saved this profile has no row, which
    `issue` answers with `NotFound`.
    """

    __tablename__ = "fiscal_profile"

    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, unique=True
    )
    # RF01..RF19, as FPR12's RegimeFiscaleType enumerates them; `NULL` on a profile
    # whose `pack_id` is `non-it` (REB-619), since the codes mean nothing abroad.
    regime_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    default_vat_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00")
    )
    default_vat_nature: Mapped[str | None] = mapped_column(String(4), default=None)
    legal_reference: Mapped[str | None] = mapped_column(Text, default=None)
    # Values of law, not preferences: configurable because the law has changed them.
    apply_stamp_duty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    stamp_duty_threshold: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("77.47")
    )
    stamp_duty_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("2.00")
    )
    payment_terms: Mapped[str] = mapped_column(String(4), nullable=False, default="TP02")
    payment_method: Mapped[str] = mapped_column(String(4), nullable=False, default="MP05")
    due_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    iban: Mapped[str | None] = mapped_column(String(34), default=None)
    # §4.5. NOT a second table: slice 1 §3 described a FiscalProfile holding "regime,
    # ATECO coefficient, substitute tax rate, INPS", and the table slice 3 delivered
    # holds the FatturaPA parameters and not the income ones. Same concept, same single
    # row -- a second fiscal profile would create two answers to "which regime am I in".
    #
    # Percentages, so Numeric(5, 2): `67.00`, not `0.67`. Stored the way its owner reads
    # and types them, converted once where the tax is computed. The defaults are the previous
    # system's own profile -- the migration of FORFETTARIO_PROFITABILITY_RATE (0.67),
    # FORFETTARIO_SUBSTITUTE_TAX_RATE (0.05) and FORFETTARIO_INPS_RATE (0.2607) out of
    # App.jsx and into the service layer, which slice 1 §14 assigns to this slice.
    #
    # Nullable, unlike the stamp duty values of law next to them, because a regime that is
    # not the flat-rate regime computes income by a different arithmetic entirely: for those
    # the honest value is "not applicable", which is NULL, and not a zero that a report
    # would quietly multiply by.
    #
    # No Python-side default on the three since REB-619: `FiscalProfileUpsert` owns the
    # flat-rate regime's values (67, 5, 26.07) and every write goes through it, while a
    # foreign profile sets them to `None` on purpose, which an ORM default would
    # silently turn back into the flat-rate regime's on the insert.
    profitability_coefficient: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    substitute_tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    social_security_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # REB-361: which jurisdiction pack governs the ceiling and social security surcharge arithmetic
    # (`pigrocrm.core.fiscal.pack`) -- a pointer, not a duplication of the pack's own
    # data, and `NOT NULL` on purpose: every space this product has ever provisioned
    # is on this one regime today, so there is no real "which pack" question for an
    # existing row to leave unanswered, and a nullable pair would only invent one (an
    # undefined ceiling evaluation the day someone reads it before someone else sets
    # it). `server_default`, not only a Python-side one, the same reasoning
    # `Organization.payment_end_of_month` (REB-326) already uses: it is what keeps every
    # row this table already holds valid without depending on the ORM path that
    # inserted it.
    pack_id: Mapped[str] = mapped_column(
        String(40), nullable=False, default="it-flat-rate", server_default=text("'it-flat-rate'")
    )
    pack_version: Mapped[str] = mapped_column(
        String(10), nullable=False, default="1", server_default=text("'1'")
    )
    # REB-974, taxes design § 1: the rest of the taxpayer's parameters, the ones that depend
    # on the person and not on the law. Both nullable: «not stated» is not a no, and a figure
    # that depends on one gives no figure rather than a guess. Neither reaches a number yet
    # (the INPS category and the ISA flag are read by the advances and the contributions of
    # the cards that follow); `fiscal_estimates` freezes them beside the ones that do.
    # The category's values are named in the design; the fixed quarterly minimum of
    # «artigiani e commercianti» and the rates are TO VERIFY (A5 row «Gestione separata and
    # artigiani e commercianti rates, minimums and quarterly dates»). `isa` decides 50/50
    # or 40/60 advances (A5, verified).
    inps_category: Mapped[str | None] = mapped_column(String(30), default=None)
    isa: Mapped[bool | None] = mapped_column(Boolean, default=None)
    # REB-1021, suppliers design § 6: how often the legal entity settles its IVA. Only a pack
    # with a periodic IVA reads it (`FiscalPack.law_for(year).iva`); on the others it stays
    # `monthly` and means nothing. Quarterly is an option the entity exercises in its annual
    # return, within the turnover limits of `IvaSettlementLaw`.
    iva_frequency: Mapped[str] = mapped_column(
        String(10), nullable=False, default="monthly", server_default=text("'monthly'")
    )

    __table_args__ = (
        CheckConstraint(
            "inps_category IN ('gestione_separata', 'artigiani_commercianti')",
            name="ck_fiscal_profile_inps_category",
        ),
        CheckConstraint(
            "iva_frequency IN ('monthly', 'quarterly')", name="ck_fiscal_profile_iva_frequency"
        ),
    )


class FiscalEstimateReading(Base, PrimaryKeyMixin, TimestampMixin):
    """A year's income estimate as it was computed, frozen with what it used (REB-974, taxes
    design § 1 and § 2). One row per legal entity and tax year.

    The pack's law is keyed by tax year and the profile is not historicised, so the estimate
    of a year is no longer reproducible once the entity changes pack on 1 January: the
    profile then holds the ordinary regime's parameters and the flat-rate ones are gone. This
    row is what stays. It stores the pack and version, the law year that governed the
    estimate, the taxpayer's parameters it read and the figures it produced, so a year
    computed under the flat-rate pack keeps its figures after the move.

    Written when the year's estimate is computed on the legal entity's own revenue and its
    pack still carries the estimate (`AnalyticsService.get_fiscal_estimate`), so it follows
    the figures while the regime lasts and stops following them when it does not; and by
    `FiscalProfileService.upsert`, which keeps the previous and the current year just before
    the legal entity changes pack, so a year nobody had opened is not lost by the move. A
    «what if» with a revenue the caller supplies is never stored. `parameters` carries the
    profile's own and the legal entity's fiscal year end.

    `parameters` and `figures` are JSON because they are copies of what `FiscalProfile` and
    `FiscalEstimate` held at that moment, read back whole and never queried by a field.
    """

    __tablename__ = "fiscal_estimates"

    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    pack_id: Mapped[str] = mapped_column(String(40), nullable=False)
    pack_version: Mapped[str] = mapped_column(String(10), nullable=False)
    law_from_year: Mapped[int] = mapped_column(Integer, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    figures: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "tax_year", name="uq_fiscal_estimates_year"),
    )


IVA_FREQUENCIES: tuple[str, ...] = ("monthly", "quarterly")
# What a period's result came to: paid to the tax office on its date, a debit small enough to
# wait for the next period's payment, a credit carried forward, or nothing at all.
IVA_OUTCOMES: tuple[str, ...] = ("due", "carried", "credit", "nil")


class IvaSettlement(Base, PrimaryKeyMixin, TimestampMixin):
    """One period's IVA settlement of one legal entity (REB-1021, suppliers design § 6).

    The figures are the register's totals and the carry-over, frozen when the period is
    settled and recomputed whenever it is settled again; nothing else writes the row
    (`IvaSettlementService`). `sales_vat` is the VAT due on the sales of the period (a sale
    under split payment owes none: the buyer pays it, art. 17-ter DPR 633/72);
    `reverse_charge_vat` is the VAT the buyer owes on its reverse-charge purchases;
    `purchase_vat` is the deductible share of the VAT on the purchases registered in the
    period. `credit_in` is the credit the previous period left and `debit_in` the small
    debit it left to be paid now, with its interest. `surcharge` is the 1% a quarterly
    settlement adds. `result` is signed: positive is owed, negative is a credit carried to the
    next period. The pack and its version are the ones the figures were computed under.
    """

    __tablename__ = "iva_settlements"
    __table_args__ = (
        UniqueConstraint(
            "legal_entity_id",
            "frequency",
            "year",
            "period_number",
            name="uq_iva_settlements_period",
        ),
        CheckConstraint(f"frequency IN {IVA_FREQUENCIES!r}", name="ck_iva_settlements_frequency"),
        CheckConstraint(f"outcome IN {IVA_OUTCOMES!r}", name="ck_iva_settlements_outcome"),
        CheckConstraint(
            "(frequency = 'monthly' AND period_number BETWEEN 1 AND 12) OR "
            "(frequency = 'quarterly' AND period_number BETWEEN 1 AND 4)",
            name="ck_iva_settlements_period_number",
        ),
        CheckConstraint("period_start <= period_end", name="ck_iva_settlements_period_order"),
        CheckConstraint(
            "credit_in >= 0 AND debit_in >= 0 AND surcharge >= 0",
            name="ck_iva_settlements_carry_not_negative",
        ),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # 1 to 12 for a month, 1 to 4 for a quarter.
    period_number: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    pack_id: Mapped[str] = mapped_column(String(40), nullable=False)
    pack_version: Mapped[str] = mapped_column(String(10), nullable=False)
    sales_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reverse_charge_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    purchase_vat: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    credit_in: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    debit_in: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    surcharge: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    result: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)
    # The day the pack's calendar says this period is paid, after a non-working day moves it.
    payment_due_on: Mapped[dt.date] = mapped_column(Date, nullable=False)


class IvaPeriodLock(Base, PrimaryKeyMixin):
    """A closed IVA period of one legal entity (REB-1036, suppliers design § 6): while the row
    exists, no document that changes the period's two registers is written, over REST and MCP.

    A switch, not a record: reopening deletes the row and the history is the timeline, which hangs
    on `id`. The id is derived from the legal entity, the frequency, the year and the number
    (`iva_lock.lock_id`), so every close and reopen of one period reads as one sequence. The
    unique constraint is the database's own guard against a second close of the same period.
    `period_start` and `period_end` are the days the lock covers, stored so a write dated on a
    day finds its lock without knowing the frequency: a year keeps the frequency it was first
    settled in, and a later one starts the year after. Monthly and quarterly rows of one legal
    entity never overlap (`IvaPeriodLockService.close_iva_period` refuses it).

    Separate from `period_locks` (`timetracking/locks.py`), which is global, by month, and
    governs hours and the cost side of purchases; neither implies the other.
    """

    __tablename__ = "iva_period_locks"
    __table_args__ = (
        UniqueConstraint(
            "legal_entity_id",
            "frequency",
            "year",
            "period_number",
            name="uq_iva_period_locks_period",
        ),
        CheckConstraint(f"frequency IN {IVA_FREQUENCIES!r}", name="ck_iva_period_locks_frequency"),
        CheckConstraint(
            "(frequency = 'monthly' AND period_number BETWEEN 1 AND 12) OR "
            "(frequency = 'quarterly' AND period_number BETWEEN 1 AND 4)",
            name="ck_iva_period_locks_period_number",
        ),
        CheckConstraint("period_start <= period_end", name="ck_iva_period_locks_period_order"),
        Index("ix_iva_period_locks_days", "legal_entity_id", "period_start", "period_end"),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_number: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[dt.date] = mapped_column(Date, nullable=False)
    closed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)


class DeclaredTaxFigure(Base, PrimaryKeyMixin, TimestampMixin):
    """The figures of a tax return as the accountant computed them, typed by an administrator
    (REB-1006, taxes design § 2). One row per legal entity, tax and tax year.

    Never computed and never replaced by an estimate: `fiscal_estimates` and this table are
    written by different paths, and a year with a row here reads as declared whatever the
    estimate says afterwards. `tax_due` and `advances` are never negative; `balance` is signed
    (a credit is negative). Whichever of the three the accountant did not give stays `NULL`,
    and at least one is present. `source` is `typed` until a figure read from a document and
    accepted by a person exists.
    """

    __tablename__ = "declared_tax_figures"

    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    tax: Mapped[str] = mapped_column(String(30), nullable=False)
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_due: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), default=None)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), default=None)
    advances: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), default=None)
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="typed", server_default=text("'typed'")
    )

    __table_args__ = (
        UniqueConstraint("legal_entity_id", "tax", "tax_year", name="uq_declared_tax_figures"),
        CheckConstraint(
            "tax_due IS NOT NULL OR balance IS NOT NULL OR advances IS NOT NULL",
            name="ck_declared_tax_figures_one_figure",
        ),
        CheckConstraint(
            "(tax_due IS NULL OR tax_due >= 0) AND (advances IS NULL OR advances >= 0)",
            name="ck_declared_tax_figures_not_negative",
        ),
    )
