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

FREELANCER_STATES = ("new", "contacted", "active", "discarded")
# A row of the Talenti list: a card's own state, or `lead` for a bare sign-up with no card.
TALENT_STATES = (*FREELANCER_STATES, "lead")
# Who wrote the seven answers last (ORB-155): the person, through the wizard or the
# member area, or an admin, from what the public web says about a signup. Research never
# overwrites `person`; the person's own words win.
FILLED_BY = ("person", "admin")
COMPANY_STATES = ("new", "contacted", "in_progress", "closed")
REMOTE_OPTIONS = ("remote", "hybrid", "on_site")
# The floor and ceiling of `Company.onsite_days` (REB-380): a work week, never "the
# whole week" -- five days in the office is `on_site`, not `hybrid`.
ONSITE_DAYS_MIN = 1
ONSITE_DAYS_MAX = 4
JOB_TITLE_MAX_LENGTH = 160
COMPANY_MAX_LENGTH = 200
DURATION_MAX_LENGTH = 120
CV_FILENAME_MAX_LENGTH = 255
CV_MIME_MAX_LENGTH = 100
# Five megabytes: a CV is two pages, and the largest a designer's portfolio-as-CV gets
# before it stops being a CV. Enforced in the service on the bytes themselves, so the
# API and the MCP server cannot disagree about it.
CV_MAX_BYTES = 5 * 1024 * 1024


ORIGIN_MAX_LENGTH = 40


class UtmMixin:
    """Where a submission came from, as the page's URL said it. Optional, written once.
    `origin` is the page of the site the person started from (`home`, `pigrocrm`), which
    the landing's script puts on every door into the hub as `da=` (ORB-167): the campaign
    says which ad, this says which page."""

    origin: Mapped[str | None] = mapped_column(String(ORIGIN_MAX_LENGTH), default=None)

    utm_source: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_medium: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_campaign: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_content: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_term: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)
    utm_id: Mapped[str | None] = mapped_column(String(UTM_MAX_LENGTH), default=None)


# Every column `UtmMixin` adds, `origin` included (a `Signup` has no `origin`, which
# is why `UTM_COLUMNS` above leaves it out), for code that copies a whole attribution
# from one row to another: a magic link's token onto the login it opens (REB-426).
ATTRIBUTION_COLUMNS = ("origin", *UTM_COLUMNS)


