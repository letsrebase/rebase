"""The anonymous card (REB-510, spec § 2.1, § 5.1): Claude reads a freelancer's CV once
and writes what a company may know of the profile without a name, and the team
builder's engine reads nothing else about a talent.

`CardWriter` is the engine; its parts are the modules `card_prompt` (what the model is
told), `card_persona` (who the person is, the guards), `card_provenance` (hashes and
staleness), `card_staleness` (the stale list), `card_store` (the save), `card_regen` (the
trail of a regeneration) and `card_types` (the shared words), split out of the one
`cards.py` (REB-892).

One call per CV, never two, but for a card that gives the person's gender away, asked
once more and then failed if it still does (REB-574, REB-802): the card keeps the SHA-256 of
the file it came from, and a failure that is the CV's own -- a refusal, an answer cut at
`max_tokens`, a body that is not the card, a card that names the person or carries an
address, a card that still gives the gender away after its rewrite, a scan with no text
-- keeps the hash of the file that failed, so neither the
same CV nor the same broken one is sent and paid for again until the bytes change, or
until an admin asks with «Rigenera scheda» (`force`). The provider down is not the CV's
fault: the row says so, no hash is kept, and the next write, or the next `rebase
cards-refresh`, asks again.
A failure writes one sentence of ours, never the model's words; it keeps the previous
card when that card is the same CV's, and otherwise retires it, an outage included,
since a card must not outlive the CV it describes: the catalogue would go on showing a
profile the person has replaced until Claude answered again. Nothing here logs more
than the freelancer's id and the kind of failure: not the CV's text, not the card, not
the answer.

The Claude call holds no transaction: the session is committed before it and a new
transaction stores the answer, after re-reading, under the freelancer's row lock, that
the CV the card came from is still the one on file. A CV replaced or cleared while
Claude was writing gets nothing stored for it; `FreelancerService.clear_cv` takes the
same row lock (its `UPDATE`) before it deletes the card, so the two cannot interleave
into a card for a CV that is gone.

The card's `anni` is `None` when the CV dates no role, and its seniority is brought inside
the year bands the prompt states (`card_level.banded`, REB-799). An admin's own seniority
and years sit on `freelancers` (`seniority_admin`, `anni_admin`), so a regenerated or
retired card never erases them, and `read` answers the card with them over it.

Bulk writing (REB-853): `refresh_stale` and `regenerate_stale` write with a pool of
`workers` (`REBASE_CARDS_WORKERS`), each worker on a session of its own and every person
claimed (a Postgres advisory lock per freelancer, taken in `_write`) so no card is paid for
twice; the first outage, spent cap or missing model stops the pool and leaves the rest as
they were. A regeneration above a threshold goes through Anthropic's Message Batches API
instead (`rebase_core.card_batch`): the same requests, the same guards and the same save.

What a person started is traced (REB-861): `regenerate_stale` records a signed start, each card
written or failed in a regeneration (`Regeneration`, a single «Rigenera scheda» too) is an entry
of that talent's trail, and the end is one more of `ai_config`'s, with the cost. The hourly
`refresh_stale` and the wizard's write are started by no person and record nothing.

Who calls it: the wizard and the member's CV upload, after their response, through
`write_after_response` in a session of its own; the admin's two routes; `rebase
cards-refresh`, for the backlog; and `FreelancerService.clear_cv`, whose `delete` runs
in the same transaction as the CV's own removal.
"""

import logging
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select

