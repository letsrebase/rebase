"""What the CRM says to the hub about engagements, and what it asks it (collaborators design § 5,
REB-1106).

Two calls, one token (`PIGROCRM_HUB_NOTICES_TOKEN`, the hub's `REBASE_PIGRO_NOTICES_TOKEN`; never
the engagements token, which opens the CRM, and never the registry token):

- `POST {hub_url}/api/hub/engagement-notices`, one outbox row at a time (`HubNoticeSweep`). It is
  idempotent: the hub ignores a notice whose sequence is not newer than the one it holds, so a
  retry after a lost answer, or two sweeps at once, change nothing.
- `POST {hub_url}/api/hub/referrer-name`, asked by the letters for `{{segnalato_da}}` and
  `{{azienda_segnalata_da}}` (`HubLink.referrer_names`): the hub answers a name only for a verified
  referral, and any failure is «nobody».

The bearer travels only over HTTPS, plain HTTP only to this machine (`speaks_https`, the hub's
own rule in `pigro_door.py`), and a redirect is never followed (`tenants.hub.urllib_call`).
**Without the token, or with a URL the bearer may not travel to, the feature is inert**: the sweep
leaves its rows for the day the setting exists and the letters print «nessuno». No database lock is
held across a network call: the sweep reads what is due, lets the transaction go, calls the hub, and
writes each answer in a transaction of its own.

A 2xx marks the row `delivered`. A wrong token (401, 403), a hub that does not have the route yet
(404), a timeout or rate limit (408, 429) and any 5xx or failure to connect are retried with a
backoff (`retry_delay`); any other 4xx is an answer the hub will give again however often it is
asked (it refused the data), so the row is `rejected`, stays in the table for a person to read and
is reported. A row that fails holds back the later rows of the same engagement for this pass, so
they go in sequence order.
"""

import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPException as HttpProtocolError
from typing import Any, NamedTuple
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.models import EngagementNotice
from pigrocrm.core.config import Settings
from pigrocrm.core.tenants.hub import MAX_BODY_BYTES, USER_AGENT, HttpCall, urllib_call

_log = logging.getLogger(__name__)

NOTICES_PATH = "/api/hub/engagement-notices"
REFERRER_PATH = "/api/hub/referrer-name"
# Where plain HTTP is still acceptable: a hub running on this machine, in development.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
# Statuses that say «not now» or «the setting is not there yet», and so are asked again.
RETRY_STATUSES = frozenset({401, 403, 404, 408, 409, 423, 425, 429})
# The first retry waits five minutes and each next one twice as long, up to six hours.
RETRY_BASE = dt.timedelta(minutes=5)
RETRY_CAP = dt.timedelta(hours=6)
# Rows delivered in one pass: a backlog after a long outage is worked off over several.
SWEEP_LIMIT = 100


def speaks_https(url: str) -> bool:
    """Whether the bearer may travel to `url`: HTTPS, or plain HTTP to this machine."""
    parts = urlsplit(url)
    if parts.scheme == "https":
        return bool(parts.hostname)
    return parts.scheme == "http" and parts.hostname in LOCAL_HOSTS


def retry_delay(attempts: int) -> dt.timedelta:
    """How long after the `attempts`-th failed try the next one waits."""
    return min(RETRY_BASE * (1 << max(attempts - 1, 0)), RETRY_CAP)


class ReferrerAnswer(BaseModel):
    """The hub's answer to `referrer-name`: each side's referrer, `None` for a side that has none
    or whose referral is not verified. Extra keys are ignored so the hub may grow its answer."""

    model_config = ConfigDict(extra="ignore")

    freelancer: str | None = None
    company: str | None = None


class DueNotice(NamedTuple):
    id: UUID
    engagement_id: UUID
    sequence: int
    kind: str
    payload: dict[str, Any]
    created_at: dt.datetime


