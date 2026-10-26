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
from datetime import date
from typing import get_args
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.work_units.models import (
    WORK_UNIT_ENTRY_STATI,
    WORK_UNIT_TRANSITIONS,
    Approval,
    WorkUnit,
)
from pigrocrm.core.work_units.repository import (
    ApprovalRepository,
    WorkUnitRepository,
    WorkUnitTransitionRepository,
)
from pigrocrm.core.work_units.schemas import (
    ApprovalCreate,
    ApprovalDetail,
    ApprovalRead,
    TermViolation,
    WorkUnitCreate,
    WorkUnitDetail,
    WorkUnitList,
    WorkUnitListQuery,
    WorkUnitRead,
    WorkUnitRow,
    WorkUnitStato,
    WorkUnitTransitionRead,
)

WORK_UNIT_ENTITY = "work_unit"
APPROVAL_ENTITY = "approval"

# The partial unique index that allows one live day per contract per date
# (`models.py`): the name Postgres reports when a second one is recorded.
LIVE_DAY_INDEX = "uq_work_units_contract_data_live"

# The two edges that are not a person's click on a day's page: `fatturato` is what an
# invoice does to a day (`invoicing.py`), and `approvato` is what recording an approval
# does, with its proof. Offering either as a plain button would let a day be marked
# approved or billed with no evidence and no invoice behind it.
NOT_A_MANUAL_ACTION: frozenset[str] = frozenset({"approvato", "fatturato"})


def allowed_actions(stato: str) -> list[WorkUnitStato]:
    """The moves a person may make from `stato`: the state graph's own edges minus the two
    above, in the order `WorkUnitStato` lists them. Empty for a terminal state."""
    targets = WORK_UNIT_TRANSITIONS.get(stato, frozenset()) - NOT_A_MANUAL_ACTION
    if stato == "lavorato_senza_approvazione":
        # The graph has the edge back to `lavorato` and the trigger rewrites it straight
        # back to this state while no approval is linked: a button that moves nothing. The
        # way out is to link the approval, which the trigger recovers by itself.
        targets = targets - {"lavorato"}
    return [candidate for candidate in get_args(WorkUnitStato) if candidate in targets]


def actor_to_transition_json(actor: Actor) -> str:
    """`work_unit_transitions.attore`'s JSON shape (spec §5), derived from `Actor`
    (`actor.py:135`) rather than a new authentication concept: mastro's own
    `TransitionActor` names `human`/`agent`/`system`, and PigroCRM's `Actor.type`
    distinguishes those three cases as `user`/`mcp`/`system`. The fourth type, `rebase`
    (the engagements door, spec 2026-09-25 § 2.5), has no word in mastro and keeps its
    own, `{"kind": "rebase"}` with no id like `system`: folded into `agent` it would say
    the freelancer's own assistant did what rebase did."""
    payload: dict[str, object]
    if actor.type == "system":
        payload = {"kind": "system"}
    elif actor.type == "rebase":
        payload = {"kind": "rebase"}
    else:
        payload = {
            "kind": "human" if actor.type == "user" else "agent",
            "id": str(actor.id) if actor.id is not None else None,
        }
    return json.dumps(payload)


