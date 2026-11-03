from datetime import date, datetime
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
    LargeBinary,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.models.profiles import AZIENDA_MAX_LENGTH
from rebase_core.regimes import PARTITA_IVA

# ---- matches and the contracts they write (REB-387) -------------------------------------

MATCH_STATES = ("bozza", "in_firma", "attivo", "concluso", "annullato")
# The Pigro link a match carries once it turns active (REB-497): `da_collegare` until a
# deal exists, `collegato` once `pigro_url` and `pigro_deal_id` point at it, `errore` on
# a failure the sweep may still retry -- `pigro_errore` then holds the cause and
# `pigro_attempted_at` the time, for the sweep's log and the admin's eye -- `rifiutato`
# on one the CRM itself refused (a 4xx, never retried).
PIGRO_STATES = ("da_collegare", "collegato", "errore", "rifiutato")
CONTRACT_KINDS = ("quadro", "lettera")
CONTRACT_STATES = ("generato", "in_attesa", "inviato", "firmato", "annullato", "disdetto")
# `contract_documents.regime` (REB-687): `partita_iva` or `occasionale`, 11 characters each,
# in the 12 that migration 0033 gave the column.
REGIME_MAX_LENGTH = 12
CODICE_FISCALE_MAX_LENGTH = 16
PARTITA_IVA_MAX_LENGTH = 11
DOMICILIO_MAX_LENGTH = 300
PEC_MAX_LENGTH = 320
# A client may be a foreign company, whose VAT number is not eleven Italian digits.
CLIENTE_PIVA_MAX_LENGTH = 32
SEDE_MAX_LENGTH = 300
LETTER_NUMBER_MAX_LENGTH = 12
TEXT_VERSION_MAX_LENGTH = 20
DOCUMENSO_ID_MAX_LENGTH = 100
# Why a document became `annullato`: the freelancer's own reason when they refused it on
# the signing site, or who cancelled it (REB-387 phase 3).
CANCEL_REASON_MAX_LENGTH = 500


class FreelancerFiscal(Base, PrimaryKeyMixin, TimestampMixin):
    """A freelancer's tax data as the contracts print them: one row per card
    (`uq_freelancer_fiscal_freelancer_id`). A table of its own rather than columns on
    `freelancers`, which PostHog's warehouse syncs whole (spec § 2); none of the four
    tables of this section is synced. `updated_by` is the admin who saved them last.
    `partita_iva` is `NULL` for a member who works without one, paid as a prestazione
    occasionale (REB-687)."""

    __tablename__ = "freelancer_fiscal"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), nullable=False
    )
    codice_fiscale: Mapped[str] = mapped_column(String(CODICE_FISCALE_MAX_LENGTH), nullable=False)
    partita_iva: Mapped[str | None] = mapped_column(String(PARTITA_IVA_MAX_LENGTH), default=None)
    domicilio: Mapped[str] = mapped_column(String(DOMICILIO_MAX_LENGTH), nullable=False)
    pec: Mapped[str | None] = mapped_column(String(PEC_MAX_LENGTH), default=None)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    __table_args__ = (Index("uq_freelancer_fiscal_freelancer_id", "freelancer_id", unique=True),)


class Match(Base, PrimaryKeyMixin, TimestampMixin):
    """A freelancer card paired with a company request, and the client's legal data as
    the letter prints them. `stato` is one of `MATCH_STATES`: `bozza` once the documents
    are generated, `in_firma` and `attivo` with the signature (phase 3), `concluso` and
    `annullato` by an admin."""

    __tablename__ = "matches"

    freelancer_id: Mapped[UUID] = mapped_column(ForeignKey("freelancers.id"), nullable=False)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    cliente_ragione_sociale: Mapped[str] = mapped_column(String(AZIENDA_MAX_LENGTH), nullable=False)
    cliente_piva: Mapped[str] = mapped_column(String(CLIENTE_PIVA_MAX_LENGTH), nullable=False)
    cliente_sede: Mapped[str] = mapped_column(String(SEDE_MAX_LENGTH), nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="bozza")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # The SHA-256 of the request this match was written from (REB-406): a retry of the
    # same client-generated id compares against it, so changed data is a 409 instead of
    # silently handing back the stale match. `NULL` for a match written before this
    # column existed, which a retry must treat the same as a mismatch.
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), default=None)
    # An admin's estimate of the engagement's billable days (REB-497), read back by
    # `MatchRead` and `MatchListItem`; `ore_previste` (elsewhere) is eight times this.
    giorni_previsti: Mapped[int | None] = mapped_column(Integer, default=None)
    # The letter's own numbers, copied from `data.lettera` when `create` writes it
    # (REB-497): a stable place for the report to read them from once the letter
    # itself is redrawn or replaced, without re-reading `contract_documents.data`.
    lettera_data_inizio: Mapped[date | None] = mapped_column(Date, default=None)
    lettera_data_fine: Mapped[date | None] = mapped_column(Date, default=None)
    lettera_compenso: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), default=None)
    # The company's own day rate, copied the same way and for the same reason as
    # `lettera_compenso` (P-REB-44): a referral reward is computed at signing, which
    # can be weeks after this match's own request, so an edit to the company's
    # request afterward never moves a reward this match already promised.
    company_budget_giornaliero: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    # The Pigro link this match carries once it turns active: `NULL` until then, one of
    # `PIGRO_STATES` after. `pigro_linked_at` is set only the once, when `collegato` is
    # first reached; `pigro_attempted_at` on every attempt, successful or not, for the
    # sweep's log and the admin's eye. `pigro_mail_sent_at` is the freelancer's own
    # notification mail, sent once on the first `collegato`.
    pigro_stato: Mapped[str | None] = mapped_column(String(20), default=None)
    pigro_slug: Mapped[str | None] = mapped_column(String(32), default=None)
    pigro_deal_id: Mapped[UUID | None] = mapped_column(default=None)
    pigro_url: Mapped[str | None] = mapped_column(Text, default=None)
    pigro_linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pigro_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    pigro_errore: Mapped[str | None] = mapped_column(Text, default=None)
    pigro_mail_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    __table_args__ = (
        Index("ix_matches_freelancer_created", "freelancer_id", "created_at"),
        CheckConstraint(
            "stato IN ('bozza', 'in_firma', 'attivo', 'concluso', 'annullato')",
            name="ck_matches_stato",
        ),
        CheckConstraint(
            "giorni_previsti IS NULL OR giorni_previsti BETWEEN 1 AND 366",
            name="ck_matches_giorni_previsti",
        ),
        CheckConstraint(
            "pigro_stato IS NULL OR pigro_stato IN "
            "('da_collegare', 'collegato', 'errore', 'rifiutato')",
            name="ck_matches_pigro_stato",
        ),
    )


