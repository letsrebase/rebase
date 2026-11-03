"""A person's logins and guide downloads, and the counts the admin area shows."""

from datetime import datetime
from uuid import UUID

from rebase_core.schema_common import ResponseModel


class GuideDownloadRead(ResponseModel):
    """One download, with the member's name for the admin's list. `freelancer_id` is the
    live card of that person, for the link to it; `None` for a company contact, who has
    none."""

    id: UUID
    user_id: UUID
    freelancer_id: UUID | None
    nome: str
    last_name: str
    email: str
    downloaded_at: datetime


class LoginRead(ResponseModel):
    """One login, with the member's name for the admin's list (ORB-158), and since
    REB-426 the campaign the login page was opened from, `None` for a login that
    started anywhere else."""

    id: UUID
    user_id: UUID
    nome: str
    last_name: str
    email: str
    logged_at: datetime
    origin: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    utm_id: str | None = None


class LoginStats(ResponseModel):
    """The logins as the admin area reads them (ORB-158), the shape of `GuideStats`:
    `total` every login, `members` the distinct people behind them, `total_members`
    everybody on file, `last_7_days` the last week -- four counters unaffected by
    `q` (REB-313), always read off the whole table. `recent` is the searched, paged
    part: newest first with no term, best-match first once `q` narrows it by name or
    email, no longer capped at 20. `next_cursor` is `None` on the last page, additive
    to the shape `GET /api/hub/logins` already answered."""

    total: int
    members: int
    total_members: int
    last_7_days: int
    recent: list[LoginRead]
    next_cursor: str | None = None


class GuideStats(ResponseModel):
    """The guide's numbers for the admin area (ORB-156). `total` counts every download,
    `members` the distinct people behind them, `total_members` everybody who could have
    (the freelancers on file), `last_7_days` the downloads of the last week, and
    `recent` the latest ones, newest first, with a name each."""

    total: int
    members: int
    total_members: int
    last_7_days: int
    recent: list[GuideDownloadRead]