def set_transition_context(session: Session, actor: Actor, motivo: str) -> None:
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
        if ContractRepository(self.session).get(data.contract_id) is None:
            raise NotFound("contract", data.contract_id)
        set_transition_context(self.session, actor, motivo)
        try:
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
        except IntegrityError as exc:
            self.session.rollback()
            if LIVE_DAY_INDEX not in str(exc.orig):
                raise
            raise Conflict(
                WORK_UNIT_ENTITY,
                "questo contratto ha gia' una giornata in quella data: "
                "revoca o rifiuta quella esistente per registrarne un'altra",
                contract_id=str(data.contract_id),
                data=data.data.isoformat(),
            ) from exc
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
        origine = work_unit.stato
        if nuovo_stato != origine and nuovo_stato not in WORK_UNIT_TRANSITIONS.get(
            origine, frozenset()
        ):
            raise Conflict(
                WORK_UNIT_ENTITY,
                f"una giornata '{origine}' non puo' passare a '{nuovo_stato}'",
                da=origine,
                a=nuovo_stato,
            )
        set_transition_context(self.session, actor, motivo)
        work_unit.stato = nuovo_stato
        try:
            self.session.flush()
        except IntegrityError as exc:
            # The trigger is the authority, and the check above only gives the common
            # refusal a clean answer: a move the graph allows can still be refused when
            # another request moved the day in between.
            self.session.rollback()
            raise Conflict(
                WORK_UNIT_ENTITY,
                f"la giornata non puo' passare a '{nuovo_stato}': e' cambiata nel frattempo",
                a=nuovo_stato,
            ) from exc
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
        set_transition_context(self.session, actor, motivo)
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

    def detail(self, work_unit_id: UUID, actor: Actor) -> WorkUnitDetail:
        """One day as its page shows it (`WorkUnitDetail`): read, so no `require_write`;
        what an actor may see is the row-level scope's, not this method's."""
        labelled = self.repo.labelled(work_unit_id)
        if labelled is None:
            raise NotFound(WORK_UNIT_ENTITY, work_unit_id)
        work_unit, contract, customer = labelled
        approval: ApprovalDetail | None = None
        if work_unit.approval_id is not None:
            approvals = ApprovalRepository(self.session)
            row = approvals.get(work_unit.approval_id)
            if row is not None:
                approval = ApprovalDetail.model_validate(
                    {
                        **ApprovalRead.model_validate(row).model_dump(),
                        "documento_titolo": approvals.document_title(row.document_id),
                    }
                )
        return WorkUnitDetail(
            **_row(work_unit, contract.titolo, customer.id, customer.ragione_sociale).model_dump(),
            requires_prior_approval=contract.requires_prior_approval,
            contratto_inizio=contract.inizio,
            contratto_fine=contract.fine,
            fuori_termine=_term_violation(work_unit.data, contract.inizio, contract.fine),
            approval=approval,
            transizioni=self.transitions_for(work_unit_id),
            azioni=allowed_actions(work_unit.stato),
        )

    # `list` stays the last method in this class: a later `def list(...)` would rebind
    # `list` in the class namespace and Python 3.13 evaluates annotations eagerly.
    def list(self, query: WorkUnitListQuery, actor: Actor) -> WorkUnitList:
        if query.contract_id is None and query.da is None and query.a is None:
            raise ValidationFailed(
                WORK_UNIT_ENTITY,
                "contract_id",
                "indica un contratto o un intervallo di date",
                expected="contract_id, da o a",
            )
        if query.da is not None and query.a is not None and query.a < query.da:
            raise ValidationFailed(
                WORK_UNIT_ENTITY, "a", "non puo' precedere da", expected=f">= {query.da}"
            )
        rows = self.repo.list_labelled(query)
        return WorkUnitList(
            items=[
                _row(work_unit, contract.titolo, customer.id, customer.ragione_sociale)
                for work_unit, contract, customer in rows[: query.limit]
            ],
            troncato=len(rows) > query.limit,
        )


def _row(
    work_unit: WorkUnit, contract_titolo: str, customer_id: UUID, customer_nome: str
) -> WorkUnitRow:
    return WorkUnitRow.model_validate(
        {
            **WorkUnitRead.model_validate(work_unit).model_dump(),
            "contract_titolo": contract_titolo,
            "customer_id": customer_id,
            "customer_nome": customer_nome,
        }
    )


def _term_violation(giorno: date, inizio: date, fine: date | None) -> TermViolation | None:
    if giorno < inizio:
        return "prima_dell_inizio"
    if fine is not None and giorno > fine:
        return "dopo_la_fine"
    return None


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


__all__ = [
    "ApprovalService",
    "WorkUnitService",
    "actor_to_transition_json",
    "set_transition_context",
]
