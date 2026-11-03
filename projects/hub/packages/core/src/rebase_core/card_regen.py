"""The trail of an admin's regeneration and the locks around it (REB-861, REB-892: split out
of `cards.py`): the signed start, each card written or failed, the end with its counts and
cost, and the advisory locks that keep one start and one writer per person.

`RegenerationTrail` is the part of `CardWriter` that does this; it reads and writes only
`ai_config`'s and the talent's trail (`AdminAction`) and the calls' usage rows.
"""

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, NotRequired, TypedDict
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select, text

from rebase_core.ai_config import AI_CONFIG_ENTITY, AI_CONFIG_ENTITY_ID
from rebase_core.audit import AdminActionService
from rebase_core.card_base import CardBase
from rebase_core.card_prompt import PROMPT_VERSION
from rebase_core.card_types import FAILURES, Regeneration, Source
from rebase_core.freelancers import ENTITY
from rebase_core.models import CARD_BATCH_ACTIVE, AdminAction, AiUsage, CardBatch

# The advisory lock of an admin's start of the regeneration (`CardWriter.regenerate_stale`).
REGENERATE_LOCK = "regenerate_stale"

# The trail of a regeneration (REB-861): its start and its end on `ai_config`, and each card
# on the freelancer.
CARDS_REGENERATE = "cards_regenerate"
CARDS_REGENERATED = "cards_regenerated"
CARD_REGENERATED = "card_regenerated"

# The reasons a regeneration did not write a card that are not one of `Failure`: the answer
# was dropped because the CV or an input changed meanwhile (`superseded`), the provider never
# answered it or applying it raised (`errored`), the batch was given up after two days
# (`expired`) or never sent (`not_sent`), the call was refused for the daily cap
# (`cap_reached`) or no model was configured (`not_configured`), the person or the CV
# was gone when the answer came (`unchanged`, recorded only when the answer had been paid for).
NO_ANSWER_REASONS = ("errored", "expired", "not_sent")

# Why a regeneration can stop early (`cards_regenerated.stop_reason`): the provider did not
# answer, the daily cap was reached, no model is configured, or something else raised.
STOP_REASONS = ("unavailable", "cap_reached", "not_configured", "error")

# The counts of a regeneration's end (`cards_regenerated`), `null` when its start was lost.
_RUN_COUNTS = ("written", "failed", "superseded", "skipped", "errored")

# Every `reason` a `card_regenerated` entry can carry: the web's «Registro» must have a word for
# each (`REGENERATION_REASON_LABELS`, held to this list by `test_card_regeneration_trail`).
REGENERATION_REASONS = (
    *FAILURES,
    "cap_reached",
    "not_configured",
    "superseded",
    "unchanged",
    *NO_ANSWER_REASONS,
)


class RegenerationStart(TypedDict):
    """The payload of `cards_regenerate`: the signed start."""

    limit: int
    queued: int
    source: Source
    batch: bool
    batch_id: NotRequired[UUID]


class CardTrailEntry(TypedDict):
    """The payload of `card_regenerated`: what became of one card of a regeneration. Ours,
    never the model's words and no card text."""

    source: Source
    bulk: bool
    prompt_version: int
    model: str | None
    outcome: str
    cost_eur: Decimal
    start_action_id: NotRequired[UUID | None]
    reason: NotRequired[str]
    unpriced: NotRequired[int]


