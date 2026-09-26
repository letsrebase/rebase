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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin

UTM_MAX_LENGTH = 200
UTM_COLUMNS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_id")
NAME_MAX_LENGTH = 120
LINKEDIN_URL_MAX_LENGTH = 300
# A phone number for a company's own referente (REB-380): companies only ask for it
# today, so it stays optional at this level and required only by `CompanyCreate`'s own
# validation -- the same split `Freelancer`'s signup-born columns already keep between
# what the database allows and what a wizard demands.
TELEFONO_MAX_LENGTH = 40

# Every column added after the production table already existed, with the width each
# one needs. Migration 0001 adopts that table as it stands and adds these with
# `ADD COLUMN IF NOT EXISTS`, which is how the rows PigroCRM's sidecar collected keep
# their place. The UTM six arrived on 2026-09-08, `nome`/`cognome`/`linkedin_url` right
# after; the tuple stays because `test_migrations.py` proves the adoption against a
# table that predates all of them.
LATE_COLUMNS: tuple[tuple[str, int], ...] = (
    *((column, UTM_MAX_LENGTH) for column in UTM_COLUMNS),
    ("nome", NAME_MAX_LENGTH),
    ("cognome", NAME_MAX_LENGTH),
    ("linkedin_url", LINKEDIN_URL_MAX_LENGTH),
)


class Signup(Base, PrimaryKeyMixin):
    __tablename__ = "signups"

    # Same width and the same case-insensitive uniqueness as `users.email`: the
    # service lowers the address before writing, and the functional index is what
    # makes that a database fact rather than an app-level habit.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Where the signup came from, as the URL said it: the five standard UTM keys plus
    # `utm_id`, which LinkedIn fills with the ad set. All optional, all written once --
    # the first attribution of an address is the one that stays (see `SignupService`).
    # Added to a table that already existed in production: migration 0001 adds them.
    utm_source: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_medium: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_campaign: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_content: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_term: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_id: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    # Who they are. `SignupCreate` requires both, so nothing written from today on is
    # nameless -- but the twenty-four rows the form collected when it asked only for an
    # address have no name to give, so the columns stay nullable rather than being
    # backfilled with `''`, which would claim a name was recorded and found empty.
    # An empty column is filled in the next time that address signs up (`SignupService`).
    nome: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    cognome: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    # Optional for everyone, always: a freelance with no LinkedIn is still a freelance.
    linkedin_url: Mapped[str | None] = mapped_column(String(LINKEDIN_URL_MAX_LENGTH), default=None)

    __table_args__ = (
        Index("uq_orbiters_signups_email_lower", func.lower(email), unique=True),
        # Trigram search on the merged talenti list (REB-285): a bare sign-up has no
        # linked `users` row, so its own `nome`/`cognome`/`email` are what `q` matches
        # against. Migration 0013 creates the GIN indexes that serve these.
        Index(
            "ix_signups_nome_trgm",
            "nome",
            postgresql_using="gin",
            postgresql_ops={"nome": "gin_trgm_ops"},
        ),
        Index(
            "ix_signups_cognome_trgm",
            "cognome",
            postgresql_using="gin",
            postgresql_ops={"cognome": "gin_trgm_ops"},
        ),
        Index(
            "ix_signups_email_trgm",
            "email",
            postgresql_using="gin",
            postgresql_ops={"email": "gin_trgm_ops"},
        ),
    )


# ---- identity: one row per person, whatever they are to the hub ------------------------

USER_ROLES = ("member", "admin")


