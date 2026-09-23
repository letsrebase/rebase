from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class FiscalProfile(Base, PrimaryKeyMixin, TimestampMixin):
    """The fiscal parameters of the one issuer. One row, ever.

    `emitter_profile` (slice 2) holds the issuer's *identity*; this holds the numbers
    and codes the SdI validates. They are not duplicates, and
    `emitter_profile.regime_fiscale` -- a `String(200)` of free text, already shipped
    and already read by `render/assets/header.typ.template` -- keeps its own meaning as
    a human-readable caption on the PDF. `codice_regime` here is the machine value,
    `String(4)`, and the only input to the XML's `RegimeFiscale`.

    Not historicised, and the alternative was considered rather than overlooked: a
    regime changes on 1 January, but spec 6.2 forbids back-dating an invoice past the
    start of the current year, so no emission ever needs a previous period's
    parameters. A `valido_da`/`valido_a` pair would only answer a question the
    per-invoice `snapshot` already answers, and answers better.

    Single-row is enforced by the database, exactly as `EmitterProfile` does it:
    `singleton` is `unique=True` and always `True`, so a second insert fails on the
    constraint. A "select then insert" pre-check alone would let two concurrent
    first-time saves both pass.
    """

    __tablename__ = "fiscal_profile"

    singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, unique=True)
    # RF01..RF19, as FPR12's RegimeFiscaleType enumerates them.
    codice_regime: Mapped[str] = mapped_column(String(4), nullable=False)
    aliquota_iva_default: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00")
    )
    natura_default: Mapped[str | None] = mapped_column(String(4), default=None)
    riferimento_normativo: Mapped[str | None] = mapped_column(Text, default=None)
    # Values of law, not preferences: configurable because the law has changed them.
    applica_bollo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    soglia_bollo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("77.47")
    )
    importo_bollo: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("2.00")
    )
    condizioni_pagamento: Mapped[str] = mapped_column(String(4), nullable=False, default="TP02")
    modalita_pagamento: Mapped[str] = mapped_column(String(4), nullable=False, default="MP05")
    giorni_scadenza: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
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
    # Nullable, unlike the bollo values of law next to them, because a regime that is
    # not the forfettario computes income by a different arithmetic entirely: for those
    # the honest value is "not applicable", which is NULL, and not a zero that a report
    # would quietly multiply by.
    coefficiente_redditivita: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), default=Decimal("67.00")
    )
    aliquota_imposta_sostitutiva: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), default=Decimal("5.00")
    )
    aliquota_inps: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=Decimal("26.07"))
    # REB-361: which jurisdiction pack governs the ceiling and rivalsa arithmetic
    # (`pigrocrm.core.fiscal.pack`) -- a pointer, not a duplication of the pack's own
    # data, and `NOT NULL` on purpose: every space this product has ever provisioned
    # is on this one regime today, so there is no real "which pack" question for an
    # existing row to leave unanswered, and a nullable pair would only invent one (an
    # undefined ceiling evaluation the day someone reads it before someone else sets
    # it). `server_default`, not only a Python-side one, the same reasoning
    # `Customer.pagamento_fine_mese` (REB-326) already uses: it is what keeps every
    # row this table already holds valid without depending on the ORM path that
    # inserted it.
    pack_id: Mapped[str] = mapped_column(
        String(40), nullable=False, default="it-flat-rate", server_default=text("'it-flat-rate'")
    )
    pack_version: Mapped[str] = mapped_column(
        String(10), nullable=False, default="1", server_default=text("'1'")
    )
