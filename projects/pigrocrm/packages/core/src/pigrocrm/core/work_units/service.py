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

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import TITOLO_MAX_LENGTH
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.storage.base import DocumentStorage
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
    ApprovalRecord,
    ApprovalRecorded,
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
# What a day's approvals are recorded against in the timeline: the contract, whose page
# already shows its own history (REB-741..744). A day's own history is the transition
# ledger, which `work_unit_log_transition` writes with the actor; an approval row has no
# actor column at all, and linking one to a day that does not change state writes no
# ledger row, so both would otherwise be changes nobody is named for (REB-781).
CONTRACT_ENTITY = "contract"

# The partial unique index that allows one live day per contract per date
# (`models.py`): the name Postgres reports when a second one is recorded.
LIVE_DAY_INDEX = "uq_work_units_contract_data_live"

# The two edges that are not a person's click on a day's page: `fatturato` is what an
# invoice does to a day (`invoicing.py`), and `approvato` is what recording an approval
# does, with its proof. Offering either as a plain button would let a day be marked
# approved or billed with no evidence and no invoice behind it.
# The states a recorded approval can still use: a proposed day becomes approved, a day worked
# without the approval its contract requires is recovered to worked.
APPROVABLE_STATI: frozenset[str] = frozenset({"proposto", "lavorato_senza_approvazione"})

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
        self.activities = ActivityService(session)

    def get(self, work_unit_id: UUID) -> WorkUnitRead:
        work_unit = self.repo.get(work_unit_id)
        if work_unit is None:
            raise NotFound(WORK_UNIT_ENTITY, work_unit_id)
        return WorkUnitRead.model_validate(work_unit)

    def create(
        self,
        data: WorkUnitCreate,
        actor: Actor,
        motivo: str = "creazione",
        *,
        commit: bool = True,
    ) -> WorkUnitRead:
        """`commit=False` leaves the transaction to a caller that writes more than the day
        and needs the writes to stand or fall together (`ApprovalRequestService.prepare`);
        the row is flushed and refreshed either way.

        `WorkUnitCreate.stato`'s own `Literal` admits every state; this is the
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
        if commit:
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

    def move(self, work_unit_id: UUID, nuovo_stato: str, actor: Actor, motivo: str) -> WorkUnitRead:
        """A person's move (REB-746): `transition`, refused unless the day's own page would
        have offered it (`allowed_actions`). The graph in the database allows more than a
        person may do from a screen: `approvato` is what recording an approval writes,
        `fatturato` is what an invoice writes, and `lavorato` from a day worked without
        approval would be rewritten straight back by the trigger. `transition` stays the
        unrestricted write for the code that owns those edges."""
        actor.require_write("transition_work_unit")
        work_unit = self.repo.get(work_unit_id)
        if work_unit is None:
            raise NotFound(WORK_UNIT_ENTITY, work_unit_id)
        if nuovo_stato not in allowed_actions(work_unit.stato):
            raise Conflict(
                WORK_UNIT_ENTITY,
                f"una giornata '{work_unit.stato}' non puo' passare a '{nuovo_stato}' da qui",
                da=work_unit.stato,
                a=nuovo_stato,
            )
        return self.transition(work_unit_id, nuovo_stato, actor, motivo)

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
        self.activities.record(
            CONTRACT_ENTITY,
            work_unit.contract_id,
            "approval_linked",
            actor,
            {"giornata": work_unit.data.isoformat()},
        )
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
    def __init__(self, session: Session, storage: DocumentStorage | None = None) -> None:
        self.session = session
        self.storage = storage
        self.repo = ApprovalRepository(session)
        self.activities = ActivityService(session)

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
        self.activities.record(
            CONTRACT_ENTITY, data.contract_id, "approval_added", actor, {"canale": data.canale}
        )
        self.session.commit()
        return ApprovalRead.model_validate(approval)

    def record(self, data: ApprovalRecord, actor: Actor) -> ApprovalRecorded:
        """A person records a client's approval and the days it covers, in one transaction
        (REB-747): the proof is filed (or checked), the approval is written, and each named
        day moves, so an approval is never left covering nothing and a day is never left
        approved by an approval that is not there.

        A day moves according to where it is: `proposto` becomes `approvato` with the
        approval linked in the same write, `lavorato_senza_approvazione` has the approval
        linked and the trigger recovers it to `lavorato` (spec §5). Any other state refuses
        the whole recording with the days named: an approval is immutable evidence, and one
        recorded against a day that cannot use it is a correction nobody can make.
        """
        actor.require_write("create_approval")
        contract = ContractRepository(self.session).get(data.contract_id)
        if contract is None:
            raise NotFound("contract", data.contract_id)
        days = self._days_to_approve(data)
        document = self._proof(data, contract.customer_id, actor)

        approval = self.repo.add(
            Approval(
                contract_id=data.contract_id,
                canale=data.canale,
                mittente=data.mittente,
                ricevuto_il=data.ricevuto_il,
                message_id=self._message_header(data),
                document_id=document.id,
                estratto=data.estratto,
                origine={"kind": "manuale"},
            )
        )
        # A fixed reason: who sent the approval and when are on the approval, which is the
        # evidence, and an address does not belong in a log nobody can edit.
        set_transition_context(self.session, actor, "Approvazione registrata")
        for day in days:
            day.approval_id = approval.id
            if day.stato == "proposto":
                day.stato = "approvato"
        try:
            self.session.flush()
        except IntegrityError as exc:
            # The days were read under a lock below, so this is a move the database refused
            # that the checks above could not see (a trigger edge): the whole recording goes.
            self.session.rollback()
            raise Conflict(
                APPROVAL_ENTITY,
                "una delle giornate e' cambiata nel frattempo: riapri la pagina e riprova",
            ) from exc
        for day in days:
            self.session.refresh(day)
        self.session.refresh(approval)
        detail = ApprovalDetail.model_validate(
            {
                **ApprovalRead.model_validate(approval).model_dump(),
                "documento_titolo": document.titolo,
            }
        )
        recorded = [WorkUnitRead.model_validate(day) for day in days]
        self.activities.record(
            CONTRACT_ENTITY,
            data.contract_id,
            "approval_recorded",
            actor,
            {"giorni": len(days), "canale": data.canale},
        )
        self.session.commit()
        return ApprovalRecorded(approval=detail, work_units=recorded)

    def _days_to_approve(self, data: ApprovalRecord) -> list[WorkUnit]:
        days: list[WorkUnit] = []
        refused: list[str] = []
        for day_id in sorted(data.work_unit_ids):
            # Locked, in a fixed order: two recordings over the same day wait for each other
            # and the second then sees the first one's approval instead of overwriting it.
            day = self.session.get(WorkUnit, day_id, with_for_update=True)
            if day is None or day.contract_id != data.contract_id:
                raise NotFound(WORK_UNIT_ENTITY, day_id)
            if day.stato not in APPROVABLE_STATI or day.approval_id is not None:
                refused.append(f"{day.data.isoformat()} ({day.stato})")
            days.append(day)
        if refused:
            raise Conflict(
                APPROVAL_ENTITY,
                "queste giornate non possono ricevere un'approvazione: " + ", ".join(refused),
                giornate=refused,
            )
        return days

    def _proof(self, data: ApprovalRecord, customer_id: UUID, actor: Actor) -> Document:
        if data.document_id is not None:
            document = self.session.get(Document, data.document_id)
            if document is None or document.deleted_at is not None:
                raise NotFound("document", data.document_id)
            if document.contract_id != data.contract_id and document.customer_id != customer_id:
                raise ValidationFailed(
                    APPROVAL_ENTITY,
                    "document_id",
                    "il documento non e' di questo contratto ne' del suo cliente",
                    expected="un documento del contratto o del cliente",
                )
            if document.versione_corrente < 1:
                raise ValidationFailed(
                    APPROVAL_ENTITY,
                    "document_id",
                    "il documento non ha ancora un file archiviato: carica la prova prima",
                    expected="un documento con almeno una versione",
                )
            return document
        if self.storage is None:  # pragma: no cover - the API always passes one
            raise ValidationFailed(APPROVAL_ENTITY, "gmail_message_id", "archivio non disponibile")
        message_id = data.gmail_message_id
        if message_id is None:  # pragma: no cover - `ApprovalRecord` takes exactly one proof
            raise ValidationFailed(APPROVAL_ENTITY, "document_id", "manca la prova")
        message = GmailRepository(self.session).message(message_id)
        if message is None:
            raise NotFound("gmail_message", message_id)
        if not GmailRepository(self.session).message_linked_to(message.id, "customer", customer_id):
            raise ValidationFailed(
                APPROVAL_ENTITY,
                "gmail_message_id",
                "il messaggio non e' tra quelli di questo cliente",
                expected="un messaggio sincronizzato per il cliente del contratto",
            )
        if not message.body_text.strip():
            raise ValidationFailed(
                APPROVAL_ENTITY,
                "gmail_message_id",
                "il corpo del messaggio non e' archiviato: carica il messaggio come file",
                expected="un messaggio con il testo sincronizzato",
            )
        # A header can carry a NUL, which `SafeStr` refuses in a title; a title is cut to its width.
        titolo = f"Email di {message.from_address}: {message.subject}".replace("\x00", "")[
            :TITOLO_MAX_LENGTH
        ]
        text = (
            f"Da: {message.from_address}\n"
            f"A: {', '.join(message.to_addresses)}\n"
            f"Data: {message.internal_date.isoformat()}\n"
            f"Oggetto: {message.subject}\n\n"
            f"{message.body_text}\n"
        )
        filed = DocumentService(self.session, self.storage).import_bytes(
            contract_id=data.contract_id,
            tipo="documento",
            titolo=titolo,
            data=text.encode("utf-8"),
            content_type="text/plain",
            actor=actor,
            origine={"gmail_message_id": str(message.id), "message_id": message.message_id_header},
            commit=False,
        )
        document = self.session.get(Document, filed.id)
        if document is None:  # pragma: no cover - flushed by `import_bytes` a line above
            raise NotFound("document", filed.id)
        return document

    def _message_header(self, data: ApprovalRecord) -> str | None:
        if data.gmail_message_id is None:
            return None
        message = GmailRepository(self.session).message(data.gmail_message_id)
        return message.message_id_header or None if message is not None else None


__all__ = [
    "ApprovalService",
    "WorkUnitService",
    "actor_to_transition_json",
    "set_transition_context",
]