class Freelancer(Base, PrimaryKeyMixin, TimestampMixin, UtmMixin):
    """A person who filled in the hub's wizard: who they are, what they do, what they
    cost, and their CV -- in the row, as bytes. In the database rather than on a disk or
    a Drive because a CV is personal data with a retention to honour, and one place to
    delete from is one place (hub spec, 2026-09-09; Ivan's decision).

    One row per person (`uq_freelancers_user_id`): a person who submits twice has
    corrected their application, and the second submission updates the first. `status`
    is the admin's, never the applicant's; what an admin says about the person is the
    `Note` thread (REB-856).

    Since ORB-155 a card can also be born from a signup, with what an admin found on
    the public web: a name, a LinkedIn profile, a position, some links. The CV, the rate,
    the position and the remote option are therefore nullable -- nothing public states
    them -- and the person completes the card from the member area. `filled_by` says
    who wrote the answers last, so research can tell a card it may replace from one it
    may not. Migration 0007 loosened the columns; the wizard still requires all of them.

    Since REB-278 the identity is `users`: `user_id` is `NOT NULL UNIQUE`, at most one
    card per person. Migration B (REB-281) drops the card's own `first_name`/`last_name`/
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
    daily_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    job_title: Mapped[str | None] = mapped_column(String(JOB_TITLE_MAX_LENGTH), default=None)
    remote: Mapped[str | None] = mapped_column(String(10), default=None)
    links: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    filled_by: Mapped[str] = mapped_column(String(10), nullable=False, default="person")
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
    # beats the card's `seniority` and `experience_years` wherever the card is read, and lives here,
    # not in `freelancer_cards`, because a regenerated or retired card must not erase it.
    seniority_admin: Mapped[str | None] = mapped_column(String(10), default=None)
    experience_years_admin: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        Index("ix_freelancers_created_at", "created_at"),
        CheckConstraint(
            "status IN ('new', 'contacted', 'active', 'discarded')", name="ck_freelancers_status"
        ),
        CheckConstraint("filled_by IN ('person', 'admin')", name="ck_freelancers_filled_by"),
        CheckConstraint(
            "remote IS NULL OR remote IN ('remote', 'hybrid', 'on_site')",
            name="ck_freelancers_remote",
        ),
        CheckConstraint(
            "seniority_admin IS NULL OR seniority_admin IN ('junior', 'mid', 'senior', 'lead')",
            name="ck_freelancers_seniority_admin",
        ),
        CheckConstraint(
            "experience_years_admin IS NULL OR "
            "(experience_years_admin >= 0 AND experience_years_admin <= 60)",
            name="ck_freelancers_experience_years_admin_range",
        ),
        Index("uq_freelancers_user_id", "user_id", unique=True),
        # Trigram search on the merged talents list (REB-285): `first_name`/`last_name`/
        # `email` live on `users` (indexed there); `job_title` is the one searchable
        # field that lives here. Migration 0013 creates the GIN index.
        Index(
            "ix_freelancers_job_title_trgm",
            "job_title",
            postgresql_using="gin",
            postgresql_ops={"job_title": "gin_trgm_ops"},
        ),
    )


class Company(Base, PrimaryKeyMixin, TimestampMixin, UtmMixin):
    """A company that needs people: the project in a few lines, from when and for how
    long, and what a day is worth to them. Several rows per company are fine -- a company
    has several projects -- so nothing is unique here but the id.

    Since REB-278 `user_id` points at the contact person's `users` row, not unique -- a
    person files several requests over time. Migration B (REB-281) drops the row's own
    `contact_person`/`email`, which duplicated the linked `users` row only for the A-to-B
    window: the contact person's name and address are read off `users` now, one place for
    every request the same person ever filed, not a free-text copy each request could
    drift from.

    REB-380 adds four more answers, all `NOT NULL` -- a company request has no
    `Freelancer`-style "born from a signup, completed later" case, so nothing here is
    optional at the database the way the freelancer card's own columns are: `remote`
    (`REMOTE_OPTIONS`, mirroring `Freelancer.remote`'s shape), `onsite_days`
    (nullable, `NULL` unless `remote` is `'hybrid'`, tied to it by
    `ck_companies_onsite_days_together` the same "together or neither" shape
    `pigrocrm`'s own `payment_days`/`payment_end_of_month` pair uses), `headcount`
    (how many people the request needs) and `requested_role` (the role, free text
    like `Freelancer.job_title`, with the same trigram index for the admin's search)."""

    __tablename__ = "companies"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(COMPANY_MAX_LENGTH), nullable=False)
    requested_role: Mapped[str] = mapped_column(String(JOB_TITLE_MAX_LENGTH), nullable=False)
    project: Mapped[str] = mapped_column(Text, nullable=False)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    duration: Mapped[str] = mapped_column(String(DURATION_MAX_LENGTH), nullable=False)
    daily_budget: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    remote: Mapped[str] = mapped_column(String(10), nullable=False)
    onsite_days: Mapped[int | None] = mapped_column(Integer, default=None)
    headcount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    # Same soft-delete as `Freelancer.deleted_at`, same reason: several requests per
    # company, and a wrongly-deleted one is a click away from being live again.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Where this request stands in rebase's own CRM (REB-868, spec 2026-10-30 § 4): the
    # states are a match's `pigro_status` (`PIGRO_STATES`), `NULL` for a row written before
    # the link existed, which the sweep never picks up. `crm_draft` is the AI's draft of the
    # first successful call, kept so a retry never asks again.
    crm_status: Mapped[str | None] = mapped_column(String(20), default=None)
    crm_customer_id: Mapped[UUID | None] = mapped_column(default=None)
    crm_deal_id: Mapped[UUID | None] = mapped_column(default=None)
    crm_deal_url: Mapped[str | None] = mapped_column(Text, default=None)
    crm_error: Mapped[str | None] = mapped_column(Text, default=None)
    crm_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    crm_draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), default=None)

    __table_args__ = (
        Index("ix_companies_created_at", "created_at"),
        CheckConstraint(
            "status IN ('new', 'contacted', 'in_progress', 'closed')", name="ck_companies_status"
        ),
        CheckConstraint("remote IN ('remote', 'hybrid', 'on_site')", name="ck_companies_remote"),
        # Trigram search (REB-285): `first_name`/`contact_person`/`email` are the linked `users`
        # row's own (indexed there); `company_name`, `project` and `requested_role`
        # (REB-380) are this table's. Migration 0013/0016 create the GIN indexes.
        Index(
            "ix_companies_company_name_trgm",
            "company_name",
            postgresql_using="gin",
            postgresql_ops={"company_name": "gin_trgm_ops"},
        ),
        Index(
            "ix_companies_project_trgm",
            "project",
            postgresql_using="gin",
            postgresql_ops={"project": "gin_trgm_ops"},
        ),
        Index(
            "ix_companies_requested_role_trgm",
            "requested_role",
            postgresql_using="gin",
            postgresql_ops={"requested_role": "gin_trgm_ops"},
        ),
        CheckConstraint(
            "onsite_days IS NULL OR "
            f"(onsite_days >= {ONSITE_DAYS_MIN} "
            f"AND onsite_days <= {ONSITE_DAYS_MAX})",
            name="ck_companies_onsite_days_range",
        ),
        CheckConstraint(
            "(remote = 'hybrid') = (onsite_days IS NOT NULL)",
            name="ck_companies_onsite_days_together",
        ),
        CheckConstraint("headcount >= 1", name="ck_companies_headcount_positive"),
        CheckConstraint(
            "crm_status IS NULL OR crm_status IN ('to_link', 'linked', 'error', 'rejected')",
            name="ck_companies_crm_status",
        ),
    )
