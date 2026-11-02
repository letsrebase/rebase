from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Computed,
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
from rebase_core.models.identity import UTM_COLUMNS, UTM_MAX_LENGTH

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
    is the admin's, never the applicant's; what an admin says about the person is the
    `Note` thread (REB-856).

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
    # The digest of `cv_bytes`, generated and stored by the database (migration 0047): the
    # card's staleness reads it instead of hashing every CV on each pass. `NULL` with no CV.
    cv_sha256: Mapped[str | None] = mapped_column(
        String(64), Computed("encode(sha256(cv_bytes), 'hex')", persisted=True)
    )
    cv_filename: Mapped[str | None] = mapped_column(String(CV_FILENAME_MAX_LENGTH), default=None)
    # When the CV in this row was stored (REB-800): what «the newest CV» means when two
    # cards of one person are merged. `updated_at` moves on every status change, so it
    # cannot say it. `NULL` with no CV; migration 0035 backfills it from `updated_at`.
    cv_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    cv_mime: Mapped[str | None] = mapped_column(String(CV_MIME_MAX_LENGTH), default=None)
    cv_size: Mapped[int | None] = mapped_column(Integer, default=None)
    tariffa_giornaliera: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    posizione: Mapped[str | None] = mapped_column(String(POSIZIONE_MAX_LENGTH), default=None)
    remoto: Mapped[str | None] = mapped_column(String(10), default=None)
    links: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    stato: Mapped[str] = mapped_column(String(20), nullable=False, default="nuovo")
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
    # What an admin says of the person's level (REB-799), `NULL` while nobody has: it
    # beats the card's `seniority` and `anni` wherever the card is read, and lives here,
    # not in `freelancer_cards`, because a regenerated or retired card must not erase it.
    seniority_admin: Mapped[str | None] = mapped_column(String(10), default=None)
    anni_admin: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        Index("ix_freelancers_created_at", "created_at"),
        CheckConstraint(
            "stato IN ('nuovo', 'contattato', 'attivo', 'scartato')", name="ck_freelancers_stato"
        ),
        CheckConstraint("compilata_da IN ('persona', 'admin')", name="ck_freelancers_compilata_da"),
        CheckConstraint(
            "remoto IS NULL OR remoto IN ('remoto', 'ibrido', 'in_sede')",
            name="ck_freelancers_remoto",
        ),
        CheckConstraint(
            "seniority_admin IS NULL OR seniority_admin IN ('junior', 'mid', 'senior', 'lead')",
            name="ck_freelancers_seniority_admin",
        ),
        CheckConstraint(
            "anni_admin IS NULL OR (anni_admin >= 0 AND anni_admin <= 60)",
            name="ck_freelancers_anni_admin_range",
        ),
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
    # Same soft-delete as `Freelancer.deleted_at`, same reason: several requests per
    # company, and a wrongly-deleted one is a click away from being live again.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Where this request stands in rebase's own CRM (REB-868, spec 2026-10-30 § 4): the
    # states are a match's `pigro_stato` (`PIGRO_STATES`), `NULL` for a row written before
    # the link existed, which the sweep never picks up. `crm_bozza` is the AI's draft of the
    # first successful call, kept so a retry never asks again.
    crm_stato: Mapped[str | None] = mapped_column(String(20), default=None)
    crm_customer_id: Mapped[UUID | None] = mapped_column(default=None)
    crm_deal_id: Mapped[UUID | None] = mapped_column(default=None)
    crm_deal_url: Mapped[str | None] = mapped_column(Text, default=None)
    crm_errore: Mapped[str | None] = mapped_column(Text, default=None)
    crm_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    crm_bozza: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), default=None)

    __table_args__ = (
        Index("ix_companies_created_at", "created_at"),
        CheckConstraint(
            "stato IN ('nuovo', 'contattato', 'in_corso', 'chiuso')", name="ck_companies_stato"
        ),
        CheckConstraint("remoto IN ('remoto', 'ibrido', 'in_sede')", name="ck_companies_remoto"),
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
        CheckConstraint(
            "crm_stato IS NULL OR crm_stato IN "
            "('da_collegare', 'collegato', 'errore', 'rifiutato')",
            name="ck_companies_crm_stato",
        ),
    )
