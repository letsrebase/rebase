"""The only writer of `work_units` and `approvals` (and, through the trigger,
`work_unit_transitions` -- see `triggers.py`).

This is the "repository layer" spec §5, §12 refer to: the one place that sets
`pigrocrm.actor`/`pigrocrm.motivo` immediately before a `work_units` write, so
`work_unit_log_transition` never falls back to its own system-actor/generic-reason
default for an ordinary application write. A direct `psql` session or a future
importer that skips this still gets a *correct* log -- just an anonymous one, which
is exactly the point of the trigger owning the fallback rather than failing.
"""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.work_units.models import WORK_UNIT_ENTRY_STATI, Approval, WorkUnit
from pigrocrm.core.work_units.repository import (
    ApprovalRepository,
    WorkUnitRepository,
    WorkUnitTransitionRepository,
)
from pigrocrm.core.work_units.schemas import (
    ApprovalCreate,
    ApprovalRead,
    WorkUnitCreate,
    WorkUnitRead,
    WorkUnitTransitionRead,
)

WORK_UNIT_ENTITY = "work_unit"
APPROVAL_ENTITY = "approval"


def actor_to_transition_json(actor: Actor) -> str:
    """`work_unit_transitions.attore`'s JSON shape (spec §5), derived from `Actor`
    (`actor.py:135`) rather than a new authentication concept: mastro's own
    `TransitionActor` names `human`/`agent`/`system`, and PigroCRM's `Actor.type`
    already distinguishes exactly those three cases as `user`/`mcp`/`system`."""
    payload: dict[str, object]
    if actor.type == "system":
        payload = {"kind": "system"}
    else:
        payload = {
            "kind": "human" if actor.type == "user" else "agent",
            "id": str(actor.id) if actor.id is not None else None,
        }
    return json.dumps(payload)


def _set_transition_context(session: Session, actor: Actor, motivo: str) -> None:
    """Session-local Postgres settings (`set_config(..., true)`, scoped to the
    transaction), read by `work_unit_log_transition` immediately after. Local, not
    global: the setting resets at the next commit or rollback, so it can never leak
    into an unrelated request that reuses a pooled connection."""
    session.execute(
        text("SELECT set_config('pigrocrm.actor', :v, true)"),
        {"v": actor_to_transition_json(actor)},
    )
    session.execute(text("SELECT set_config('pigrocrm.motivo', :v, true)"), {"v": motivo})


class WorkUnitService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = WorkUnitRepository(session)
        self.transitions = WorkUnitTransitionRepository(session)

    def get(self, work_unit_id: UUID) -> WorkUnitRead:
        work_unit = self.repo.get(work_unit_id)
        if work_unit is None:
            raise NotFound(WORK_UNIT_ENTITY, work_unit_id)
        return WorkUnitRead.model_validate(work_unit)

    def create(self, data: WorkUnitCreate, actor: Actor, motivo: str = "creazione") -> WorkUnitRead:
        """`WorkUnitCreate.stato`'s own `Literal` admits every state; this is the
        narrower check against `WORK_UNIT_ENTRY_STATI` -- a `ValidationFailed` here
        instead of the trigger's own `IntegrityError`, for the one case a schema can
        catch without a round trip to Postgres. The trigger enforces the identical
        rule regardless (`test_work_units_models.py`), so a caller that reaches this
        method through anything but this service is refused just the same."""
        actor.require_write("create_work_unit")
        if data.stato not in WORK_UNIT_ENTRY_STATI:
            raise ValidationFailed(
                WORK_UNIT_ENTITY,
                "stato",
                f"un work_unit non si crea direttamente in stato '{data.stato}'",
                expected=f"uno tra {sorted(WORK_UNIT_ENTRY_STATI)}",
            )
        _set_transition_context(self.session, actor, motivo)
        work_unit = self.repo.add(
            WorkUnit(
                contract_id=data.contract_id,
                data=data.data,
                quantita=data.quantita,
                descrizione=data.descrizione,
                stato=data.stato,
                approval_id=data.approval_id,
                note=data.note,
            )
        )
        # `work_unit_enforce_state_machine` can silently rewrite `stato` in flight
        # (the redirect/recovery, spec §5) -- `expire_on_commit=False`
        # (`db/session.py`) means the ORM object otherwise keeps showing the value
        # the caller *requested*, not the one the trigger actually wrote. A refresh
        # is what makes the redirect observable to a caller of this service, which
        # is the entire point of the Done-when ("a day ... lands in
        # 'lavorato_senza_approvazione' automatically").
        self.session.refresh(work_unit)
        self.session.commit()
        return WorkUnitRead.model_validate(work_unit)

    def transition(
        self, work_unit_id: UUID, nuovo_stato: str, actor: Actor, motivo: str
    ) -> WorkUnitRead:
        """Moves a day. The database is what actually enforces the edge
        (`work_unit_enforce_state_machine`) -- this is only where the transition
        context is set before the write reaches it."""
        actor.require_write("transition_work_unit")
        work_unit = self.repo.get(work_unit_id)
        if work_unit is None:
            raise NotFound(WORK_UNIT_ENTITY, work_unit_id)
        _set_transition_context(self.session, actor, motivo)
        work_unit.stato = nuovo_stato
        self.session.flush()
        self.session.refresh(work_unit)  # see create()'s own comment
        self.session.commit()
        return WorkUnitRead.model_validate(work_unit)

    def link_approval(
        self, work_unit_id: UUID, approval_id: UUID, actor: Actor, motivo: str
    ) -> WorkUnitRead:
        """Links an approval without naming a target `stato`: on a day sitting in
        `'lavorato_senza_approvazione'` this is exactly what the automatic recovery
        reads (spec §5) -- the trigger itself flips `stato` back to `'lavorato'`."""
        actor.require_write("link_approval")
        work_unit = self.repo.get(work_unit_id)
        if work_unit is None:
            raise NotFound(WORK_UNIT_ENTITY, work_unit_id)
        approval = self.session.get(Approval, approval_id)
        if approval is None:
            raise NotFound(APPROVAL_ENTITY, approval_id)
        _set_transition_context(self.session, actor, motivo)
        work_unit.approval_id = approval_id
        self.session.flush()
        self.session.refresh(work_unit)  # see create()'s own comment
        self.session.commit()
        return WorkUnitRead.model_validate(work_unit)

    def transitions_for(self, work_unit_id: UUID) -> list[WorkUnitTransitionRead]:
        return [
            WorkUnitTransitionRead.model_validate(t)
            for t in self.transitions.for_work_unit(work_unit_id)
        ]


class ApprovalService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ApprovalRepository(session)

    def get(self, approval_id: UUID) -> ApprovalRead:
        approval = self.repo.get(approval_id)
        if approval is None:
            raise NotFound(APPROVAL_ENTITY, approval_id)
        return ApprovalRead.model_validate(approval)

    def create(self, data: ApprovalCreate, actor: Actor) -> ApprovalRead:
        actor.require_write("create_approval")
        approval = self.repo.add(
            Approval(
                contract_id=data.contract_id,
                canale=data.canale,
                mittente=data.mittente,
                ricevuto_il=data.ricevuto_il,
                message_id=data.message_id,
                document_id=data.document_id,
                estratto=data.estratto,
                origine=data.origine,
            )
        )
        self.session.commit()
        return ApprovalRead.model_validate(approval)


__all__ = ["ApprovalService", "WorkUnitService", "actor_to_transition_json"]
