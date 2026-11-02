"""The gate every team proposal passes before Claude is asked (spec § 5, REB-512, REB-519;
REB-892 moved it here from `routers/team.py`, where the cloud router had to import it).

A proposal is let through by, in this order: the switch (`team_builder_enabled` and a
configured model: off says so, not «Troppe richieste», whatever the day's count); the
asker's own share of the day (public asks only); a slot of the process; and room in the
day (`team_caps.require_daily_room`). The public page and the cloud go through the same
gate and the same slots and cap, so a company cannot run up what a stranger cannot
(REB-519); only the per-client share is the public page's, since the cloud is grant-gated.

**A client's share** (REB-889). The daily cap is one number for everybody, and the speed
bump is five a minute, so one address could spend all 300 in about an hour and close the
builder for the rest of the day. Every public ask is therefore counted against its client
before anything else can refuse it, refused asks included: a client that has used
`team_builder_client_daily_cap` asks in the last 24 hours is refused with the same
«Troppe richieste» sentence without taking a slot or a unit of the cap. The window is a
rolling day, not the Rome day the cap counts: it bounds one client, not the spend. It is
per process like every limiter of this hub (`ratelimit.py`): with several workers a
client's ceiling is the share times the workers, and the cap, counted in the database,
stays exact. Many distinct addresses are the proxy's to stop, not this gate's.

**A slot** is a semaphore of the process (`team_builder_concurrency`): Claude holds a
worker thread for seconds to tens of seconds, on the pool the member area and the
webhooks share, so the one past the cap is refused at once, never queued.

**A refusal is a row** (0028): what was asked is a measure of use whether the hub had room
for it or not (`TeamBuilder.record_refusal`). The write holds no proposal slot, only one
of `REFUSAL_WRITES`, waited for up to `REFUSAL_WAIT_SECONDS` by at most `REFUSAL_WAITERS`
at once (PR #495): under a flood from many clients the rows would be the one thing left
unbounded. Two writes at a time serve hundreds of refusals a second, since each is one
insert; a few waiters keep a burst from losing a row; and the two ceilings together keep a
flood from holding more than ten of the worker threads the member area and the webhooks
share. Past them, the refusal is answered, the row is not written, and the log says so: a
metric lost under a flood is cheaper than a pool held by the flood.
"""

import logging
import threading
from datetime import datetime
from typing import NoReturn
from uuid import UUID

from rebase_core.config import Settings
from rebase_core.errors import TeamBuilderBusy, TeamBuilderOff
from rebase_core.ratelimit import RateLimiter
from rebase_core.team_builder import BUSY_SENTENCE, OFF_SENTENCE, TeamBuilder
from rebase_core.team_caps import require_daily_room
from rebase_core.team_schemas import TeamProposalCreate, TeamProposalRead

logger = logging.getLogger(__name__)

REFUSAL_WRITES = 2
REFUSAL_WAITERS = 8
REFUSAL_WAIT_SECONDS = 0.5
# A client's share counts over a rolling day.
CLIENT_WINDOW_SECONDS = 24 * 60 * 60