class RegenerationTrail(CardBase):
    """The trail of a regeneration, the locks and the batch check of `CardWriter`."""

    # ---- the trail of a regeneration (REB-861) ---------------------------------------

    @contextmanager
    def acting(self, run: Regeneration | None) -> Iterator[None]:
        """The writes of this block are `run`'s: signed in each talent's trail."""
        before, self.run = self.run, run
        try:
            yield
        finally:
            self.run = before

    def record_start(
        self, admin_id: UUID, source: Source, limit: int, queued: int, batch_id: UUID | None = None
    ) -> UUID | None:
        """The signed start of a regeneration, on `ai_config`: who asked (`source`: the web or
        the command line), how many it queued of the `limit` asked, and whether and as which
        Message Batch it was sent. Answers the entry's id, which each card's entry and the end
        point back to."""
        details: RegenerationStart = {
            "limit": limit,
            "queued": queued,
            "source": source,
            "batch": batch_id is not None,
        }
        if batch_id is not None:
            details["batch_id"] = batch_id
        return AdminActionService(self.session).record(
            AI_CONFIG_ENTITY, AI_CONFIG_ENTITY_ID, CARDS_REGENERATE, admin_id, details
        )

    def begin_card(self, freelancer_id: UUID) -> None:
        """A card's write starts, under the person's claim: from now on the calls recorded
        for the person are this write's (`card_done` reads them), and only they. Does nothing
        without a `run`."""
        self._since = None
        if self.run is not None:
            self._since = self.session.scalar(select(func.clock_timestamp()))

    def card_done(
        self,
        freelancer_id: UUID,
        outcome: str,
        *,
        reason: str | None = None,
        model: str | None = None,
    ) -> None:
        """What became of the card a person asked to have written, signed in the talent's
        trail (`card_regenerated`): written (`scritta`) or not (`non_scritta`, with the
        reason: a `Failure`, `superseded` or one of `NO_ANSWER_REASONS`), by which door, the
        model, the prompt version and what the calls cost (the `ai_usage` rows of the person
        since the write began). No personal data and no card text: ours, never the model's
        words. Does nothing without a `run`, for an outcome that is not one (`unchanged`
        with nothing spent) and for an `unavailable` card the run tries again."""
        run, begun, self._since = self.run, self._since, None
        if run is None:
            return
        if (begun is None and run.batch_since is None) or (
            outcome == "unavailable" and run.retries
        ):
            return
        cost, unpriced, calls = self._usage_since(freelancer_id, begun, run.batch_since)
        if outcome in ("unchanged", "deleted") and calls == 0:
            return
        written = outcome == "written"
        payload: CardTrailEntry = {
            "source": run.source,
            "bulk": run.bulk,
            "prompt_version": PROMPT_VERSION,
            "model": model or None,
            "outcome": "scritta" if written else "non_scritta",
            "cost_eur": cost,
        }
        if run.bulk:
            payload["start_action_id"] = run.start_action_id
        if not written:
            payload["reason"] = "superseded" if outcome == "superseded" else reason or outcome
        if unpriced:
            payload["unpriced"] = unpriced
        AdminActionService(self.session).record(
            ENTITY, freelancer_id, CARD_REGENERATED, run.admin_id, payload
        )

    def _usage_since(
        self, freelancer_id: UUID, since: datetime | None, batch_since: datetime | None
    ) -> tuple[Decimal, int, int]:
        """What the person's `card` calls of this write cost: the sum of the priced ones, how
        many had no price, how many there were. A write's own calls are the ones recorded since
        its claim began (`since`): the claim is per person, so no other write of the person
        is in that window. A Message Batch's answer is recorded in the pass that applies the
        card, in a transaction that may have begun before the claim, and an earlier pass may
        have recorded it, so the batch-priced rows (`ai_usage.batch`) count from the day it
        was sent (`batch_since`; one batch is active at a time, so they are its own). Any
        other synchronous call of the person in that window, the wizard's, the hourly loop's or
        a «Rigenera scheda», is not this write's. An evaluation's and a «Prova»'s never are."""
        own = [
            AiUsage.at >= since if since is not None else false(),
            and_(AiUsage.batch.is_(True), AiUsage.at >= batch_since)
            if batch_since is not None
            else false(),
        ]
        rows = self.session.execute(
            select(AiUsage.cost_eur, AiUsage.priced).where(
                AiUsage.task == "card",
                AiUsage.purpose == "run",
                AiUsage.freelancer_id == freelancer_id,
                or_(*own),
            )
        ).all()
        cost = sum((Decimal(c) for c, _ in rows if c is not None), Decimal(0))
        return cost, sum(1 for _, priced in rows if not priced), len(rows)

    def end_regeneration(
        self,
        *,
        admin_id: UUID,
        source: Source,
        start_action_id: UUID | None,
        queued: int,
        batch_id: UUID | None = None,
        batch_status: str | None = None,
        stopped: bool = False,
        left: int = 0,
        stop_reason: str | None = None,
        known: Mapping[str, int] | None = None,
    ) -> None:
        """The end of a bulk regeneration, on `ai_config`, signed by the admin who started
        it (`cards_regenerated`): what became of the `queued` cards, counted from each card's
        own entry (the latest one per person): `written`, `failed`, `superseded` (the answer
        was dropped, the CV or an input having changed), `errored` (no answer from the
        provider, or one that could not be applied), `skipped` (no longer stale, or a
        person or CV gone), the cost of the calls (`cost_eur`, the sum of the priced ones,
        `unpriced` how many had no price) and `failures`, each person's id and the reason, with
        no name and no card text. `stopped` says the run ended early, `stop_reason` why when it
        is one of `STOP_REASONS`, `left` how many people it never started. Written once: when
        the pool returns, when a batch is applied, or when one is given up.

        With no `start_action_id` (the start's entry was lost, the trail being best-effort) no
        card's entry can be found, so the counts and the cost are not known: they are `null`,
        with `start_missing` true, except what the caller counted itself (`known`, by the names
        of `_RUN_COUNTS`, such as the worker pool's written and failed)."""
        cards: dict[UUID, dict[str, Any]] = {}
        if start_action_id is not None:
            rows = self.session.execute(
                select(AdminAction.entity_id, AdminAction.payload)
                .where(
                    AdminAction.kind == CARD_REGENERATED,
                    AdminAction.payload["start_action_id"].astext == str(start_action_id),
                )
                .order_by(AdminAction.created_at, AdminAction.id)
            ).all()
            for freelancer_id, payload in rows:
                cards[freelancer_id] = payload
        written = failed = superseded = errored = unpriced = 0
        cost = Decimal(0)
        failures: list[dict[str, str]] = []
        for freelancer_id, payload in cards.items():
            cost += Decimal(str(payload.get("cost_eur") or 0))
            unpriced += int(payload.get("unpriced") or 0)
            if payload.get("outcome") == "scritta":
                written += 1
                continue
            reason = str(payload.get("reason"))
            if reason == "unchanged":
                continue
            if reason == "superseded":
                superseded += 1
            elif reason in NO_ANSWER_REASONS:
                errored += 1
            else:
                failed += 1
            failures.append({"freelancer_id": str(freelancer_id), "reason": reason})
        details: dict[str, object] = {
            "source": source,
            "start_action_id": start_action_id,
            "written": written,
            "failed": failed,
            "superseded": superseded,
            "skipped": max(0, queued - written - failed - superseded - errored - left),
            "errored": errored,
            "cost_eur": cost,
            "failures": failures,
        }
        if start_action_id is None:
            # The start's entry was lost (the trail is best-effort), so no card's entry points at
            # it and nothing above is known. Say so rather than report zeros: only what the
            # caller counted itself is kept, the rest is `null`.
            counted = known or {}
            details.update(
                {key: counted.get(key) for key in _RUN_COUNTS},
                cost_eur=None,
                failures=None,
                start_missing=True,
            )
        if batch_id is not None:
            details["batch_id"] = batch_id
            details["batch_status"] = batch_status
        if unpriced:
            details["unpriced"] = unpriced
        if stopped:
            details["stopped"] = True
            details["left"] = left
            # Why, when it is known: the call that stopped the pool says so in its own entry
            # (an outage, the daily cap, no model), a batch that was refused in the exception
            # it met, and anything else that ended the run early is `error`.
            cause = stop_reason or next(
                (f["reason"] for f in failures if f["reason"] in STOP_REASONS), None
            )
            if cause is not None:
                details["stop_reason"] = cause
        AdminActionService(self.session).record(
            AI_CONFIG_ENTITY, AI_CONFIG_ENTITY_ID, CARDS_REGENERATED, admin_id, details
        )

    def regeneration_running(self) -> bool:
        """Whether an admin's start is inside its batch right now, in any process, or a
        Message Batch of cards is still running or being applied."""
        with self.advisory(REGENERATE_LOCK) as free:
            return not free or self.batch_running()

    def batch_running(self) -> bool:
        """Whether a regeneration sent through the Batch API is not yet applied."""
        return (
            self.session.scalar(
                select(CardBatch.id).where(CardBatch.status.in_(CARD_BATCH_ACTIVE)).limit(1)
            )
            is not None
        )

    @contextmanager
    def advisory(self, name: str) -> Iterator[bool]:
        """A Postgres advisory lock named `name`, taken without waiting and held until the
        block ends, whether it was got (`True`) or not. Held on a connection of its own:
        the session commits and gives its connection back many times in a write, and a
        session-level lock would stay behind on whichever one was returned to the pool."""
        key = int.from_bytes(
            hashlib.sha256(f"rebase:{name}".encode()).digest()[:8], "big", signed=True
        )
        with self.engine().connect() as conn:
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
            try:
                yield got
            finally:
                if got:
                    conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()