from rebase_core.ai_config import TASK_OFF
from rebase_core.bands import band_for
from rebase_core.card_draft import CardDraft, clean_persona
from rebase_core.card_guard import KnownIdentifiers, identifying
from rebase_core.card_level import banded, card_read
from rebase_core.card_persona import (
    fill_persona,
    gendered_words,
    known_identifiers,
    persona_differences,
    persona_of,
    stored_persona,
    with_persona,
)
from rebase_core.card_prompt import (
    PROMPT_VERSION,
    PROMPT_VERSIONS,
    card_prompt,
    card_prompt_v1,
    draft_from,
    rewrite_request,
)
from rebase_core.card_provenance import hash_inputs, provenance
from rebase_core.card_regen import REGENERATE_LOCK
from rebase_core.card_staleness import CardStaleness
from rebase_core.card_store import CardStore
from rebase_core.card_types import Composed, Fonte, Outcome, Prepared, Regeneration
from rebase_core.db import SessionOpener, session_factory
from rebase_core.errors import LlmNotConfigured, LlmUnavailable
from rebase_core.freelancers import FreelancerService
from rebase_core.hub_facts import HubFacts, hub_facts
from rebase_core.llm import LlmCall, LlmResponse
from rebase_core.models import Freelancer, FreelancerCard, User
from rebase_core.sources import SourceSet
from rebase_core.team_schemas import (
    CardProvenance,
    CardsRefreshed,
    ConflictRead,
    FreelancerCardAdminRead,
    FreelancerCardRead,
    SourceProvenance,
)

if TYPE_CHECKING:
    from rebase_core.card_batch import CardBatches

logger = logging.getLogger(__name__)