class ContractDocument(Base, PrimaryKeyMixin, TimestampMixin):
    """One generated contract, its PDF in the row as the CV is (one place to delete
    from). A framework agreement (`quadro`) belongs to the freelancer and hangs on no
    match; a letter (`lettera`) belongs to a match and carries a `numero`, `YYYY-NNN`.
    `data` is every field value the PDF printed, so a document can be regenerated the
    same; `testo_bozza` says the text was still `status: draft`, a preview nothing may
    send. The Documenso columns, `notice_at` and `cancel_reason` are phase 3's: an
    envelope and its item are stored together, and an envelope is one document's."""

    __tablename__ = "contract_documents"

    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    # Which family of texts the document belongs to (REB-687): `partita_iva`, the
    # ordinary pair, or `occasionale`, a prestazione occasionale's. With `kind` it names
    # the text (`rebase_core.regimes.text_name`); a match's regime is its letter's.
    regime: Mapped[str] = mapped_column(
        String(REGIME_MAX_LENGTH),
        nullable=False,
        default=PARTITA_IVA,
        server_default=PARTITA_IVA,
    )
    freelancer_id: Mapped[UUID] = mapped_column(ForeignKey("freelancers.id"), nullable=False)
    match_id: Mapped[UUID | None] = mapped_column(ForeignKey("matches.id"), default=None)
    numero: Mapped[str | None] = mapped_column(String(LETTER_NUMBER_MAX_LENGTH), default=None)
    text_version: Mapped[str] = mapped_column(String(TEXT_VERSION_MAX_LENGTH), nullable=False)
    testo_bozza: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pdf: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False)
    documenso_id: Mapped[str | None] = mapped_column(String(DOCUMENSO_ID_MAX_LENGTH), default=None)
    # The envelope *item* the sealed copy is downloaded by: the create answers only the
    # envelope's id, so the hub reads this once, right after (probe § 4 and § 11.7).
    documenso_item_id: Mapped[str | None] = mapped_column(
        String(DOCUMENSO_ID_MAX_LENGTH), default=None
    )
    signing_url: Mapped[str | None] = mapped_column(Text, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    signed_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    # Each set only once its own signed-copy mail is accepted, so a restart or a refused
    # mail leaves that one `NULL` for the next `finish` to retry, without repeating a
    # mail the other recipient already got (REB-391).
    signed_copy_to_freelancer_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    signed_copy_to_rebase_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    notice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    cancel_reason: Mapped[str | None] = mapped_column(
        String(CANCEL_REASON_MAX_LENGTH), default=None
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    sent_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)

    __table_args__ = (
        Index("ix_contract_documents_freelancer", "freelancer_id", "kind", "created_at"),
        Index("ix_contract_documents_match_id", "match_id"),
        Index("uq_contract_documents_numero", "numero", unique=True),
        CheckConstraint("kind IN ('quadro', 'lettera')", name="ck_contract_documents_kind"),
        CheckConstraint(
            "regime IN ('partita_iva', 'occasionale')", name="ck_contract_documents_regime"
        ),
        CheckConstraint(
            "stato IN ('generato', 'in_attesa', 'inviato', 'firmato', 'annullato', 'disdetto')",
            name="ck_contract_documents_stato",
        ),
        CheckConstraint(
            "(kind = 'quadro') = (match_id IS NULL)",
            name="ck_contract_documents_match_for_letters",
        ),
        CheckConstraint(
            "(kind = 'lettera') = (numero IS NOT NULL)",
            name="ck_contract_documents_numero_for_letters",
        ),
        CheckConstraint(
            "stato <> 'disdetto' OR kind = 'quadro'",
            name="ck_contract_documents_notice_for_quadro",
        ),
        # The webhook finds its document by the envelope: one envelope, one document.
        Index("uq_contract_documents_documenso_id", "documenso_id", unique=True),
        CheckConstraint(
            "(documenso_id IS NULL) = (documenso_item_id IS NULL)",
            name="ck_contract_documents_envelope_item",
        ),
    )


class LetterCounter(Base):
    """The last letter number taken in a year: `2026-001`, `2026-002`, ... Bumped with
    `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` inside the transaction that writes
    the letter (`rebase_core.framework.next_letter_number`), so two letters written at
    once never share a number and a generation that fails leaves no gap."""

    __tablename__ = "contract_letter_counters"

    anno: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ultimo: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("ultimo >= 1", name="ck_contract_letter_counters_positive"),)
