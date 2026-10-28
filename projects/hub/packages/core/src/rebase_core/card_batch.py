"""A bulk regeneration through the Message Batches API (REB-853): half the price, an answer
within the hour, and a state that outlives the process.

`CardBatches.submit` (an admin's start with at least `threshold` stale cards, or `rebase
cards-regenerate --batch`) builds each person's request with `CardWriter.prepare`, the code
the synchronous write builds it with: same prompt, same answer shape, and, through
`llm.AiBatch`, the same model, effort and output ceiling, less the fallback a batch refuses.
It sends them as one batch whose `custom_id` is the freelancer's id, and writes the batch and
one `card_batch_items` row per card (the hash of the CV and of every input) **before** it
calls the provider, so the batch it is about to pay for is never one the hub has no record of.

`CardBatches.poll` (`rebase cards-batch-poll`, run by the `cards` service every few minutes,
and by hand to resume after a restart: everything it needs is in the tables) asks whether the
batch ended; when it has, it applies each result through the same steps as a synchronous
write: `CardWriter.finish` (the draft, the guards, and the one synchronous rewrite a card that
gives the gender or an identifier away is asked for) and `CardWriter.store` (the one save). A
result is applied only to the card it was asked for: if the CV or any input changed while the
batch ran, the card is left as it is, still stale, and the answer, already paid for, is
recorded and dropped. A refused request is written again as a synchronous call, which has the
server-side fallback a batch has not. A request the provider did not answer (errored, expired,
canceled) leaves its card stale for the next start.

Usage is recorded at the batch price (`ai_usage.batch`), once per card (`usage_recorded`),
when the result is read, so the cap counts it on the day it lands. Hashes only are kept: never
a CV, a request or an answer. One batch is active at a time; `CardWriter.regenerate_stale`
refuses a second under the regeneration's advisory lock.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Literal, NamedTuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.ai_config import BATCH_DISCOUNT
from rebase_core.ai_schemas import CardBatchRead
from rebase_core.audit import utcnow
from rebase_core.card_draft import CardDraft
from rebase_core.cards import PROMPT_VERSION, CardWriter, Composed, Outcome
from rebase_core.errors import LlmUnavailable
from rebase_core.llm import BatchCall, BatchHandle, BatchOutcome, LlmRequest
from rebase_core.models import CARD_BATCH_ACTIVE, CardBatch, CardBatchItem, Freelancer
from rebase_core.team_schemas import CardsRefreshed

logger = logging.getLogger(__name__)

# The advisory lock of a pass that carries the batch on, so the `cards` loop and a hand-run
# `rebase cards-batch-poll` never apply the same result twice.
POLL_LOCK = "card_batch_poll"

# A batch still `submitting` this long after it was written, with no provider id, is one whose
# process died between writing it and sending it: it was never sent, or the provider's answer
# was lost with it. It is marked `failed` (the cards are still stale and an admin may start
# again) rather than sent a second time, which could pay for it twice.
SUBMITTING_GIVEN_UP_AFTER = timedelta(minutes=15)

# A batch the provider has had this long since it was sent is given up on: a batch expires
# after 24 hours, so what is still not applied after two days is one that cannot be (the key
# gone, the results gone, a card that never settles). Its cards that were not applied are
# settled `errored`, still stale, and the next start picks them up; without this exit one
# batch would block every start for ever.
ACTIVE_GIVEN_UP_AFTER = timedelta(days=2)


class BatchPolled(NamedTuple):
    """What one `CardBatches.poll` pass did. `state`: `none` (no batch is active), `running`
    (at the provider), `applied` (every card settled), `applying` (some are left for the next
    pass: a card being written elsewhere, or the model not answering a rewrite), `failed` (it
    was never sent), `unavailable` (the provider could not be asked), `busy` (another pass
    is on it). `written` and `failed` count the cards settled by this pass; `left` the
    ones still waiting."""

    state: Literal["none", "running", "applied", "applying", "failed", "unavailable", "busy"]
    written: int = 0
    failed: int = 0
    left: int = 0


class CardBatches:
    """The Message Batches path of the card writer. `call` is the provider's batch API
    (`llm.batch_for_task`); `threshold` the number of cards from which an admin's start
    uses it. Holds no session: each method works on the writer's."""

    def __init__(
        self, call: BatchCall, threshold: int, *, now: Callable[[], datetime] = utcnow
    ) -> None:
        self.call = call
        self.threshold = max(1, threshold)
        self.now = now

    def wanted(self, count: int) -> bool:
        return count >= self.threshold

    # ---- submit ----------------------------------------------------------------------

    def submit(
        self, writer: CardWriter, ids: Sequence[UUID], admin_id: UUID | None
    ) -> CardsRefreshed:
        """Sends the cards of `ids` that are still stale as one batch, and records it. Called
        under the regeneration's advisory lock, with no batch active. A person whose CV has no
        text is not sent (no model would read it): the synchronous writer fails it with its
        sentence. The provider refusing the submission (a spent cap, no key, an outage) marks
        the batch `failed` and leaves every card stale: `stopped`."""
        session = writer.session
        sources = writer.source_service()
        refetch = bool(sources.enabled_kinds())
        requests: dict[str, LlmRequest] = {}
        items: list[CardBatchItem] = []
        no_text: list[UUID] = []
        for freelancer_id in ids:
            if writer.stale_of(freelancer_id) not in ("prompt", "source"):
                continue
            # As `write_claimed` does: the sources are read again when a card is written.
            if refetch:
                sources.refetch(freelancer_id)
            prepared = writer.prepare(freelancer_id)
            if isinstance(prepared, Composed):
                no_text.append(freelancer_id)
                continue
            requests[str(freelancer_id)] = prepared.request
            items.append(
                CardBatchItem(
                    freelancer_id=freelancer_id,
                    cv_sha256=prepared.cv_sha256,
                    input_hashes=prepared.hashes,
                    prompt_version=PROMPT_VERSION,
                )
            )
        session.commit()
        for freelancer_id in no_text:
            writer.write(freelancer_id, force=True)
        if not items:
            return CardsRefreshed(written=0, failed=0)
        batch = CardBatch(
            provider=self.call.provider,
            status="submitting",
            request_count=len(items),
            started_by=admin_id,
        )
        session.add(batch)
        session.flush()
        for item in items:
            item.batch_id = batch.id
            session.add(item)
        session.commit()
        try:
            cost = writer.estimate_cost(len(items))
            # The estimate is of synchronous calls; the batch is half of it. The spend lands
            # when the results are read, so the cap is asked about now.
            handle = self.call.submit(requests, None if cost is None else cost * BATCH_DISCOUNT)
        except LlmUnavailable as error:
            batch.status = "failed"
            session.commit()
            logger.warning("card batch %s not sent: %s", batch.id, type(error).__name__)
            return CardsRefreshed(written=0, failed=0, stopped=True)
        batch.provider_batch_id = handle.id
        batch.handle = handle.to_json()
        batch.status = "in_progress"
        batch.submitted_at = self.now()
        session.commit()
        logger.info("card batch %s sent: %d cards", batch.id, len(items))
        return CardsRefreshed(written=0, failed=0, batched=len(items))

    # ---- poll ------------------------------------------------------------------------

    def poll(self, writer: CardWriter) -> BatchPolled:
        """One pass over the active batch: asks the provider whether it ended and, once it
        has, applies every result not applied yet, a card at a time, each settled and committed
        as it goes. A restart loses nothing: the next pass reads the batch's state from the
        tables and its results from the provider (kept 29 days), and skips the cards already
        settled and the usage already recorded. The provider not answering the status check or
        the results leaves everything as it was."""
        session = writer.session
        with writer.advisory(POLL_LOCK) as got:
            if not got:
                return BatchPolled("busy")
            batch = session.scalars(
                select(CardBatch)
                .where(CardBatch.status.in_(CARD_BATCH_ACTIVE))
                .order_by(CardBatch.created_at)
                .limit(1)
            ).first()
            if batch is None:
                return BatchPolled("none")
            if batch.status == "submitting":
                return self._given_up(session, batch)
            assert batch.handle is not None
            if batch.submitted_at is not None and (
                self.now() - batch.submitted_at > ACTIVE_GIVEN_UP_AFTER
            ):
                return self._abandon(session, batch)
            handle = BatchHandle.from_json(batch.handle)
            try:
                if batch.status == "in_progress":
                    if not self.call.ended(handle):
                        return BatchPolled("running", left=self._left(session, batch))
                    batch.status = "ended"
                    batch.ended_at = self.now()
                    session.commit()
                outcomes = self.call.collect(handle, CardDraft)
            except LlmUnavailable as error:
                logger.warning("card batch %s not read: %s", batch.id, type(error).__name__)
                return BatchPolled("unavailable", left=self._left(session, batch))
            return self._apply(writer, batch, handle, outcomes)

    def _abandon(self, session: Session, batch: CardBatch) -> BatchPolled:
        left = session.scalars(
            select(CardBatchItem).where(
                CardBatchItem.batch_id == batch.id, CardBatchItem.status == "pending"
            )
        ).all()
        for item in left:
            item.status = "errored"
        batch.status = "failed"
        session.commit()
        logger.error(
            "card batch %s given up after two days: %d cards left stale", batch.id, len(left)
        )
        return BatchPolled("failed", failed=len(left))

    def _given_up(self, session: Session, batch: CardBatch) -> BatchPolled:
        if (
            batch.provider_batch_id is None
            and self.now() - batch.created_at > SUBMITTING_GIVEN_UP_AFTER
        ):
            batch.status = "failed"
            session.commit()
            logger.warning("card batch %s was never sent: marked failed", batch.id)
            return BatchPolled("failed")
        return BatchPolled("running", left=self._left(session, batch))

    def _apply(
        self,
        writer: CardWriter,
        batch: CardBatch,
        handle: BatchHandle,
        outcomes: dict[str, BatchOutcome],
    ) -> BatchPolled:
        session = writer.session
        pending = session.scalars(
            select(CardBatchItem)
            .where(CardBatchItem.batch_id == batch.id, CardBatchItem.status == "pending")
            .order_by(CardBatchItem.freelancer_id)
        ).all()
        written = failed = 0
        for item in pending:
            try:
                outcome = self._apply_item(
                    writer, item, handle, outcomes.get(str(item.freelancer_id))
                )
            except Exception as error:
                # One card that cannot be applied must not hold the rest, nor the batch, for
                # ever: it is settled as errored (its card stays stale for the next start),
                # and the log names the class, never the message.
                session.rollback()
                logger.error(
                    "card batch %s: card %s not applied: %s",
                    batch.id,
                    item.freelancer_id,
                    type(error).__name__,
                )
                outcome = self._settle(session, item, "errored")
            # An item left `unavailable` (a rewrite the model did not answer) waits for the
            # next pass; the others, which need no model, are applied all the same.
            if outcome == "written":
                written += 1
            elif outcome in ("failed", "errored"):
                failed += 1
        left = self._left(session, batch)
        if left == 0:
            batch.status = "applied"
            batch.applied_at = self.now()
            session.commit()
            return BatchPolled("applied", written=written, failed=failed)
        return BatchPolled("applying", written=written, failed=failed, left=left)

    def _apply_item(
        self,
        writer: CardWriter,
        item: CardBatchItem,
        handle: BatchHandle,
        answer: BatchOutcome | None,
    ) -> str:
        """One card's result, through the synchronous write's own steps, under the person's
        claim. Returns the item's new status, or `pending` (left for the next pass: the card
        is being written by someone else) or `unavailable` (the model did not answer the
        rewrite: the card waits for the next pass)."""
        session = writer.session
        freelancer_id = item.freelancer_id
        with writer.advisory(f"card:{freelancer_id}") as claimed:
            if not claimed:
                return "pending"
            answer = answer or BatchOutcome("expired")
            response = answer.response if answer.status == "succeeded" else None
            if response is not None and not item.usage_recorded:
                # Paid for whatever becomes of the card. Recorded before it is applied, in the
                # same transaction as the mark that says so: in the books once, and a
                # restart in between leaves it either both or neither.
                self.call.record(session, handle, response, freelancer_id)
                item.usage_recorded = True
                session.commit()
            if response is None:
                return self._settle(session, item, "errored")
            row = session.get(Freelancer, freelancer_id)
            if row is None or row.deleted_at is not None or row.cv_bytes is None:
                return self._settle(session, item, "unchanged")
            prepared = writer.prepare(freelancer_id)
            session.commit()
            if (
                isinstance(prepared, Composed)
                or prepared.cv_sha256 != item.cv_sha256
                or prepared.hashes != item.input_hashes
                or item.prompt_version != PROMPT_VERSION
            ):
                # What the model read is no longer what the card would be written from, or
                # was asked by a prompt that is not the one running now.
                return self._settle(session, item, "superseded")
            digest = prepared.cv_sha256
            result: Outcome
            if response.stop_reason == "refusal":
                # A batch has no server-side fallback: ask once more the way a write does.
                result = writer.write_claimed(freelancer_id, digest)
            else:
                try:
                    composed = writer.finish(freelancer_id, prepared, response)
                except LlmUnavailable as error:
                    result = writer.unanswered(freelancer_id, digest, error)
                else:
                    result = writer.store(freelancer_id, digest, composed)
            if result == "unavailable":
                return "unavailable"
            status = {
                "written": "written",
                "failed": "failed",
                "superseded": "superseded",
            }.get(result, "unchanged")
            return self._settle(session, item, status)

    @staticmethod
    def _settle(session: Session, item: CardBatchItem, status: str) -> str:
        item.status = status
        session.commit()
        return status

    @staticmethod
    def _left(session: Session, batch: CardBatch) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(CardBatchItem)
                .where(CardBatchItem.batch_id == batch.id, CardBatchItem.status == "pending")
            )
            or 0
        )


def active_batch(session: Session) -> CardBatchRead | None:
    """The regeneration that is on at the provider or being applied, for «Schede da
    aggiornare»: how many cards it holds, when it was sent and how many are settled."""
    batch = session.scalars(
        select(CardBatch)
        .where(CardBatch.status.in_(CARD_BATCH_ACTIVE))
        .order_by(CardBatch.created_at)
        .limit(1)
    ).first()
    if batch is None:
        return None
    settled = session.scalar(
        select(func.count())
        .select_from(CardBatchItem)
        .where(CardBatchItem.batch_id == batch.id, CardBatchItem.status != "pending")
    )
    return CardBatchRead(
        status=batch.status,
        count=batch.request_count,
        done=int(settled or 0),
        submitted_at=batch.submitted_at,
    )