class CardWriter(CardStore, CardStaleness):
    """Writes, reads and regenerates the anonymous cards (the module's docstring). `llm`
    is `None` when no model is configured for the `card` task: then nothing is written."""

    def write(self, freelancer_id: UUID, *, force: bool = False) -> FreelancerCardRead:
        """The card of the freelancer's current CV: written when the CV is new to this
        writer, left as it is when it already produced this card or already failed,
        unless `force` («Rigenera scheda»). No CV: the card goes."""
        self._write(freelancer_id, force=force)
        return self.read(freelancer_id)

    def refresh_stale(self, limit: int = 50) -> CardsRefreshed:
        """Every live freelancer whose CV is neither the card's nor the failed one,
        oldest first, `limit` at a time: the backlog `rebase cards-refresh` works
        through, one batch per run. The CV is compared by the digest the database keeps
        (`freelancers.cv_sha256`), so no CV is hashed and no bytes leave it. Nor is a
        turned-down person's CV sent (`stato = 'scartato'`): the catalogue would never show
        their card (`team_builder.cloud_visible`), so it would be a CV at Anthropic for nothing.

        The batch stops at the first outage (`LlmUnavailable`), counted as not done: a
        run of 429s would otherwise walk the whole batch for nothing, and the CVs after
        it are simply the next run's. A run with nothing written and nothing failed is
        the backlog done. A card still giving the gender away after its rewrite counts as
        failed (REB-802), and its CV is not tried again until it changes; an outage on
        that rewrite is an outage like any other. `stopped` says the batch ended at an
        outage.

        Only a new CV makes a card here. A change of the hub's facts, of the position or of
        a source, and a newer prompt version, only mark the card (`stale_cards`) for an
        admin's start. Reading the sources again is not this pass's: the `cards` loop runs
        `rebase sources-refetch` beside it (`docker-compose.yml`), which needs no model and
        runs with `REBASE_CARDS_LOOP` off."""
        if self.llm is None:
            return CardsRefreshed(written=0, failed=0)
        stale = self.session.scalars(
            select(Freelancer.id)
            .outerjoin(FreelancerCard, FreelancerCard.freelancer_id == Freelancer.id)
            .where(
                Freelancer.deleted_at.is_(None),
                Freelancer.stato != "scartato",
                Freelancer.cv_sha256.is_not(None),
                Freelancer.cv_sha256.is_distinct_from(FreelancerCard.cv_sha256),
                Freelancer.cv_sha256.is_distinct_from(FreelancerCard.error_cv_sha256),
            )
            .order_by(Freelancer.created_at, Freelancer.id)
            .limit(limit)
        ).all()
        return self._write_all(stale, force=False, only_stale=False)

    def _write_all(
        self,
        freelancer_ids: Sequence[UUID],
        *,
        force: bool,
        only_stale: bool,
        outcomes: dict[UUID, Outcome | None] | None = None,
    ) -> CardsRefreshed:
        """`_write` for each person, `workers` at a time. One worker is this writer's own
        session, in order. More are a pool, each on a session and a writer of its own (the
        sources over that session too); the first `unavailable` (an outage, a spent cap, no
        model) stops the pool: the people not yet started are left as they were, and the
        ones already in a call finish and count as they come out. A person is claimed by an
        advisory lock for the whole write (`_write`), so even a list that named someone
        twice pays once. An exception ends the pool the same way and is raised after the
        workers have stopped. `outcomes`, when given, is filled with what became of each
        person, `None` for one never started."""
        if outcomes is not None:
            outcomes.update(dict.fromkeys(freelancer_ids))
        if self.workers == 1 or len(freelancer_ids) < 2:
            written = failed = 0
            for freelancer_id in freelancer_ids:
                outcome = self._write(freelancer_id, force=force, only_stale=only_stale)
                if outcomes is not None:
                    outcomes[freelancer_id] = outcome
                if outcome == "written":
                    written += 1
                elif outcome in ("failed", "unavailable"):
                    failed += 1
                if outcome == "unavailable":
                    return CardsRefreshed(written=written, failed=failed, stopped=True)
            return CardsRefreshed(written=written, failed=failed)
        # No transaction of ours stays open under the workers.
        self.session.commit()
        open_session = session_factory(self.engine())
        stop = threading.Event()

        def work(freelancer_id: UUID) -> Outcome | None:
            if stop.is_set():
                return None
            try:
                with open_session() as session:
                    sources = self._source_service.on(session) if self._source_service else None
                    worker = CardWriter(
                        session, self.llm, now=self.now, sources=sources, run=self.run
                    )
                    outcome = worker._write(freelancer_id, force=force, only_stale=only_stale)
            except BaseException:
                stop.set()
                raise
            if outcome == "unavailable":
                stop.set()
            return outcome

        written = failed = 0
        error: BaseException | None = None
        with ThreadPoolExecutor(
            max_workers=min(self.workers, len(freelancer_ids)), thread_name_prefix="card"
        ) as pool:
            futures = [pool.submit(work, freelancer_id) for freelancer_id in freelancer_ids]
            for freelancer_id, future in zip(freelancer_ids, futures, strict=True):
                try:
                    done = future.result()
                except Exception as exc:
                    stop.set()
                    error = error or exc
                    continue
                if outcomes is not None:
                    outcomes[freelancer_id] = done
                if done == "written":
                    written += 1
                elif done in ("failed", "unavailable"):
                    failed += 1
        if error is not None:
            raise error
        return CardsRefreshed(written=written, failed=failed, stopped=stop.is_set())

    def delete(self, freelancer_id: UUID) -> None:
        """The card goes with the CV. Not committed here: `clear_cv` commits it with the
        CV's own removal, and `write` with its answer. A statement rather than a loaded
        object, so it reads the table when it runs, after the caller's own lock."""
        self.session.execute(
            delete(FreelancerCard).where(FreelancerCard.freelancer_id == freelancer_id)
        )

    def read(self, freelancer_id: UUID) -> FreelancerCardRead:
        row = self._freelancer(freelancer_id)
        stored = self.session.get(FreelancerCard, freelancer_id, populate_existing=True)
        if stored is None:
            return FreelancerCardRead(
                freelancer_id=freelancer_id,
                card=None,
                modalita=row.remoto,
                fascia=band_for(row.tariffa_giornaliera),
                cv_sha256=None,
                model=None,
                generated_at=None,
                error=None,
                seniority_admin=row.seniority_admin,
                anni_admin=row.anni_admin,
            )
        return FreelancerCardRead(
            freelancer_id=freelancer_id,
            card=(
                card_read(stored.card, row.seniority_admin, row.anni_admin)
                if stored.card is not None
                else None
            ),
            modalita=row.remoto,
            fascia=band_for(row.tariffa_giornaliera),
            cv_sha256=stored.cv_sha256,
            model=stored.model,
            generated_at=stored.generated_at,
            error=stored.error,
            seniority_admin=row.seniority_admin,
            anni_admin=row.anni_admin,
        )

    def read_admin(self, freelancer_id: UUID) -> FreelancerCardAdminRead:
        """`read` and the card's provenance: what it was written from, by what, the
        sources stored for the person now and whether the card still matches them. For
        the admin's page only; `provenance` is `None` without a card."""
        base = self.read(freelancer_id)
        stored = self.session.get(FreelancerCard, freelancer_id, populate_existing=True)
        provenance = None
        if stored is not None and stored.card is not None:
            provenance = CardProvenance(
                prompt_version=stored.prompt_version or 1,
                provider=stored.provider,
                fonti=stored.fonti or {},
                conflicts=[ConflictRead.model_validate(c) for c in stored.conflicts or []],
                sources=[
                    SourceProvenance(
                        key=item.key,
                        kind="github" if item.kind == "github" else "link",
                        ref=item.ref,
                        status=item.status,
                        fetched_at=item.fetched_at,
                    )
                    for item in self.source_service().current(freelancer_id).items
                ],
                source_hashes=stored.source_hashes or {},
                stale=self.stale_of(freelancer_id),
            )
        persona = stored_persona(self.session, freelancer_id)
        return FreelancerCardAdminRead.model_validate(
            {
                **base.model_dump(),
                "provenance": provenance,
                "persona": persona,
                "differenze": persona_differences(self.session, persona, freelancer_id),
            }
        )

    def regenerate_stale(self, admin_id: UUID, limit: int, *, fonte: Fonte) -> CardsRefreshed:
        """An admin's start (`AdminAction` `cards_regenerate`, signed, with its `fonte`: the
        web or the command line): the first `limit` of `stale_cards`, each written again
        whatever its CV's hash says. Nothing else
        spends on a version bump or a change of the inputs. A card that is no longer
        stale when its turn comes (another start wrote it first) is skipped, checked under
        the person's claim, so a double click pays once. The batch stops at the first outage
        or spent cap, as `refresh_stale` does, and the rest stay stale. `workers` cards are
        written at once.

        Each card it writes, or fails to, is signed in that talent's trail
        (`card_regenerated`, `Regeneration`), and the end is recorded once, when the pool
        returns (`cards_regenerated`, `end_regeneration`).

        With `batches` and at least its threshold of cards waiting, they are sent instead
        as one Message Batch (`CardBatches.submit`, which records the start with the batch's
        id): `batched` counts them, the batch is carried on by the `cards` service and
        `rebase cards-batch-poll`, which records the end when it is applied, and the cards
        stay stale until their results are applied. A start while a batch is active queues
        nothing."""
        if self.llm is None:
            return CardsRefreshed(written=0, failed=0)
        # One start at a time, for the whole batch: a second click while the first is inside
        # its calls would see the same cards still stale and pay for each of them again.
        # It queues nothing and says so (`regeneration_running`).
        with self.advisory(REGENERATE_LOCK) as started:
            if not started or self.batch_running():
                return CardsRefreshed(written=0, failed=0)
            waiting = self.stale_cards(limit)
            ids = [item.freelancer_id for item in waiting]
            if self.batches is not None and self.batches.wanted(len(ids)):
                return self.batches.submit(self, ids, admin_id, fonte=fonte, limit=limit)
            start = self.record_start(admin_id, fonte, limit, len(ids))
            outcomes: dict[UUID, Outcome | None] = {}
            run = Regeneration(admin_id, fonte, bulk=True, start_action_id=start)
            try:
                with self.acting(run):
                    result = self._write_all(ids, force=True, only_stale=True, outcomes=outcomes)
            except BaseException:
                # A database error leaves the session's transaction aborted: nothing can be
                # read or written on it until it is rolled back, and the end is the record
                # that this run stopped. The original error is what the caller gets.
                self.session.rollback()
                left = sum(1 for outcome in outcomes.values() if outcome is None)
                self.end_regeneration(
                    admin_id=admin_id,
                    fonte=fonte,
                    start_action_id=start,
                    queued=len(ids),
                    stopped=True,
                    stop_reason="error",
                    left=left,
                )
                raise
            self.end_regeneration(
                admin_id=admin_id,
                fonte=fonte,
                start_action_id=start,
                queued=len(ids),
                stopped=result.stopped,
                left=sum(1 for outcome in outcomes.values() if outcome is None),
                known={"written": result.written, "failed": result.failed},
            )
            return result

    # ---- helpers ---------------------------------------------------------------------

    def compose(self, freelancer_id: UUID, prompt_version: int = PROMPT_VERSION) -> Composed:
        """What the model makes of the person's inputs, with nothing stored: the prompt
        (the CV, and the sources and hub facts the person has, as already stored: nothing
        is fetched here), the call, the card and the guards (it names the person, carries
        an address, gives the gender away, names something a source or the CV's employers
        would give away), and for a card that does, the one rewrite. `_write` saves what
        this returns; the evaluation set (`card_eval`) measures it without ever touching
        a live card.

        The session is committed before the call: Claude takes seconds, and no pooled
        connection or lock waits on it. A call the seam refuses or that finds the provider
        down raises `LlmUnavailable` (or `LlmCapReached`, `LlmNotConfigured`), for the
        caller to read, as the first call and the rewrite alike. A failure of the CV or of
        the card is a `Composed` with `failure` set.

        What the model says it used for each field is checked against the sources that
        exist (`verify_fonti`); years claimed on no source that dates a profession are
        dropped and recorded; a hub that shows more real work than the card counts is
        recorded. The employers the model reports are read by the guard and kept, with the
        persona, for the admin's named card (REB-855).

        `prompt_version` 1 is for an evaluation's baseline (REB-842): the CV alone, version
        1's words and guards (the person's name, an address, the gender), the answer a
        bare card, and no persona. Nothing live is ever written under it."""
        if self.llm is None:
            raise LlmNotConfigured(TASK_OFF)
        prepared = self.prepare(freelancer_id, prompt_version)
        if isinstance(prepared, Composed):
            return prepared
        # Claude takes seconds: the transaction ends here, so no pooled connection and
        # no lock waits on it. `_save` opens the next one.
        self.session.commit()
        response = self.llm.complete(prepared.request)
        return self.finish(freelancer_id, prepared, response)

    def prepare(
        self, freelancer_id: UUID, prompt_version: int = PROMPT_VERSION
    ) -> "Prepared | Composed":
        """The request `compose` sends for this person, with what the answer will be read
        against, and nothing sent: the CV's text (or, with none, the `no_text` failure, which
        has no model), the sources and hub facts as stored, the hash of every input. The one
        place a card's prompt is built: `compose` asks the model with it, and a Message Batch
        (`card_batch`) sends the same request and applies the answer through `finish`.
        Reads the database and leaves its transaction open: the caller commits."""
        if prompt_version not in PROMPT_VERSIONS:
            raise ValueError(f"prompt version {prompt_version} cannot be written")
        row = self._freelancer(freelancer_id)
        cv = FreelancerService(self.session).cv_text(freelancer_id)
        if not cv.testo.strip():
            return Composed(None, "no_text", "", "", 0, 0, 0)
        owner = self.session.get(User, row.user_id)
        cognome = owner.cognome if owner is not None else ""
        nome = owner.nome if owner is not None else ""
        if prompt_version == 1:
            sources, facts = SourceSet([]), HubFacts(seniority_admin=None, anni_admin=None)
            known = KnownIdentifiers.empty(cognome, nome)
            request = card_prompt_v1(cv, row.posizione)
        else:
            sources = self.source_service().current(freelancer_id)
            facts = hub_facts(self.session, freelancer_id)
            known = known_identifiers(cognome, nome, row.links, sources, facts)
            request = card_prompt(cv, row.posizione, sources, facts)
        cv_sha256 = self.cv_digest(freelancer_id) or ""
        return Prepared(
            # The person the call is about goes on its usage row, which is how a card's cost
            # is read back (`card_done`); it is never part of what a provider is sent.
            request=request.model_copy(update={"freelancer_id": freelancer_id}),
            known=known,
            sources=sources,
            facts=facts,
            hashes=hash_inputs(row.posizione, sources, facts),
            cv_sha256=cv_sha256,
        )

    def finish(self, freelancer_id: UUID, prepared: Prepared, response: LlmResponse) -> Composed:
        """What the model's first answer to `prepared.request` makes of the card: the draft,
        the guards, and for a card that gives the gender away or names something a source or
        an employer would, the one rewrite, asked synchronously with `llm` whatever way the
        first answer came (the call, or a Message Batch's result). Raises `LlmUnavailable` and
        its kin from the rewrite, for the caller to read: only a rewrite needs the model, so
        a card that passes the guards is finished with none configured."""
        request, known = prepared.request, prepared.known
        sources, facts, hashes = prepared.sources, prepared.facts, prepared.hashes
        first = draft_from(response, known)
        parsed = persona_of(response)
        if not isinstance(first, CardDraft):
            return Composed(
                None,
                first,
                response.model,
                response.provider,
                response.input_tokens,
                response.output_tokens,
                response.thinking_tokens,
                input_hashes=hashes,
                persona=parsed[1] if parsed is not None else None,
            )
        draft = first
        persona = clean_persona(first.persona, first.employers)
        employers = [*known.employers, *first.employers]
        model, provider = response.model, response.provider
        input_tokens = response.input_tokens
        output_tokens = response.output_tokens
        thinking_tokens = response.thinking_tokens
        gendered = gendered_words(draft.card)
        # The anonymous card is read against the account's identifiers and against what the
        # CV says of the person (REB-855); the persona itself is not read by any guard.
        leaks = identifying(
            draft.card, replace(with_persona(known, first, persona), employers=employers)
        )
        if (gendered or leaks) and response.text is not None:
            # One rewrite, then a gate (REB-574, REB-802, REB-839): a card that still
            # gives the gender away, or names an employer, an account, a repository, a
            # site, the person's name or a contact, is failed, never written, with its
            # sentence beside the old card if the CV's own had one. The row keeps the hash
            # of the CV that failed, so the same bytes are not paid for again until an
            # admin asks with «Rigenera scheda». A rewrite that is a refusal, a bad shape
            # or a card that names the person leaves the first card in hand, still at
            # fault. An outage on the rewrite is the provider's, as on the first call: no
            # hash, asked again.
            logger.info(
                "card for freelancer %s asked again (gender: %s, identifiers: %s)",
                freelancer_id,
                bool(gendered),
                bool(leaks),
            )
            if self.llm is None:
                raise LlmNotConfigured(TASK_OFF)
            again = self.llm.complete(
                rewrite_request(request, response.text, gendered=gendered, fields=leaks)
            )
            input_tokens += again.input_tokens
            output_tokens += again.output_tokens
            thinking_tokens += again.thinking_tokens
            rewritten = draft_from(again, known)
            if (again_parsed := persona_of(again)) is not None:
                # What the rewrite read, with what the first answer had where it forgot.
                persona = fill_persona(again_parsed[1], persona)
            if isinstance(rewritten, CardDraft):
                draft, model, provider = rewritten, again.model, again.provider
                # The rewrite may forget an employer the first answer named; the guard
                # reads both.
                employers = list(dict.fromkeys([*employers, *rewritten.employers]))
            if gendered_words(draft.card):
                return Composed(
                    None,
                    "gendered",
                    model,
                    provider,
                    input_tokens,
                    output_tokens,
                    thinking_tokens,
                    input_hashes=hashes,
                    persona=persona,
                )
            if identifying(
                draft.card, replace(with_persona(known, draft, persona), employers=employers)
            ):
                return Composed(
                    None,
                    "source_identifying",
                    model,
                    provider,
                    input_tokens,
                    output_tokens,
                    thinking_tokens,
                    input_hashes=hashes,
                    persona=persona,
                )
        card, fonti, found = provenance(draft, sources, facts)
        return Composed(
            banded(card),
            None,
            model,
            provider,
            input_tokens,
            output_tokens,
            thinking_tokens,
            fonti=fonti,
            conflicts=found,
            input_hashes=hashes,
            persona=persona,
        )

    def _write(self, freelancer_id: UUID, *, force: bool, only_stale: bool = False) -> Outcome:
        digest = self.cv_digest(freelancer_id)
        if digest is None:
            self.delete(freelancer_id)
            self.session.commit()
            return "deleted"
        if self.llm is None:
            return "unchanged"
        stored = self.session.get(FreelancerCard, freelancer_id, populate_existing=True)
        if (
            stored is not None
            and not force
            and digest in (stored.cv_sha256, stored.error_cv_sha256)
        ):
            return "unchanged"
        # One writer per person at a time: a second one (a double click, «Rigenera scheda»
        # while an admin's start is on the same card) would pay for the same card twice.
        # It finds the card is being written and leaves it to the first. `only_stale` (an
        # admin's start) asks again, under the claim, whether the card is still stale: the
        # first writer may have finished between the list and this turn.
        with self.advisory(f"card:{freelancer_id}") as claimed:
            if not claimed:
                return "unchanged"
            if only_stale and self.stale_of(freelancer_id) not in ("prompt", "source"):
                return "unchanged"
            self.begin_card(freelancer_id)
            return self.write_claimed(freelancer_id, digest)

    def write_claimed(self, freelancer_id: UUID, digest: str) -> Outcome:
        # The sources are read again when a card is written (design, «Sources»): a
        # switch that is off reads nothing, and the weekly pass never gets here.
        sources = self.source_service()
        if sources.enabled_kinds():
            sources.refetch(freelancer_id)
        try:
            composed = self.compose(freelancer_id)
        except LlmUnavailable as exc:
            return self.unanswered(freelancer_id, digest, exc)
        return self.store(freelancer_id, digest, composed)