class ProposalSlots:
    """The process's slots, client shares and refusal writers for one configuration. The
    API holds one for the life of the process (`proposal_slots`); a test builds its own."""

    def __init__(self, concurrency: int, client_daily_cap: int) -> None:
        self.concurrency = concurrency
        self.client_daily_cap = client_daily_cap
        self._slots = threading.BoundedSemaphore(concurrency)
        self._clients = RateLimiter(client_daily_cap, CLIENT_WINDOW_SECONDS)
        self._refusal_slots = threading.BoundedSemaphore(REFUSAL_WRITES)
        self._refusal_waiters = threading.BoundedSemaphore(REFUSAL_WAITERS)

    def propose(
        self,
        builder: TeamBuilder,
        data: TeamProposalCreate,
        *,
        origine: str,
        user_id: UUID | None,
        company_id: UUID | None = None,
        client: str | None = None,
    ) -> TeamProposalRead:
        """A proposal behind the switch, the client's share, a slot and the day's room, in
        that order. `client` is the asker's key (`rebase_core.ratelimit.client_key`) for a
        public ask and `None` for the cloud's. The builder's clock (`builder.now`) is read
        once: the cap counts the day of that instant and the new row is stamped with it, so
        a call to Claude that ends past midnight in Rome is not counted on one day and
        written on the next (REB-581). `TeamBuilderOff` and `TeamBuilderBusy` are the
        refusals; a busy one has been kept as an attempt row when the row could be
        written."""
        settings = builder.settings
        # Before the caps: an environment with the builder off says so, not «Troppe
        # richieste», whatever the day's count.
        if not settings.team_builder_enabled or builder.llm is None:
            raise TeamBuilderOff(OFF_SENTENCE)
        now = builder.now()
        if client is not None and not self._clients.spend(client):
            logger.info("team builder: a client has used its share of the day")
            self._refuse(builder, data, origine, user_id, company_id, now)
        if not self._slots.acquire(blocking=False):
            logger.info("team builder: every proposal slot is taken")
            self._refuse(builder, data, origine, user_id, company_id, now)
        # One release site for the slot, whatever the count or the call raises: a query
        # that fails here with the slot still held would, four times over, close the
        # builder until the process restarts.
        try:
            try:
                require_daily_room(builder.session, settings, now=now)
            except TeamBuilderBusy:
                pass
            else:
                return builder.propose(
                    data, origine=origine, user_id=user_id, company_id=company_id, now=now
                )
        finally:
            self._slots.release()
        self._refuse(builder, data, origine, user_id, company_id, now)

    def _refuse(
        self,
        builder: TeamBuilder,
        data: TeamProposalCreate,
        origine: str,
        user_id: UUID | None,
        company_id: UUID | None,
        now: datetime,
    ) -> NoReturn:
        """A «Troppe richieste» is kept as an attempt row before it is answered (0028).
        Past either ceiling of the refusal writers the row is skipped, and a database that
        refuses it is logged. Neither is ever a 500 in place of the 503 the visitor gets
        either way."""
        busy = TeamBuilderBusy(BUSY_SENTENCE)
        if not self._refusal_waiters.acquire(blocking=False):
            logger.warning("team builder: refused ask not kept, every refusal write is busy")
            raise busy
        # The waiting place is held only while waiting: a writer gives it back, so the
        # eight places are eight waiters and never six.
        try:
            writing = self._refusal_slots.acquire(timeout=REFUSAL_WAIT_SECONDS)
        finally:
            self._refusal_waiters.release()
        if not writing:
            logger.warning("team builder: refused ask not kept, every refusal write is busy")
            raise busy
        try:
            builder.record_refusal(
                data, origine=origine, user_id=user_id, error=busy, company_id=company_id, now=now
            )
        except Exception:
            logger.exception("team builder: the refused ask was not kept")
        finally:
            self._refusal_slots.release()
        raise busy


# One gate for the process, built when the first proposal arrives and keyed by what it was
# sized by, the way `analytics.build_client` keys its client: the settings never change in
# a running process, and a test that sets other sizes gets a gate of those; a slot held on
# the old one is released into the old one.
_gate: ProposalSlots | None = None
_gate_lock = threading.Lock()


def proposal_slots(settings: Settings) -> ProposalSlots:
    """The process's gate for `settings`."""
    global _gate
    size = (settings.team_builder_concurrency, settings.team_builder_client_daily_cap)
    with _gate_lock:
        if _gate is None or (_gate.concurrency, _gate.client_daily_cap) != size:
            _gate = ProposalSlots(*size)
        return _gate


def reset_proposal_slots() -> None:
    """Forgets the process's gate, every client's share included. For tests, since the
    gate lives as long as the process and one test's asks would be spent out of the next
    test's share."""
    global _gate
    with _gate_lock:
        _gate = None
