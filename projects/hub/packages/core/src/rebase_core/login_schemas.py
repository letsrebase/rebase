"""A person's logins and guide downloads, and the counts the admin area shows."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class GuideDownloadRead(BaseModel):
    """One download, with the member's name for the admin's list."""

    id: UUID
    user_id: UUID
    nome: str
    cognome: str
    email: str
    downloaded_at: datetime


class LoginRead(BaseModel):
    """One login, with the member's name for the admin's list (ORB-158), and since
    REB-426 the campaign the login page was opened from, `None` for a login that
    started anywhere else."""

    id: UUID
    user_id: UUID
    nome: str
    cognome: str
    email: str
    logged_at: datetime
    origine: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    utm_id: str | None = None


class LoginStats(BaseModel):
    """The logins as the admin area reads them (ORB-158), the shape of `GuideStats`:
    `totale` every login, `membri` the distinct people behind them, `membri_totali`
    everybody on file, `ultimi_7_giorni` the last week -- four counters unaffected by
    `q` (REB-313), always read off the whole table. `recenti` is the searched, paged
    part: newest first with no term, best-match first once `q` narrows it by name or
    email, no longer capped at 20. `next_cursor` is `None` on the last page, additive
    to the shape `GET /api/hub/logins` already answered."""

    totale: int
    membri: int
    membri_totali: int
    ultimi_7_giorni: int
    recenti: list[LoginRead]
    next_cursor: str | None = None


class GuideStats(BaseModel):
    """The guide's numbers for the admin area (ORB-156). `totale` counts every download,
    `membri` the distinct people behind them, `membri_totali` everybody who could have
    (the freelancers on file), `ultimi_7_giorni` the downloads of the last week, and
    `recenti` the latest ones, newest first, with a name each."""

    totale: int
    membri: int
    membri_totali: int
    ultimi_7_giorni: int
    recenti: list[GuideDownloadRead]
