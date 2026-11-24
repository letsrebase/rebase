from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin

# ---- a freelancer's GitHub account, verified by signing in there (REB-1166, REB-1167) ----

# What the person let the hub read when they linked the account: `public` (no scope, public
# activity only) or `private` (`read:user`, so private contributions are in the monthly totals,
# never with a repository's name). The person's choice, asked at every link.
GITHUB_SCOPES = ("public", "private")
# GitHub's own limit on a login.
GITHUB_LOGIN_MAX_LENGTH = 39
# How a technology of the profile's usage stands against the account's public repositories
# (REB-1169): among their languages, not among them, or not a language GitHub can show (a
# framework, a service).
GITHUB_TECH_STATUSES = ("seen", "not_seen", "not_visible")


class GithubAccount(Base, PrimaryKeyMixin):
    """The GitHub account a freelancer proved is theirs by signing in to it from the member area,
    and what the hub read of it at that moment. One per freelancer
    (`uq_github_accounts_freelancer_id`) and one freelancer per account
    (`uq_github_accounts_github_id`), so nobody can claim an account somebody else linked.

    `github_id` is GitHub's numeric id, which survives a rename; `login` is the name at the time
    of the link. The token is never stored: `activity` and `languages` are a snapshot taken during
    the link, and refreshing them means linking again (Lorenzo, 2026-11-24).

    `activity` holds one entry per calendar month of the 24 before the link, oldest first:
    `{"month": "YYYY-MM", "commits", "pull_requests", "reviews", "issues"}`. Commits are dated by
    their author, so they can be backdated; the other three are dated by GitHub. `languages` are
    the technology labels the person's own public repositories show, mapped to the fork's labels.
    Counts and labels only: no repository name is read into either."""

    __tablename__ = "github_accounts"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), nullable=False
    )
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    login: Mapped[str] = mapped_column(String(GITHUB_LOGIN_MAX_LENGTH), nullable=False)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activity: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    languages: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        CheckConstraint("scope IN ('public', 'private')", name="ck_github_accounts_scope"),
        Index("uq_github_accounts_freelancer_id", "freelancer_id", unique=True),
        Index("uq_github_accounts_github_id", "github_id", unique=True),
    )