class User(Base, PrimaryKeyMixin, TimestampMixin):
    """One row per person the hub has an address for, one per `lower(email)`
    (`uq_users_email_lower`). A freelancer card, a company's own request and an admin
    grant are things a `users` row may have, zero or more of each, not three values
    competing for one row's identity (design record 2026-09-17, decision (c)): Lorenzo
    and Ivan are freelancers on some engagements, a company's own referente on others,
    and admins throughout. `role` is `member` or `admin` (`USER_ROLES`), validated in
    the service layer the way `stato` and `compilata_da` already are -- two values on a
    handful of rows do not buy a `user_roles` table. `attivo`, carried over from
    `admin_users.attivo` by REB-278's migration, still means only "this admin's tokens
    and sessions keep failing the same way": nothing deactivates a member yet, matching
    today."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    nome: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    cognome: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    linkedin_url: Mapped[str | None] = mapped_column(String(LINKEDIN_URL_MAX_LENGTH), default=None)
    telefono: Mapped[str | None] = mapped_column(String(TELEFONO_MAX_LENGTH), default=None)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="member")
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("uq_users_email_lower", func.lower(email), unique=True),
        # Trigram search (REB-285): `nome`/`cognome`/`email` back both the talenti
        # search (a card's identity lives here since REB-281) and the companies search
        # (a request's referente). Migration 0013 creates the GIN indexes.
        Index(
            "ix_users_nome_trgm",
            "nome",
            postgresql_using="gin",
            postgresql_ops={"nome": "gin_trgm_ops"},
        ),
        Index(
            "ix_users_cognome_trgm",
            "cognome",
            postgresql_using="gin",
            postgresql_ops={"cognome": "gin_trgm_ops"},
        ),
        Index(
            "ix_users_email_trgm",
            "email",
            postgresql_using="gin",
            postgresql_ops={"email": "gin_trgm_ops"},
        ),
    )


# ---- the hub proper: who wants to work, and who needs people --------------------------

FREELANCER_STATES = ("nuovo", "contattato", "attivo", "scartato")
# Who wrote the seven answers last (ORB-155): the person, through the wizard or the
# member area, or an admin, from what the public web says about a signup. Research never
# overwrites `persona`; the person's own words win.
COMPILATA_DA = ("persona", "admin")
COMPANY_STATES = ("nuovo", "contattato", "in_corso", "chiuso")
REMOTE_OPTIONS = ("remoto", "ibrido", "in_sede")
# The floor and ceiling of `Company.giorni_presenza` (REB-380): a work week, never "the
# whole week" -- five days in the office is `in_sede`, not `ibrido`.
GIORNI_PRESENZA_MIN = 1
GIORNI_PRESENZA_MAX = 4
POSIZIONE_MAX_LENGTH = 160
AZIENDA_MAX_LENGTH = 200
DURATA_MAX_LENGTH = 120
CV_FILENAME_MAX_LENGTH = 255
CV_MIME_MAX_LENGTH = 100
# Five megabytes: a CV is two pages, and the largest a designer's portfolio-as-CV gets
# before it stops being a CV. Enforced in the service on the bytes themselves, so the
# API and the MCP server cannot disagree about it.
CV_MAX_BYTES = 5 * 1024 * 1024


ORIGINE_MAX_LENGTH = 40


class UtmMixin:
    """Where a submission came from, as the page's URL said it. Optional, written once.
    `origine` is the page of the site the person started from (`home`, `pigrocrm`), which
    the landing's script puts on every door into the hub as `da=` (ORB-167): the campaign
    says which ad, this says which page."""

    origine: Mapped[str | None] = mapped_column(String(ORIGINE_MAX_LENGTH), default=None)

    utm_source: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_medium: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_campaign: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_content: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_term: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_id: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)


# Every column `UtmMixin` adds, `origine` included (a `Signup` has no `origine`, which
# is why `UTM_COLUMNS` above leaves it out), for code that copies a whole attribution
# from one row to another: a magic link's token onto the login it opens (REB-426).
ATTRIBUTION_COLUMNS = ("origine", *UTM_COLUMNS)


class Freelancer(Base, PrimaryKeyMixin, TimestampMixin, UtmMixin):
    """A person who filled in the hub's wizard: who they are, what they do, what they
    cost, and their CV -- in the row, as bytes. In the database rather than on a disk or
    a Drive because a CV is personal data with a retention to honour, and one place to
    delete from is one place (hub spec, 2026-09-09; Ivan's decision).

    One row per person (`uq_freelancers_user_id`): a person who submits twice has
    corrected their application, and the second submission updates the first. `stato`
    and `note` are the admin's, never the applicant's.

    Since ORB-155 a card can also be born from a signup, with what an admin found on
    the public web: a name, a LinkedIn profile, a position, some links. The CV, the rate,
    the position and the remote option are therefore nullable -- nothing public states
    them -- and the person completes the card from the member area. `compilata_da` says
    who wrote the answers last, so research can tell a card it may replace from one it
    may not. Migration 0007 loosened the columns; the wizard still requires all of them.

    Since REB-278 the identity is `users`: `user_id` is `NOT NULL UNIQUE`, at most one
    card per person. Migration B (REB-281) drops the card's own `nome`/`cognome`/
    `email`/`linkedin_url`, which `FreelancerService` kept in step with the linked
    `users` row only for as long as both copies existed: `users` is now the one place
    a name or an address lives, read through a join wherever the card is."""

    __tablename__ = "freelancers"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    cv_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    cv_filename: Mapped[str | None] = mapped_column(String(CV_FILENAME_MAX_LENGTH), default=None)
    cv_mime: Mapped[str | None] = mapped_column(String(CV_MIME_MAX_LENGTH), default=None)
    cv_size: Mapped[int | None] = mapped_column(Integer, default=None)
    tariffa_giornaliera: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    posizione: Mapped[str | None] = mapped_column(String(POSIZIONE_MAX_LENGTH), default=None)
    remoto: Mapped[str | None] = mapped_column(String(10), default=None)
    links: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="nuovo")
    note: Mapped[str | None] = mapped_column(Text, default=None)
    compilata_da: Mapped[str] = mapped_column(String(10), nullable=False, default="persona")
    # `None` while the card is live; a moment once an admin soft-deletes it (REB-347).
    # Never a hard delete -- see `AdminAction`, whose "deleted"/"restored" entries are
    # what makes flipping this back to `None` a real undo rather than a fresh guess.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # «Vetted», REB-509: a manual flag an admin sets in «Talenti» or over MCP, shown as
    # a badge in the talent cloud; the public builder's page does not distinguish. Not
    # an `ADMIN_ACTION_KINDS` "overridden" -- it is its own kind, `vetted`, since it is
    # neither a field correction nor reversible the same way (there is no "before").
    vetted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    vetted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)

    __table_args__ = (
        Index("ix_freelancers_created_at", "created_at"),
        Index("uq_freelancers_user_id", "user_id", unique=True),
        # Trigram search on the merged talenti list (REB-285): `nome`/`cognome`/
        # `email` live on `users` (indexed there); `posizione` is the one searchable
        # field that lives here. Migration 0013 creates the GIN index.
        Index(
            "ix_freelancers_posizione_trgm",
            "posizione",
            postgresql_using="gin",
            postgresql_ops={"posizione": "gin_trgm_ops"},
        ),
    )


class Company(Base, PrimaryKeyMixin, TimestampMixin, UtmMixin):
    """A company that needs people: the project in a few lines, from when and for how
    long, and what a day is worth to them. Several rows per company are fine -- a company
    has several projects -- so nothing is unique here but the id.

    Since REB-278 `user_id` points at the referente's `users` row, not unique -- a
    person files several requests over time. Migration B (REB-281) drops the row's own
    `referente`/`email`, which duplicated the linked `users` row only for the A-to-B
    window: the referente's name and address are read off `users` now, one place for
    every request the same person ever filed, not a free-text copy each request could
    drift from.

    REB-380 adds four more answers, all `NOT NULL` -- a company request has no
    `Freelancer`-style "born from a signup, completed later" case, so nothing here is
    optional at the database the way the freelancer card's own columns are: `remoto`
    (`REMOTE_OPTIONS`, mirroring `Freelancer.remoto`'s shape), `giorni_presenza`
    (nullable, `NULL` unless `remoto` is `'ibrido'`, tied to it by
    `ck_companies_giorni_presenza_together` the same "together or neither" shape
    `pigrocrm`'s own `giorni_pagamento`/`pagamento_fine_mese` pair uses), `numero_risorse`
    (how many people the request needs) and `figura_richiesta` (the role, free text
    like `Freelancer.posizione`, with the same trigram index for the admin's search)."""

    __tablename__ = "companies"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    nome_azienda: Mapped[str] = mapped_column(String(AZIENDA_MAX_LENGTH), nullable=False)
    figura_richiesta: Mapped[str] = mapped_column(String(POSIZIONE_MAX_LENGTH), nullable=False)
    progetto: Mapped[str] = mapped_column(Text, nullable=False)
    periodo_da: Mapped[date] = mapped_column(Date, nullable=False)
    durata: Mapped[str] = mapped_column(String(DURATA_MAX_LENGTH), nullable=False)
    budget_giornaliero: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    remoto: Mapped[str] = mapped_column(String(10), nullable=False)
    giorni_presenza: Mapped[int | None] = mapped_column(Integer, default=None)
    numero_risorse: Mapped[int] = mapped_column(Integer, nullable=False)
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="nuovo")
    note: Mapped[str | None] = mapped_column(Text, default=None)
    # Same soft-delete as `Freelancer.deleted_at`, same reason: several requests per
    # company, and a wrongly-deleted one is a click away from being live again.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_companies_created_at", "created_at"),
        # Trigram search (REB-285): `nome`/`referente`/`email` are the linked `users`
        # row's own (indexed there); `nome_azienda`, `progetto` and `figura_richiesta`
        # (REB-380) are this table's. Migration 0013/0016 create the GIN indexes.
        Index(
            "ix_companies_nome_azienda_trgm",
            "nome_azienda",
            postgresql_using="gin",
            postgresql_ops={"nome_azienda": "gin_trgm_ops"},
        ),
        Index(
            "ix_companies_progetto_trgm",
            "progetto",
            postgresql_using="gin",
            postgresql_ops={"progetto": "gin_trgm_ops"},
        ),
        Index(
            "ix_companies_figura_richiesta_trgm",
            "figura_richiesta",
            postgresql_using="gin",
            postgresql_ops={"figura_richiesta": "gin_trgm_ops"},
        ),
        CheckConstraint(
            "giorni_presenza IS NULL OR "
            f"(giorni_presenza >= {GIORNI_PRESENZA_MIN} "
            f"AND giorni_presenza <= {GIORNI_PRESENZA_MAX})",
            name="ck_companies_giorni_presenza_range",
        ),
        CheckConstraint(
            "(remoto = 'ibrido') = (giorni_presenza IS NOT NULL)",
            name="ck_companies_giorni_presenza_together",
        ),
        CheckConstraint("numero_risorse >= 1", name="ck_companies_numero_risorse_positive"),
    )


# ---- matches and the contracts they write (REB-387) -------------------------------------

MATCH_STATES = ("bozza", "in_firma", "attivo", "concluso", "annullato")
CONTRACT_KINDS = ("quadro", "lettera")
CONTRACT_STATES = ("generato", "in_attesa", "inviato", "firmato", "annullato", "disdetto")
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
    """A freelancer's tax data as the two contracts print them: one row per card
    (`uq_freelancer_fiscal_freelancer_id`). A table of its own rather than columns on
    `freelancers`, which PostHog's warehouse syncs whole (spec § 2); none of the four
    tables of this section is synced. `updated_by` is the admin who saved them last."""

    __tablename__ = "freelancer_fiscal"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), nullable=False
    )
    codice_fiscale: Mapped[str] = mapped_column(String(CODICE_FISCALE_MAX_LENGTH), nullable=False)
    partita_iva: Mapped[str] = mapped_column(String(PARTITA_IVA_MAX_LENGTH), nullable=False)
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

    __table_args__ = (
        Index("ix_matches_freelancer_created", "freelancer_id", "created_at"),
        CheckConstraint(
            "stato IN ('bozza', 'in_firma', 'attivo', 'concluso', 'annullato')",
            name="ck_matches_stato",
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


# ---- comments: what an admin or an assistant says about a row, over time ---------------

COMMENT_ENTITY_TYPES = ("freelancer", "company")
COMMENT_MAX_LENGTH = 4000
AUTORE_MAX_LENGTH = NAME_MAX_LENGTH


class Comment(Base, PrimaryKeyMixin):
    """One remark about a freelancer or a company, signed and dated. Append-only, like
    PigroCRM's timeline entries: no update and no delete anywhere in the hub, so a
    thread read in a month is the thread as it was written. `note` on the row itself
    stays the one-line summary an admin overwrites; this is the history beside it
    (ORB-59, Ivan's decision of 2026-09-09).

    `entity_type` plus `entity_id` rather than two nullable foreign keys: the service
    checks the row exists before writing, and one table with one index is what a
    thread on a third kind of row would reuse without a migration on this table."""

    __tablename__ = "comments"

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    testo: Mapped[str] = mapped_column(Text, nullable=False)
    autore: Mapped[str] = mapped_column(String(AUTORE_MAX_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_comments_entity", "entity_type", "entity_id", "created_at"),)


# ---- what an admin overrode, cleared, deleted or restored, and when --------------------

ADMIN_ACTION_ENTITY_TYPES = ("freelancer", "company", "match", "freelancer_fiscal")
# `overridden` covers both "set" and "clear" -- clearing is setting a field to its empty
# value, and `rebase_core.audit.field_changes` records the same `changed`/`before`/`after`
# shape either way. `cleared` is only for the one thing that shape must never carry: the
# freelancer's CV, whose bytes are personal data that must not be duplicated into an audit
# row (`FreelancerService.clear_cv`). `deleted`/`restored` are the whole story on their own,
# with an empty payload.
# REB-387 adds the matches' own kinds, on entity type `match` (phase 3 writes the last
# four), and `fiscal_updated` on `freelancer_fiscal`, whose payload names the fields that
# changed and never their values: a tax identifier is not copied into this table.
# REB-509 adds `vetted`, on entity type `freelancer`: «Vetted» flips `Freelancer.vetted_at`
# on or off, not a field an "overridden"/"cleared" pair already describes.
ADMIN_ACTION_KINDS = (
    "overridden",
    "cleared",
    "deleted",
    "restored",
    "match_created",
    "match_cancelled",
    "match_closed",
    "fiscal_updated",
    "documents_sent",
    "document_cancelled",
    "mail_resent",
    "notice_recorded",
    "vetted",
)


class AdminAction(Base, PrimaryKeyMixin):
    """One row per admin-driven change to a `Freelancer`/`Company` record beyond
    `stato`/`note` (REB-347, `Comment`'s own append-only discipline): who, when, and for
    an `overridden`/`cleared` entry, the field's value before and after
    (`rebase_core.audit.field_changes`). Append-only like `Comment` -- an admin reverses
    an action by writing a new one from this row's own `payload`, never by editing it.

    `entity_type` plus `entity_id` rather than a foreign key per entity, the same
    reasoning `Comment` gives: the service checks the row exists before writing, and one
    table with one index is what a third entity's own trail would reuse without a
    migration here."""

    __tablename__ = "admin_actions"

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    admin_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_admin_actions_entity", "entity_type", "entity_id", "created_at"),)


# ---- an admin's own tokens, for agents --------------------------------------------------

ADMIN_SESSION_TOKEN_HASH_LENGTH = 64  # sha256, hex
ADMIN_TOKEN_PREFIX_LENGTH = 20


class AdminToken(Base, PrimaryKeyMixin, TimestampMixin):
    """A personal token of an admin, for an agent (REB-213): the credential the MCP
    server takes as a bearer. Only the sha256 of the value is stored; the value itself
    is shown once, at creation, and never again. `prefix` is the visible head of it, so a
    list can tell two tokens apart. No expiry: `revoked_at` is the end of a token, and a
    revoked one is refused like an unknown one.

    Since REB-278 `user_id` (`users.id`, `role == 'admin'`) is the owner; migration B
    (REB-281) drops the `admin_id` column it replaced, once `admin_users` itself is
    gone and nothing points at it any more."""

    __tablename__ = "admin_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    nome: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(ADMIN_SESSION_TOKEN_HASH_LENGTH), nullable=False, unique=True
    )
    prefix: Mapped[str] = mapped_column(String(ADMIN_TOKEN_PREFIX_LENGTH), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        # Trigram search (REB-313): Agenti's list matches a token by its own `nome`,
        # the name the admin gave it -- there is no linked `users` row to search
        # instead, unlike Talenti and Aziende.
        Index(
            "ix_admin_tokens_nome_trgm",
            "nome",
            postgresql_using="gin",
            postgresql_ops={"nome": "gin_trgm_ops"},
        ),
    )


# ---- the member area: how anyone gets back in -------------------------------------------

TOKEN_HASH_LENGTH = 64  # sha256, hex


class MagicLinkToken(Base, PrimaryKeyMixin, UtmMixin):
    """One link, one entry. The raw value travels in the mail and nowhere else; the row
    holds its sha256, a deadline (`magic_link_minutes`) and the moment it was spent, so a
    link forwarded or fetched twice opens nothing the second time. Hangs on the person
    with `ON DELETE CASCADE`: deleting them deletes their way in.

    Since REB-278 the link is for anyone with a `users` row, not only a freelancer,
    `user_id` is the owner; migration B (REB-281) drops the `freelancer_id` it replaced,
    once nothing writes it any more.

    Since REB-426 it also holds the attribution the login page arrived with (`UtmMixin`),
    only to hand it to the `Login` the link opens: the page asks for the link, the mail
    opens it, and the token is the one thing both ends share."""

    __tablename__ = "magic_link_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Login(Base, PrimaryKeyMixin, UtmMixin):
    """One row per time somebody entered through a magic link (ORB-158): who, when, and
    since REB-426 the campaign the login page was opened from (`UtmMixin`, copied off the
    token), so an outreach mail's link says who came in from it without a cookie. Nothing
    else -- no address, no user agent. A log rather than the session table,
    which forgets a session on logout and on expiry, so the admin can read who came in
    and when a week later. Written by `UserService.enter` in the commit that opens the
    session. Hangs on the person with `ON DELETE CASCADE`, like the sessions.

    Renamed from `member_logins`/`MemberLogin` in migration B (REB-281), the same
    reasoning as `UserSession` below: `user_id` is the owner for anyone who signs in,
    member or admin, and "member" stopped describing who is in this table the moment
    an admin's own login started landing here too (REB-278). `freelancer_id`, the
    column the rename leaves behind, is dropped in the same migration."""

    __tablename__ = "logins"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GuideDownload(Base, PrimaryKeyMixin):
    """One row per time somebody fetched the guide (ORB-156): who and when, and nothing
    else. A log rather than a counter on the freelancer, so the admin can read a trend
    and see who came back for it; nothing about the file itself is stored, since the
    file is package data and the same for everybody. Hangs on the person with
    `ON DELETE CASCADE`: a deleted person takes their downloads.

    Since REB-278 `user_id` is the owner; migration B (REB-281) drops the
    `freelancer_id` it replaced."""

    __tablename__ = "guide_downloads"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserSession(Base, PrimaryKeyMixin):
    """The session's shape: opaque cookie, hashed at rest, sliding expiry, revoked by
    deleting the row. One table and one cookie for everyone who signs in, member and
    admin alike (REB-278): a role is read off the `users` row it resolves to, not off
    which table the session lives in.

    The table is renamed from `member_sessions` in migration B (REB-281): "member"
    stopped describing who is in it the moment an admin's own session started landing
    here too, the same rule `REB-207`-`REB-212`/`REB-214` already applied elsewhere in
    this codebase (an identifier that actively misleads gets renamed). The Python class
    keeps the `Member` prefix off but stops short of the bare `Session` the table name
    suggests: every service already imports `sqlalchemy.orm.Session` under that exact
    name, and a class sharing it would silently shadow one or the other on whichever
    import runs second. `freelancer_id`, the column the rename leaves behind, is
    dropped in the same migration."""

    __tablename__ = "sessions"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---- the team builder and the talent cloud (REB-509, design 2026-09-25) ----------------

CARD_SENIORITIES = ("junior", "mid", "senior", "lead")
TEAM_REQUEST_STATES = ("nuova", "contattata", "chiusa")
TEAM_PROPOSAL_ORIGINS = ("pubblico", "cloud", "admin")
TEAM_REQUEST_ORIGINS = ("pubblico", "cloud")
TALENT_ANSWERS = ("si", "no")
# An Anthropic model id, `claude-opus-5` today: short, but the SDK's own ids run longer
# (`claude-opus-4-20250514`), so the width is generous the same way every other width in
# this file is.
CARD_MODEL_MAX_LENGTH = 60


class FreelancerCard(Base, TimestampMixin):
    """Claude's anonymous read of a freelancer's CV (spec § 2.1), one row per card
    (`freelancer_id` is the primary key: at most one per person, gone with the person on
    `ON DELETE CASCADE`). `cv_sha256`, `card`, `model` and the two token counts are the
    *last successful* generation, written together by `rebase_core.cards.CardWriter`
    (spec § 5.1) or not at all -- a freelancer whose CV has never produced a card has
    every one of these `NULL`. `error` and `error_cv_sha256` are the *last failure*,
    independent of whether a card exists at all: an empty CV, a refusal, a `max_tokens`
    stop or a body that is not the shape leaves the previous card exactly as it was and
    only these two change, so the same CV is not retried and paid for again until it
    changes."""

    __tablename__ = "freelancer_cards"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), primary_key=True
    )
    cv_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    card: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    model: Mapped[str | None] = mapped_column(String(CARD_MODEL_MAX_LENGTH), default=None)
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    error_cv_sha256: Mapped[str | None] = mapped_column(String(64), default=None)


class TeamProposal(Base, PrimaryKeyMixin):
    """One team the engine proposed (spec § 3.4), immutable once written -- no
    `TimestampMixin`: a «Rigenera» writes a new row with `previous_id` pointing at the
    one it replaces, never edits this one. `luogo`, `team` and `economia` are the JSONB
    the engine and § 3.3 describe; `model` and the three token counts are `usage` from
    the call that produced this row, always present since a row is written only once a
    proposal actually succeeds -- a failed attempt raises `LlmUnavailable` and writes
    nothing here. `origine` says who asked (`TEAM_PROPOSAL_ORIGINS`): the public page, a
    signed-in cloud user, or an admin from the talent cloud; `user_id` is that person,
    `NULL` for a public visitor with no account at all."""

    __tablename__ = "team_proposals"

    descrizione: Mapped[str] = mapped_column(Text, nullable=False)
    # The «Rigenera» note, e.g. «togli il designer» (spec § 3.4): `NULL` on a first ask.
    nota: Mapped[str | None] = mapped_column(Text, default=None)
    previous_id: Mapped[UUID | None] = mapped_column(ForeignKey("team_proposals.id"), default=None)
    riassunto: Mapped[str] = mapped_column(Text, nullable=False)
    luogo: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    team: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    economia: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(String(CARD_MODEL_MAX_LENGTH), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    origine: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_team_proposals_created_at", "created_at"),
        CheckConstraint(
            "origine IN ('pubblico', 'cloud', 'admin')", name="ck_team_proposals_origine"
        ),
    )


class TeamRequest(Base, PrimaryKeyMixin, TimestampMixin):
    """A company's «Assumi team» (spec § 3.2, § 3.5): who asked (`azienda`, `email`,
    `telefono`, the same shape the company wizard already collects), and which proposal
    it is for -- `proposal_id`, `NULL` for a single-talent request with no team proposal
    behind it, unique where set (`uq_team_requests_proposal_id`) so a double click on
    «Assumi team» is a `409`, not a second row. `origine` is `pubblico` or `cloud`
    (`TEAM_REQUEST_ORIGINS`); `user_id` and `company_id` are the cloud user and their own
    request row, both `NULL` for a public visitor with neither. `stato` is the admin's
    own progress on it (`TEAM_REQUEST_STATES`), `note` theirs too; `contacted_at` and
    `closed_at` are each set once, by «Contatta i talenti» and «Chiudi»."""

    __tablename__ = "team_requests"

    proposal_id: Mapped[UUID | None] = mapped_column(ForeignKey("team_proposals.id"), default=None)
    origine: Mapped[str] = mapped_column(String(10), nullable=False)
    azienda: Mapped[str] = mapped_column(String(AZIENDA_MAX_LENGTH), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    telefono: Mapped[str | None] = mapped_column(String(TELEFONO_MAX_LENGTH), default=None)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"), default=None)
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="nuova")
    note: Mapped[str | None] = mapped_column(Text, default=None)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index(
            "uq_team_requests_proposal_id",
            "proposal_id",
            unique=True,
            postgresql_where=text("proposal_id IS NOT NULL"),
        ),
        Index("ix_team_requests_stato_created_at", "stato", "created_at"),
        CheckConstraint(
            "stato IN ('nuova', 'contattata', 'chiusa')", name="ck_team_requests_stato"
        ),
        CheckConstraint("origine IN ('pubblico', 'cloud')", name="ck_team_requests_origine"),
    )