def write_after_response(
    open_session: SessionOpener, llm: LlmCall | None, freelancer_id: UUID
) -> None:
    """The wizard's and the CV upload's background task: returns at once without a key,
    otherwise writes the card in a session of its own (the request's is closed by then),
    and never raises, since nobody is left to read it. The log names the freelancer and
    the exception's class, never its message, which could quote a card or a CV."""
    if llm is None:
        return
    try:
        with open_session() as session:
            CardWriter(session, llm).write(freelancer_id)
    except Exception as error:
        logger.warning(
            "card for freelancer %s not written: %s", freelancer_id, type(error).__name__
        )


def regenerate_after_response(
    open_session: SessionOpener,
    llm: LlmCall | None,
    admin_id: UUID,
    limit: int,
    *,
    fonte: Fonte,
    workers: int = 1,
    batches: "CardBatches | None" = None,
) -> None:
    """An admin's start of the regeneration (`CardWriter.regenerate_stale`), run after the
    response in a session of its own, since it takes as long as `limit` cards do (with
    `workers` at once, or as one Message Batch past `batches`' threshold, which only submits
    here and is carried on by the `cards` service). Returns at once without a model and never
    raises, since nobody is left to read it; the log names the exception's class, never its
    message."""
    if llm is None:
        return
    try:
        with open_session() as session:
            CardWriter(session, llm, workers=workers, batches=batches).regenerate_stale(
                admin_id, limit, fonte=fonte
            )
    except Exception as error:
        logger.warning("regeneration of the stale cards stopped: %s", type(error).__name__)