@dataclass(frozen=True)
class Answer:
    """What one call to the hub came to: its status (`None` when no answer came) and the cause of
    a failure, a status or a word, never a body or a header."""

    status: int | None
    body: bytes = b""
    error: str | None = None

    @property
    def delivered(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


class HubLink:
    """The CRM's side of the two hub routes."""

    def __init__(self, settings: Settings, http: HttpCall | None = None) -> None:
        self.settings = settings
        self.http: HttpCall = http or urllib_call

    @property
    def base(self) -> str:
        return self.settings.hub_url.strip().rstrip("/")

    @property
    def configured(self) -> bool:
        """The token is set and the bearer may travel to the hub's URL."""
        return bool(self.settings.hub_notices_token) and speaks_https(self.base)

    def post(self, path: str, body: dict[str, Any]) -> Answer:
        """One call. Never raises for a network or protocol failure: that is an `Answer` with no
        status, which a caller retries. Without `configured`, nothing is sent."""
        if not self.configured:
            return Answer(None, error="not configured")
        headers = {
            "Authorization": f"Bearer {self.settings.hub_notices_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        try:
            status, raw = self.http(
                "POST", f"{self.base}{path}", headers, json.dumps(body).encode("utf-8")
            )
        except (OSError, HttpProtocolError, ValueError) as exc:
            return Answer(None, error=type(exc).__name__)
        return Answer(
            status, raw[: MAX_BODY_BYTES + 1], None if 200 <= status < 300 else f"HTTP {status}"
        )

    def referrer_names(
        self, hub_freelancer_id: UUID | None, hub_company_id: UUID | None
    ) -> ReferrerAnswer:
        """The referrer of the freelancer and of the company, as the hub names them, for a verified
        referral only. Any failure, an unset token included, is «nobody» on both sides."""
        if hub_freelancer_id is None and hub_company_id is None:
            return ReferrerAnswer()
        answer = self.post(
            REFERRER_PATH,
            {
                "hub_freelancer_id": str(hub_freelancer_id) if hub_freelancer_id else None,
                "hub_company_id": str(hub_company_id) if hub_company_id else None,
            },
        )
        if not answer.delivered or len(answer.body) > MAX_BODY_BYTES:
            return ReferrerAnswer()
        try:
            return ReferrerAnswer.model_validate(json.loads(answer.body))
        except (ValueError, ValidationError):
            return ReferrerAnswer()


@dataclass(frozen=True)
class NoticeSweepReport:
    """One pass: rows delivered, rows left to try again, rows the hub refused for good, and, when
    the feature is inert, how many rows are waiting for the setting."""

    delivered: int = 0
    retrying: int = 0
    rejected: int = 0
    waiting: int = 0


class HubNoticeSweep:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        http: HttpCall | None = None,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
        limit: int = SWEEP_LIMIT,
    ) -> None:
        self.session = session
        self.link = HubLink(settings, http)
        self.now = now
        self.limit = limit

    def due(self) -> list[DueNotice]:
        """The pending rows whose turn has come, an engagement's in sequence order."""
        rows = self.session.execute(
            select(
                EngagementNotice.id,
                EngagementNotice.engagement_id,
                EngagementNotice.sequence,
                EngagementNotice.kind,
                EngagementNotice.payload,
                EngagementNotice.created_at,
            )
            .where(
                EngagementNotice.state == "pending", EngagementNotice.next_attempt_at <= self.now()
            )
            .order_by(EngagementNotice.engagement_id, EngagementNotice.sequence)
            .limit(self.limit)
        ).all()
        return [DueNotice(*row) for row in rows]

    def pending(self) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(EngagementNotice)
                .where(EngagementNotice.state == "pending")
            )
            or 0
        )

    def run(self, *, dry_run: bool = False) -> NoticeSweepReport:
        """Delivers what is due. A rehearsal only counts it."""
        if dry_run:
            count = len(self.due())
            self.session.rollback()
            return NoticeSweepReport(waiting=count)
        if not self.link.configured:
            waiting = self.pending()
            self.session.rollback()
            if waiting:
                _log.warning("%d engagement notices wait for PIGROCRM_HUB_NOTICES_TOKEN", waiting)
            return NoticeSweepReport(waiting=waiting)
        rows = self.due()
        # Nothing is held while the hub is called.
        self.session.rollback()
        delivered = retrying = rejected = 0
        held_back: set[UUID] = set()
        for notice_id, engagement_id, sequence, kind, payload, created_at in rows:
            if engagement_id in held_back:
                continue
            answer = self.link.post(
                NOTICES_PATH,
                {
                    "sequence": sequence,
                    "kind": kind,
                    "occurred_at": created_at.isoformat(),
                    **payload,
                },
            )
            outcome = self._record(notice_id, answer)
            if outcome == "delivered":
                delivered += 1
            elif outcome == "rejected":
                rejected += 1
            else:
                retrying += 1
                held_back.add(engagement_id)
        return NoticeSweepReport(delivered, retrying, rejected)

    def _record(self, notice_id: UUID, answer: Answer) -> str:
        """Writes one answer on its row, in a transaction of its own."""
        row = self.session.get(
            EngagementNotice, notice_id, populate_existing=True, with_for_update=True
        )
        if row is None or row.state != "pending":
            self.session.rollback()
            return "delivered" if row is not None and row.state == "delivered" else "retry"
        now = self.now()
        row.attempts += 1
        row.last_attempt_at = now
        if answer.delivered:
            row.state, row.delivered_at, row.last_error = "delivered", now, None
            outcome = "delivered"
        elif (
            answer.status is not None
            and 400 <= answer.status < 500
            and answer.status not in RETRY_STATUSES
        ):
            row.state, row.last_error = "rejected", answer.error
            outcome = "rejected"
        else:
            row.last_error = answer.error
            row.next_attempt_at = now + retry_delay(row.attempts)
            outcome = "retry"
        self.session.commit()
        return outcome
