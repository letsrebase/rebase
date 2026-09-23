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

ADMIN_ACTION_ENTITY_TYPES = ("freelancer", "company")
# `overridden` covers both "set" and "clear" -- clearing is setting a field to its empty
# value, and `rebase_core.audit.field_changes` records the same `changed`/`before`/`after`
# shape either way. `cleared` is only for the one thing that shape must never carry: the
# freelancer's CV, whose bytes are personal data that must not be duplicated into an audit
# row (`FreelancerService.clear_cv`). `deleted`/`restored` are the whole story on their own,
# with an empty payload.
ADMIN_ACTION_KINDS = ("overridden", "cleared", "deleted", "restored")


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


class MagicLinkToken(Base, PrimaryKeyMixin):
    """One link, one entry. The raw value travels in the mail and nowhere else; the row
    holds its sha256, a deadline (`magic_link_minutes`) and the moment it was spent, so a
    link forwarded or fetched twice opens nothing the second time. Hangs on the person
    with `ON DELETE CASCADE`: deleting them deletes their way in.

    Since REB-278 the link is for anyone with a `users` row, not only a freelancer,
    `user_id` is the owner; migration B (REB-281) drops the `freelancer_id` it replaced,
    once nothing writes it any more."""

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


class Login(Base, PrimaryKeyMixin):
    """One row per time somebody entered through a magic link (ORB-158): who and when,
    and nothing else -- no address, no user agent. A log rather than the session table,
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
