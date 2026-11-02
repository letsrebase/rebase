"""The state every part of the card writer shares (REB-892, split out of `cards.py`).

`CardWriter` is assembled from three parts that each own one job (`RegenerationTrail` in
`card_regen`, `CardStaleness` in `card_staleness`, `CardStore` in `card_store`), and all
three stand on this one: the session, the model seam, the clock, the sources, the pool size,
the Message Batches path and the person who started the write, if one did.
"""

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.card_types import Regeneration
from rebase_core.errors import NotFound
from rebase_core.freelancers import ENTITY
from rebase_core.llm import LlmCall
from rebase_core.models import Freelancer
from rebase_core.sources import SourceService, default_source_service

if TYPE_CHECKING:
    from rebase_core.card_batch import CardBatches


class CardBase:
    """`llm` is `None` when no model is configured for the `card` task
    (`call_for_task`): then nothing is written and every read answers the row as it
    stands. `sources` is what a write refetches the person's sources with (the real
    network unless a test hands another); a compose reads only what is stored."""

    def __init__(
        self,
        session: Session,
        llm: LlmCall | None,
        *,
        now: Callable[[], datetime] = utcnow,
        sources: SourceService | None = None,
        workers: int = 1,
        batches: "CardBatches | None" = None,
        run: Regeneration | None = None,
    ) -> None:
        self.session = session
        self.llm = llm
        self.now = now
        self._source_service = sources
        # `workers` cards at once in `refresh_stale` and `regenerate_stale`; 1 is the
        # caller's own session, one card at a time. `batches` is the Message Batches path
        # of an admin's regeneration, `None` when the provider has none.
        self.workers = max(1, workers)
        self.batches = batches
        # The person who started what this writer writes, if one did (`Regeneration`):
        # `None` for the wizard's write and the hourly `refresh_stale`, which record nothing.
        self.run = run
        self._since: datetime | None = None

    def engine(self) -> Engine:
        """The engine the session is bound to: a worker pool opens sessions of its own on
        it, and an advisory lock is taken on a connection of its own."""
        bind = self.session.get_bind()
        if not isinstance(bind, Engine):
            raise TypeError("the card writer needs a session bound to an engine")
        return bind

    def cv_digest(self, freelancer_id: UUID) -> str | None:
        """The SHA-256 of the person's CV as the database holds it now (`freelancers.cv_sha256`,
        generated from the bytes: nothing is hashed here, and the CV never leaves the
        database to be). `None` without a CV; `NotFound` for a person who is not live."""
        found = self.session.execute(
            select(Freelancer.deleted_at, Freelancer.cv_sha256).where(
                Freelancer.id == freelancer_id
            )
        ).one_or_none()
        if found is None or found[0] is not None:
            raise NotFound(ENTITY, freelancer_id)
        return found[1]

    def _freelancer(self, freelancer_id: UUID) -> Freelancer:
        row = self.session.get(Freelancer, freelancer_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(ENTITY, freelancer_id)
        return row

    def source_service(self) -> SourceService:
        if self._source_service is None:
            self._source_service = default_source_service(self.session)
        return self._source_service
