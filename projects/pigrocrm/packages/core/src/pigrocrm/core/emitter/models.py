from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Index, String, Text, func, select, text
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

# Wide enough for a foreign VAT number kept in its own shape (REB-619, spec 2026-10-03
# §1.3): an EU one runs to fourteen characters with its prefix (`NL123456789B01`), a
# Swiss one to sixteen once its punctuation is stripped. An Italian P.IVA is still
# exactly eleven digits, which `LegalEntityService` checks before anything reaches the
# column; the width only stops a foreign code from dying as a `DataError` at flush.
PARTITA_IVA_WIDTH = 20


def _nome_from_ragione_sociale(context: Any) -> str:
    params = context.get_current_parameters()
    return str(params.get("ragione_sociale") or "")[:80]


def default_azienda_id(context: Any) -> UUID | None:
    """The space's default azienda, for a row inserted without naming one.

    A column default on `invoices.azienda_id`, `invoice_counters.azienda_id` and
    `invoice_register_gaps.azienda_id` (REB-619): the services name the azienda on
    every row they write, so in production this runs for no row at all; it is what a
    row built by hand, which the test suite does in fifty places and a migration's
    planted fixture may, gets instead of a NOT NULL violation. Until the customer chain
    of milestone 3 decides it, the default azienda is also the only answer there is.
    Context-sensitive on purpose (`context.connection` is the inserting connection, so
    a default seeded in the caller's own transaction is visible); `None` when the space
    has no default at all, which the NOT NULL then refuses loudly rather than this
    function inventing one.
    """
    found: UUID | None = context.connection.execute(
        select(LegalEntity.id).where(LegalEntity.predefinita.is_(True))
    ).scalar_one_or_none()
    return found


class LegalEntity(Base, PrimaryKeyMixin, TimestampMixin):
    """Who is issuing the document: one row per azienda of the space.

    Until REB-615 this was `EmitterProfile`, «one row, ever», held to a single row by
    a `singleton` unique column. The table keeps its name, `emitter_profile`, on
    purpose (spec 2026-10-03 §10): the routes, this class and every word the product
    says use «azienda», while the storage, the error label and the timeline rows keep
    the name they already carry. The fiscal columns mirror `customers` exactly rather
    than being free text, because FatturaPA is built on them (slice 3).

    What the row gained: `nome`, the short name the selector shows («humancraft»,
    distinct from `ragione_sociale`); `predefinita`, exactly one `True` per space,
    enforced by the partial unique index below and not by a check in the service; and
    `attiva`, because an azienda that issued an invoice is never deleted, only switched
    off. The two partial unique indexes on the upper-cased fiscal ids are what makes
    «an XML whose supplier matches two aziende» impossible to save in the first place
    (spec §1.5, §2 step 1); `LegalEntityService` writes both ids already normalised, so
    two spellings of one code cannot sit on two rows.
    """

    __tablename__ = "emitter_profile"

    # Defaults to the first 80 characters of `ragione_sociale` at insert time, which is
    # what the migration backfilled for every row that already existed and what the
    # service derives when a write names none: a row built without a short name is
    # not a row without a name.
    nome: Mapped[str] = mapped_column(
        String(80), nullable=False, default=_nome_from_ragione_sociale
    )
    predefinita: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    attiva: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    ragione_sociale: Mapped[str] = mapped_column(String(255), nullable=False)
    partita_iva: Mapped[str | None] = mapped_column(String(PARTITA_IVA_WIDTH), default=None)
    codice_fiscale: Mapped[str | None] = mapped_column(String(16), default=None)
    indirizzo: Mapped[str | None] = mapped_column(String(255), default=None)
    cap: Mapped[str | None] = mapped_column(String(10), default=None)
    comune: Mapped[str | None] = mapped_column(String(120), default=None)
    provincia: Mapped[str | None] = mapped_column(String(2), default=None)
    nazione: Mapped[str] = mapped_column(String(2), nullable=False, default="IT")
    pec: Mapped[str | None] = mapped_column(String(320), default=None)
    codice_sdi: Mapped[str | None] = mapped_column(String(7), default=None)
    telefono: Mapped[str | None] = mapped_column(String(40), default=None)
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    sito_web: Mapped[str | None] = mapped_column(String(255), default=None)
    # Storage keys, not filesystem paths: the logo and signature live in the same
    # DocumentStorage as everything else, so a Drive-backed install keeps them too.
    logo_key: Mapped[str | None] = mapped_column(String(255), default=None)
    firma_key: Mapped[str | None] = mapped_column(String(255), default=None)
    # A text block -- name and role of the person signing. NOT the same thing as
    # `firma_key` above, which is the storage key of a signature *image* used on the
    # PDF: an email does not attach an image of a signature, it wants text. The phone
    # number, the website and the company name are deliberately NOT repeated here; the
    # reminder template reads them from their own columns, so the number cannot diverge
    # between two places. The previous system hardcoded all of it, twice, verbatim, in two builders.
    firma_email: Mapped[str | None] = mapped_column(Text, default=None)
    regime_fiscale: Mapped[str | None] = mapped_column(String(200), default=None)

    __table_args__ = (
        # The default is what every implicit read resolves to, so it is never a row that
        # was switched off: enforced here, not only in `set_default`/`deactivate`, because
        # the two can race each other across transactions.
        CheckConstraint(
            "NOT (predefinita AND NOT attiva)", name="ck_emitter_profile_default_active"
        ),
        Index(
            "uq_emitter_profile_predefinita",
            "predefinita",
            unique=True,
            postgresql_where=text("predefinita"),
        ),
        Index(
            "uq_emitter_profile_partita_iva",
            func.upper(partita_iva),
            unique=True,
            postgresql_where=text("partita_iva IS NOT NULL"),
        ),
        Index(
            "uq_emitter_profile_codice_fiscale",
            func.upper(codice_fiscale),
            unique=True,
            postgresql_where=text("codice_fiscale IS NOT NULL"),
        ),
    )
