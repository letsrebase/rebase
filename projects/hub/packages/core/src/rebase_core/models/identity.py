from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.linkedin import (
    LINKEDIN_URL_MAX_LENGTH,
    PROFILE_SQL_PREDICATE,
    canonical_linkedin,
)

UTM_MAX_LENGTH = 200
UTM_COLUMNS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "utm_id")
NAME_MAX_LENGTH = 120
# A phone number for a company's own referente (REB-380): companies only ask for it
# today, so it stays optional at this level and required only by `CompanyCreate`'s own
# validation -- the same split `Freelancer`'s signup-born columns already keep between
# what the database allows and what a wizard demands.
PHONE_MAX_LENGTH = 40
# Base32-ish, upper-case, unambiguous alphabet (no 0/O/1/I): short enough to sit in a
# URL's path, long enough that guessing another member's code is not a real attack
# (P-REB-44, `rebase_core.referrals`).
REFERRAL_CODE_LENGTH = 10

# Every column added after the production table already existed, with the width each
# one needs. Migration 0001 adopts that table as it stands and adds these with
# `ADD COLUMN IF NOT EXISTS`, which is how the rows PigroCRM's sidecar collected keep
# their place. The UTM six arrived on 2026-09-08, `nome`/`last_name`/`linkedin_url` right
# after; the tuple stays because `test_migrations.py` proves the adoption against a
# table that predates all of them.
LATE_COLUMNS: tuple[tuple[str, int], ...] = (
    *((column, UTM_MAX_LENGTH) for column in UTM_COLUMNS),
    ("nome", NAME_MAX_LENGTH),
    ("last_name", NAME_MAX_LENGTH),
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
    last_name: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    # Optional for everyone, always: a freelance with no LinkedIn is still a freelance.
    linkedin_url: Mapped[str | None] = mapped_column(String(LINKEDIN_URL_MAX_LENGTH), default=None)
    # The referral code the visitor arrived with (`?ref=`, REB-554), kept as typed and
    # unresolved: a signup is not a user, so nothing is credited here. It is read once,
    # when an admin drafts a card from this signup (`FreelancerService.draft_from_signup`),
    # which is where a pending referral is made. Written only when the row is created: a
    # repeat signup for the address never fills or replaces it, because nobody has proved
    # they own that address (REB-646).
    ref: Mapped[str | None] = mapped_column(String(REFERRAL_CODE_LENGTH), default=None)

    @validates("linkedin_url")
    def _canonical_linkedin(self, _key: str, value: str | None) -> str | None:
        return canonical_linkedin(value)

    __table_args__ = (
        Index("uq_orbiters_signups_email_lower", func.lower(email), unique=True),
        # The same fact for the landing's list (REB-800); see `uq_users_linkedin_lower`.
        Index(
            "uq_signups_linkedin_lower",
            func.lower(linkedin_url),
            unique=True,
            postgresql_where=text(PROFILE_SQL_PREDICATE),
        ),
        # Trigram search on the merged talenti list (REB-285): a bare sign-up has no
        # linked `users` row, so its own `nome`/`last_name`/`email` are what `q` matches
        # against. Migration 0013 creates the GIN indexes that serve these.
        Index(
            "ix_signups_name_trgm",
            "nome",
            postgresql_using="gin",
            postgresql_ops={"nome": "gin_trgm_ops"},
        ),
        Index(
            "ix_signups_last_name_trgm",
            "last_name",
            postgresql_using="gin",
            postgresql_ops={"last_name": "gin_trgm_ops"},
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
    the service layer the way `status` and `compilata_da` already are -- two values on a
    handful of rows do not buy a `user_roles` table. `active`, carried over from
    `admin_users.active` by REB-278's migration, still means only "this admin's tokens
    and sessions keep failing the same way": nothing deactivates a member yet, matching
    today. `referral_code` (P-REB-44) is issued lazily, the first time `ReferralService`
    needs one, never at signup: most rows never refer anyone, and a code nobody reads
    is a column with nothing behind it."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    nome: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    last_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    linkedin_url: Mapped[str | None] = mapped_column(String(LINKEDIN_URL_MAX_LENGTH), default=None)
    phone: Mapped[str | None] = mapped_column(String(PHONE_MAX_LENGTH), default=None)
    referral_code: Mapped[str | None] = mapped_column(String(REFERRAL_CODE_LENGTH), default=None)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="member")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Set when an admin merged this row into another person's (REB-800): the row stays,
    # nothing is deleted, and its address still answers: a login link asked for it opens
    # the survivor, and a form that types it lands on the survivor. `NULL` for everyone
    # else. Never a chain: a merge repoints whoever pointed at the row it absorbs.
    merged_into_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)

    @validates("linkedin_url")
    def _canonical_linkedin(self, _key: str, value: str | None) -> str | None:
        return canonical_linkedin(value)

    __table_args__ = (
        Index("uq_users_email_lower", func.lower(email), unique=True),
        # Trigram search (REB-285): `nome`/`last_name`/`email` back both the talenti
        # search (a card's identity lives here since REB-281) and the companies search
        # (a request's referente). Migration 0013 creates the GIN indexes.
        Index(
            "ix_users_name_trgm",
            "nome",
            postgresql_using="gin",
            postgresql_ops={"nome": "gin_trgm_ops"},
        ),
        Index(
            "ix_users_last_name_trgm",
            "last_name",
            postgresql_using="gin",
            postgresql_ops={"last_name": "gin_trgm_ops"},
        ),
        Index(
            "ix_users_email_trgm",
            "email",
            postgresql_using="gin",
            postgresql_ops={"email": "gin_trgm_ops"},
        ),
        Index("uq_users_referral_code", "referral_code", unique=True),
        # One person per LinkedIn profile (REB-800), the same fact `uq_users_email_lower`
        # states for the address, for personal profiles only (`linkedin_key`). Migration 0035
        # creates it only when no two rows share a profile yet; otherwise
        # `rebase talent-duplicates --apply` does, after the merge.
        Index(
            "uq_users_linkedin_lower",
            func.lower(linkedin_url),
            unique=True,
            postgresql_where=text(PROFILE_SQL_PREDICATE),
        ),
    )
