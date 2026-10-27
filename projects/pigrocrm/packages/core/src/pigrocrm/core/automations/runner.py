"""The two automations of spec §9, running inside their trigger's transaction.

**Never commits.** `DocumentService.set_offer_state` owns the transaction, and that is the
whole answer to "what happens if an automation fails halfway": there is no halfway. Either
the offer is accepted and the deal is moved, or neither is true.

**Never authorises.** `set_offer_state` already called `actor.require_write`, so a
`readonly` actor never gets here. The runner does not re-check and does not elevate: if it
could elevate, accepting an offer would become a way to write to a deal the actor could not
otherwise touch.

**Never guesses a stage.** By `code`, then by `tipo` where a `tipo` can identify one
(A1 only), then it declines and records why. Never by name -- a name is renamable, and
matching on one is precisely what `pipeline_stages.code` and `tipo` exist to prevent
(slice 1 §5.4, residuo R11).

**Absorbs a declared list and nothing else.** The four `AutomationSkipReason` values are
recorded and the trigger continues. Every other exception propagates and rolls everything
back: a database that cannot write is not an automation that did not fire, and it is the
one case where the user should not see their offer accepted. There is therefore no `except`
anywhere in this file, and `test_no_exception_handler_swallows_anything_in_the_runner`
keeps it that way -- a broad handler here would turn a real failure into a silent
non-execution, which is the exact blur §9.3 refuses.

**A triggered run cannot end silently.** Every path from a recognised transition returns an
`AutomationOutcome`, and that dataclass refuses to exist with neither a move nor a reason:
a fifth, unnamed way of declining is unrepresentable rather than merely untested. The one
thing that ends without an outcome is a transition that was never a trigger, or an offer
with no live deal behind it -- checked before any rule is selected, so no rule ever
"declines" for a reason that is not one of the four.

No execution-log table (§9.4). Idempotence is inherited from three properties already in
the tree: the trigger is unrepeatable by construction (`OFFER_TRANSITIONS` gives
`"accettata": frozenset()`, so `inviata -> accettata` happens at most once per document);
the effect is a state rather than an increment, so a repeat is a recorded no-op; and
atomicity with the trigger means "fired but not recorded" does not exist.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.automations.repository import AutomationConfigRepository
from pigrocrm.core.automations.schemas import (
    KIND_NOT_EXECUTED,
    KIND_STAGE_MOVED,
    AutomationRule,
    AutomationSkipReason,
)
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents.models import Document
from pigrocrm.core.pipeline.repository import PipelineRepository
from pigrocrm.core.pipeline.schemas import PipelineStageRead

_DEAL_ENTITY = "deal"
# A1 fires on this transition and no other; A2 on this one. Declared as data rather than
# as `if` chains so that "which transitions are triggers" is answerable by reading two
# lines, the same shape `OFFER_TRANSITIONS` already uses.
_A1_TRANSITION = ("inviata", "accettata")
_A2_TRANSITION = ("bozza", "inviata")

# The two stage codes the rules resolve. Not names: a name is renamable and `code` exists
# precisely so an automation survives somebody relabelling a column on the board.
_WON_CODE = "vinto"
_OFFER_CODE = "offerta"


@dataclass(frozen=True)
class AutomationOutcome:
    """What one rule did, and -- if it did nothing -- which of the four declared reasons.

    `__post_init__` is the fifth-path guard, and it is here rather than in a test because a
    test can only find the silent skips somebody thought to write a case for. Exactly one
    of "it moved" and "it declined for reason X" must be true: `moved=False, reason=None`
    is the silent non-execution §9.5 exists to prevent, and `moved=True` with a reason is a
    caller who recorded a refusal for a movement that happened. Both raise `ValueError`,
    which is an *undeclared* exception -- so it propagates out of the runner and rolls the
    trigger back rather than reaching a user as a quietly successful acceptance.
    """

    rule: AutomationRule | None
    moved: bool
    reason: AutomationSkipReason | None

    def __post_init__(self) -> None:
        if self.moved == (self.reason is not None):
            raise ValueError(
                "un'automazione o sposta il deal o dichiara il motivo per cui non lo fa: "
                f"moved={self.moved!r}, motivo={self.reason!r}"
            )


class AutomationRunner:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.config = AutomationConfigRepository(session)
        self.deals = DealRepository(session)
        self.deal_service = DealService(session)
        self.pipeline = PipelineRepository(session)
        self.activities = ActivityService(session)

    def on_offer_state_changed(self, document: Document, previous: str, actor: Actor) -> None:
        """Called explicitly by `DocumentService.set_offer_state`, not by a hook.

        Explicitly because an implicit hook on a state change is a mechanism whose call
        sites cannot be found by reading the code, and because §9.3 fixes the order inside
        the trigger: mutate the document, then this, then the document's own activity,
        then commit. That order is not cosmetic -- `ActivityService.record`'s docstring
        requires it to be the last thing touching the session before the caller's commit.
        """
        current = document.stato
        if current is None or document.deal_id is None:
            # Not an offer, or an offer attached to a customer rather than a deal. Nothing
            # was supposed to happen, so nothing is recorded: a `non_eseguita` entry here
            # would fill the log with non-events.
            return

        deal = self.deals.get(document.deal_id)
        if deal is None:
            # The deal was soft-deleted while the offer lived on. Same class of non-event
            # as the branch above, and deliberately *not* one of the four reasons: those
            # describe a rule declining, and here no rule was ever selected. Recording
            # `stage_bersaglio_assente` would name the wrong missing thing -- the target
            # stage is present; the deal is not.
            return

        transition = (previous, current)
        if transition == _A1_TRANSITION:
            outcome = self._apply_a1(document, deal, actor)
        elif transition == _A2_TRANSITION:
            outcome = self._apply_a2(document, deal, actor)
        else:
            return

        if outcome.reason is not None:
            self._record_skip(document, deal.id, outcome, actor)

    # -- the two rules ---------------------------------------------------------

    def _apply_a1(self, document: Document, deal: Deal, actor: Actor) -> AutomationOutcome:
        row = self.config.get_or_create()
        if not row.a1_offerta_accettata_vince_deal:
            return AutomationOutcome("A1", False, "regola_disattivata")

        target, reason = self._resolve_won_stage()
        if target is None:
            # `_resolve_won_stage` returns a reason with every `None`, so this is never a
            # skip without a motive; `AutomationOutcome` would refuse it if it were.
            return AutomationOutcome("A1", False, reason)
        return self._move(document, deal, target, "A1", actor)

    def _apply_a2(self, document: Document, deal: Deal, actor: Actor) -> AutomationOutcome:
        row = self.config.get_or_create()
        if not row.a2_offerta_inviata_avanza_deal:
            return AutomationOutcome("A2", False, "regola_disattivata")

        # By `code` only. There is deliberately no `tipo` fallback: "Offerta" is `open`
        # like every other open stage, so a `tipo` lookup would select an arbitrary one --
        # the asymmetry with A1, where `won` identifies exactly one intended stage.
        stage = self.pipeline.get_by_code(_OFFER_CODE)
        if stage is None:
            return AutomationOutcome("A2", False, "stage_bersaglio_assente")

        current = self._stage_of(deal)

        # Never backwards, and never sideways. A deal already at or past "Offerta" stays
        # put: an automation that retreats a deal because a second offer went out gets
        # switched off on its first day (§9.2). `posizione` and not `probabilita_default`,
        # which two stages may share.
        if current.posizione >= stage.posizione:
            return AutomationOutcome("A2", False, "gia_nello_stato")

        return self._move(document, deal, PipelineStageRead.model_validate(stage), "A2", actor)

    def _resolve_won_stage(
        self,
    ) -> tuple[PipelineStageRead | None, AutomationSkipReason | None]:
        """`code='vinto'`, then the single `tipo='won'`, then decline.

        The `tipo` fallback exists for A1 and not for A2 because `won` identifies exactly
        one intended stage while `open` identifies four. Residuo **R14**: two `won` stages
        are legal, so "there are two" has to be a visible answer -- hence
        `get_by_tipo` returning a list.
        """
        by_code = self.pipeline.get_by_code(_WON_CODE)
        if by_code is not None:
            return PipelineStageRead.model_validate(by_code), None

        candidates = self.pipeline.get_by_tipo("won")
        if not candidates:
            return None, "stage_bersaglio_assente"
        if len(candidates) > 1:
            return None, "stage_bersaglio_ambiguo"
        return PipelineStageRead.model_validate(candidates[0]), None

    def _stage_of(self, deal: Deal) -> PipelineStageRead:
        """The deal's current stage, which is a `NOT NULL` foreign key.

        A missing row here is a dangling FK, not a domain condition: it belongs to the
        "database that cannot answer" family, so it raises rather than becoming a fifth
        skip reason. The raise propagates and rolls the trigger back, which is right --
        the offer should not read as accepted on a database in that state.
        """
        stage = self.pipeline.get(deal.pipeline_stage_id)
        if stage is None:
            raise RuntimeError(
                f"deal {deal.id} punta a uno stato inesistente ({deal.pipeline_stage_id})"
            )
        return PipelineStageRead.model_validate(stage)

    # -- the movement and its two activities -----------------------------------

    def _move(
        self,
        document: Document,
        deal: Deal,
        target: PipelineStageRead,
        rule: AutomationRule,
        actor: Actor,
    ) -> AutomationOutcome:
        if deal.pipeline_stage_id == target.id:
            return AutomationOutcome(rule, False, "gia_nello_stato")

        previous = self._stage_of(deal)

        # The one call in the whole repository to a `*_in_transaction` method, and
        # `packages/core/tests/test_in_transaction_callers.py` is what keeps it the only
        # one. It mutates and does not record, so the single timeline entry below is the
        # single timeline entry for this movement.
        self.deal_service.set_stage_in_transaction(deal, target)

        # `Actor.system()` carries `id=None`, so `actor_id` is NULL and the triggering
        # human lives in `attivata_da`: the timeline has to say both "the system did it"
        # and "because you accepted that offer" (§9.5).
        self.activities.record(
            _DEAL_ENTITY,
            deal.id,
            KIND_STAGE_MOVED,
            Actor.system(),
            {
                "regola": rule,
                "documento_id": str(document.id),
                "da": previous.nome,
                "a": target.nome,
                "attivata_da": str(actor.id) if actor.id is not None else None,
                "attivata_via": actor.type,
            },
        )
        return AutomationOutcome(rule, True, None)

    def _record_skip(
        self, document: Document, deal_id: UUID, outcome: AutomationOutcome, actor: Actor
    ) -> None:
        """§9.5's fourth surface, and the one that is usually missing.

        Without it, "it did not fire" and "it was not supposed to fire" are the same empty
        screen. The signal "offerta accettata, deal non vinto" on the commercial dashboard
        (§6.2) is this entry's permanent cross-check: if the automation goes quiet, the
        count speaks.
        """
        self.activities.record(
            _DEAL_ENTITY,
            deal_id,
            KIND_NOT_EXECUTED,
            Actor.system(),
            {
                "regola": outcome.rule,
                "motivo": outcome.reason,
                "documento_id": str(document.id),
                "attivata_da": str(actor.id) if actor.id is not None else None,
                "attivata_via": actor.type,
            },
        )