class TeamRequestTalent(Base, PrimaryKeyMixin):
    """One talent asked for on a request (spec § 3.5, § 3.6): `ruolo` is the role they
    were proposed for, carried here on its own rather than re-read off the proposal's
    `team` JSONB, so a request keeps its own copy even if the proposal above it is later
    regenerated. `token_hash` is the availability mail's one-use token (SHA-256 at rest,
    `token_urlsafe(32)` sent, the magic link's own shape), `NULL` until the first send;
    `mail_sent_at`, `risposta` (`TALENT_ANSWERS`) and `risposta_at` fill in as the mail
    goes out and the talent answers, all `NULL` until then. `unique (request_id,
    freelancer_id)`: a talent is asked once per request."""

    __tablename__ = "team_request_talents"

    request_id: Mapped[UUID] = mapped_column(ForeignKey("team_requests.id"), nullable=False)
    freelancer_id: Mapped[UUID] = mapped_column(ForeignKey("freelancers.id"), nullable=False)
    ruolo: Mapped[str] = mapped_column(String(POSIZIONE_MAX_LENGTH), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(TOKEN_HASH_LENGTH), default=None)
    mail_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    risposta: Mapped[str | None] = mapped_column(String(10), default=None)
    risposta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index(
            "uq_team_request_talents_request_freelancer",
            "request_id",
            "freelancer_id",
            unique=True,
        ),
        Index("uq_team_request_talents_token_hash", "token_hash", unique=True),
        CheckConstraint("risposta IN ('si', 'no')", name="ck_team_request_talents_risposta"),
    )


class TalentCloudGrant(Base, PrimaryKeyMixin):
    """A company's access to the private talent cloud (spec § 4.1): opened by «Apri il
    talent cloud» on a company request's page, for the referente's own `user_id`, closed
    by «Revoca». One live grant per user (`revoked_at IS NULL`, the partial unique index
    below): the admin route checks for one first, so a second «Apri» on an
    already-open grant answers the existing row rather than doubling it. `company_id` is
    the request the grant was opened from, kept for the trail even though the same user
    may later hold a grant from a different one; `granted_by` and `revoked_by` are the
    admin who acted."""

    __tablename__ = "talent_cloud_grants"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    granted_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index(
            "uq_talent_cloud_grants_user_id_live",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
