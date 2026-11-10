"""The only writer of `work_units` and `approvals` (and, through the trigger,
`work_unit_transitions` -- see `triggers.py`).

This is the "repository layer" spec §5, §12 refer to: the one place that sets
`pigrocrm.actor`/`pigrocrm.reason` immediately before a `work_units` write, so
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
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import TITLE_MAX_LENGTH
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import Conflict, DomainError, NotFound, ValidationFailed
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.labels import WORK_UNIT_STATUS, label
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.work_units.models import (
    WORK_UNIT_ENTRY_STATUSES,
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
    WorkUnitStatus,
    WorkUnitTransitionRead,
)

WORK_UNIT_ENTITY = "work_unit"
APPROVAL_ENTITY = "approval"
# What a contract's days and approvals are recorded against in the timeline: the contract,
# whose page already shows its own history (REB-741..744). The day's own ledger
# (`work_unit_transitions`, written by `work_unit_log_transition` with the actor) stays the
# authority on its states, but it names a member by id and a channel as «human» or «agent»
# and no timeline serves it; an approval row has no actor column at all, and linking one to
# a day that does not change state writes no ledger row. So each of these writes also leaves
# a contract entry naming the member and the channel (REB-781).
CONTRACT_ENTITY = "contract"

# The partial unique index that allows one live day per contract per date
# (`models.py`): the name Postgres reports when a second one is recorded.
LIVE_DAY_INDEX = "uq_work_units_contract_date_live"

# The two edges that are not a person's click on a day's page: `invoiced` is what an
# invoice does to a day (`invoicing.py`), and `approved` is what recording an approval
# does, with its proof. Offering either as a plain button would let a day be marked
# approved or billed with no evidence and no invoice behind it.
# The states a recorded approval can still use: a proposed day becomes approved, a day worked
# without the approval its contract requires is recovered to worked.
APPROVABLE_STATUSES: frozenset[str] = frozenset({"proposed", "worked_without_approval"})

NOT_A_MANUAL_ACTION: frozenset[str] = frozenset({"approved", "invoiced"})


def allowed_actions(status: str) -> list[WorkUnitStatus]:
    """The moves a person may make from `status`: the state graph's own edges minus the two
    above, in the order `WorkUnitStatus` lists them. Empty for a terminal state."""
    targets = WORK_UNIT_TRANSITIONS.get(status, frozenset()) - NOT_A_MANUAL_ACTION
    if status == "worked_without_approval":
        # The graph has the edge back to `worked` and the trigger rewrites it straight
        # back to this state while no approval is linked: a button that moves nothing. The
        # way out is to link the approval, which the trigger recovers by itself.
        targets = targets - {"worked"}
    return [candidate for candidate in get_args(WorkUnitStatus) if candidate in targets]


def actor_to_transition_json(actor: Actor) -> str:
    """`work_unit_transitions.actor`'s JSON shape (spec §5), derived from `Actor`
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


def set_transition_context(session: Session, actor: Actor, reason: str) -> None:
    """Session-local Postgres settings (`set_config(..., true)`, scoped to the
    transaction), read by `work_unit_log_transition` immediately after. Local, not
    global: the setting resets at the next commit or rollback, so it can never leak
    into an unrelated request that reuses a pooled connection."""
    session.execute(
        text("SELECT set_config('pigrocrm.actor', :v, true)"),
        {"v": actor_to_transition_json(actor)},
    )
    session.execute(text("SELECT set_config('pigrocrm.reason', :v, true)"), {"v": reason})


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
        reason: str = "creazione",
        *,
        commit: bool = True,
    ) -> WorkUnitRead:
        """`commit=False` leaves the transaction to a caller that writes more than the day
        and needs the writes to stand or fall together (`ApprovalRequestService.prepare`);
        the row is flushed and refreshed either way.

        `WorkUnitCreate.status`'s own `Literal` admits every state; this is the
        narrower check against `WORK_UNIT_ENTRY_STATUSES` -- a `ValidationFailed` here
        instead of the trigger's own `IntegrityError`, for the one case a schema can
        catch without a round trip to Postgres. The trigger enforces the identical
        rule regardless (`test_work_units_models.py`), so a caller that reaches this
        method through anything but this service is refused just the same."""
        actor.require_write("create_work_unit")
        if data.status not in WORK_UNIT_ENTRY_STATUSES:
            raise ValidationFailed(
                WORK_UNIT_ENTITY,
                "status",
                f"una giornata non si crea direttamente in stato "
                f"'{label(WORK_UNIT_STATUS, data.status)}'",
                expected=f"uno tra {sorted(WORK_UNIT_ENTRY_STATUSES)}",
            )
        if ContractRepository(self.session).get(data.contract_id) is None:
            raise NotFound("contract", data.contract_id)
        set_transition_context(self.session, actor, reason)

        def refuse(exc: IntegrityError) -> DomainError | IntegrityError:
            if LIVE_DAY_INDEX not in str(exc.orig):
                return exc
            return Conflict(
                WORK_UNIT_ENTITY,
                "questo contratto ha gia' una giornata in quella data: "
                "revoca o rifiuta quella esistente per registrarne un'altra",
                contract_id=str(data.contract_id),
                date=data.date.isoformat(),
            )

        with savepoint(self.session, refuse):
            work_unit = self.repo.add(
                WorkUnit(
                    contract_id=data.contract_id,
                    date=data.date,
                    quantity=data.quantity,
                    description=data.description,
                    status=data.status,
                    approval_id=data.approval_id,
                    note=data.note,
                )
            )
        # `work_unit_enforce_state_machine` can silently rewrite `status` in flight
        # (the redirect/recovery, spec §5) -- `expire_on_commit=False`
        # (`db/session.py`) means the ORM object otherwise keeps showing the value
        # the caller *requested*, not the one the trigger actually wrote. A refresh
        # is what makes the redirect observable to a caller of this service, which
        # is the entire point of the Done-when ("a day ... lands in
        # 'lavorato_senza_approvazione' automatically").
        self.session.refresh(work_unit)
        # The day's own ledger holds who and when (`work_unit_transitions`); the contract's
        # timeline holds it too, with the member's name and the channel, which is where a
        # person looking for «who proposed this day» looks (REB-781). Written after the
        # refresh, so a state the trigger redirected is the state recorded.
        self.activities.record(
            CONTRACT_ENTITY,
            work_unit.contract_id,
            "day_created",
            actor,
            {"work_unit": work_unit.date.isoformat(), "status": work_unit.status},
        )
        if commit:
            self.session.commit()
        return WorkUnitRead.model_validate(work_unit)

    def transition(
        self, work_unit_id: UUID, new_status: str, actor: Actor, reason: str
    ) -> WorkUnitRead:
        """Moves a day. The database is what actually enforces the edge
        (`work_unit_enforce_state_machine`) -- this is only where the transition
        context is set before the write reaches it."""
        actor.require_write("transition_work_unit")
        work_unit = self.repo.get(work_unit_id)
        if work_unit is None:
            raise NotFound(WORK_UNIT_ENTITY, work_unit_id)
        origin = work_unit.status
        if new_status != origin and new_status not in WORK_UNIT_TRANSITIONS.get(
            origin, frozenset()
        ):
            raise Conflict(
                WORK_UNIT_ENTITY,
                f"una giornata '{label(WORK_UNIT_STATUS, origin)}' non puo' passare a "
                f"'{label(WORK_UNIT_STATUS, new_status)}'",
                from_status=origin,
                to_status=new_status,
            )
        set_transition_context(self.session, actor, reason)
        # The trigger is the authority, and the check above only gives the common refusal a
        # clean answer: a move the graph allows can still be refused when another request
        # moved the day in between.
        with savepoint(
            self.session,
            lambda exc: Conflict(
                WORK_UNIT_ENTITY,
                f"la giornata non puo' passare a '{label(WORK_UNIT_STATUS, new_status)}': e' "
                f"cambiata nel frattempo",
                to_status=new_status,
            ),
        ):
            work_unit.status = new_status
        self.session.refresh(work_unit)  # see create()'s own comment
        if work_unit.status != origin:
            self.activities.record(
                CONTRACT_ENTITY,
                work_unit.contract_id,
                "day_state_changed",
                actor,
                {"work_unit": work_unit.date.isoformat(), "from": origin, "to": work_unit.status},
            )
        self.session.commit()
        return WorkUnitRead.model_validate(work_unit)

    def move(self, work_unit_id: UUID, new_status: str, actor: Actor, reason: str) -> WorkUnitRead:
        """A person's move (REB-746): `transition`, refused unless the day's own page would
        have offered it (`allowed_actions`). The graph in the database allows more than a
        person may do from a screen: `approved` is what recording an approval writes,
        `invoiced` is what an invoice writes, and `worked` from a day worked without
        approval would be rewritten straight back by the trigger. `transition` stays the
        unrestricted write for the code that owns those edges."""
        actor.require_write("transition_work_unit")
        work_unit = self.repo.get(work_unit_id)
        if work_unit is None:
            raise NotFound(WORK_UNIT_ENTITY, work_unit_id)
        if new_status not in allowed_actions(work_unit.status):
            raise Conflict(
                WORK_UNIT_ENTITY,
                f"una giornata '{label(WORK_UNIT_STATUS, work_unit.status)}' non puo' passare a "
                f"'{label(WORK_UNIT_STATUS, new_status)}' da qui",
                from_status=work_unit.status,
                to_status=new_status,
            )
        return self.transition(work_unit_id, new_status, actor, reason)

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
                        "title_document": approvals.document_title(row.document_id),
                    }
                )
        return WorkUnitDetail(
            **_row(work_unit, contract.title, customer.id, customer.legal_name).model_dump(),
            requires_prior_approval=contract.requires_prior_approval,
            start_contract=contract.start,
            contract_end=contract.end,
            out_of_term=_term_violation(work_unit.date, contract.start, contract.end),
            approval=approval,
            transitions=self.transitions_for(work_unit_id),
            actions=allowed_actions(work_unit.status),
        )

    # `list` stays the last method in this class: a later `def list(...)` would rebind
    # `list` in the class namespace and Python 3.13 evaluates annotations eagerly.
    def list(self, query: WorkUnitListQuery, actor: Actor) -> WorkUnitList:
        if query.contract_id is None and query.date_from is None and query.date_to is None:
            raise ValidationFailed(
                WORK_UNIT_ENTITY,
                "contract_id",
                "indica un contratto o un intervallo di date",
                expected="contract_id, da o a",
            )
        if (
            query.date_from is not None
            and query.date_to is not None
            and query.date_to < query.date_from
        ):
            raise ValidationFailed(
                WORK_UNIT_ENTITY,
                "to",
                "non puo' precedere 'from'",
                expected=f">= {query.date_from}",
            )
        rows = self.repo.list_labelled(query)
        return WorkUnitList(
            items=[
                _row(work_unit, contract.title, customer.id, customer.legal_name)
                for work_unit, contract, customer in rows[: query.limit]
            ],
            truncated=len(rows) > query.limit,
        )


def _row(
    work_unit: WorkUnit, contract_title: str, customer_id: UUID, customer_name: str
) -> WorkUnitRow:
    return WorkUnitRow.model_validate(
        {
            **WorkUnitRead.model_validate(work_unit).model_dump(),
            "contract_title": contract_title,
            "customer_id": customer_id,
            "customer_name": customer_name,
        }
    )


def _term_violation(day: date, start: date, end: date | None) -> TermViolation | None:
    if day < start:
        return "before_start"
    if end is not None and day > end:
        return "after_end"
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
                channel=data.channel,
                sender=data.sender,
                received_at=data.received_at,
                message_id=data.message_id,
                document_id=data.document_id,
                excerpt=data.excerpt,
                origin=data.origin,
            )
        )
        self.activities.record(
            CONTRACT_ENTITY, data.contract_id, "approval_added", actor, {"channel": data.channel}
        )
        self.session.commit()
        return ApprovalRead.model_validate(approval)

    def record(self, data: ApprovalRecord, actor: Actor) -> ApprovalRecorded:
        """A person records a client's approval and the days it covers, in one transaction
        (REB-747): the proof is filed (or checked), the approval is written, and each named
        day moves, so an approval is never left covering nothing and a day is never left
        approved by an approval that is not there.

        A day moves according to where it is: `proposed` becomes `approved` with the
        approval linked in the same write, `worked_without_approval` has the approval
        linked and the trigger recovers it to `worked` (spec §5). Any other state refuses
        the whole recording with the days named: an approval is immutable evidence, and one
        recorded against a day that cannot use it is a correction nobody can make.
        """
        actor.require_write("create_approval")
        contract = ContractRepository(self.session).get(data.contract_id)
        if contract is None:
            raise NotFound("contract", data.contract_id)
        days = self._days_to_approve(data)
        document = self._proof(data, contract.customer_id, actor)

        # A fixed reason: who sent the approval and when are on the approval, which is the
        # evidence, and an address does not belong in a log nobody can edit.
        set_transition_context(self.session, actor, "Approvazione registrata")
        # The days were read under a lock above, so a refusal here is a move the database
        # refused that the checks could not see (a trigger edge): the approval and the
        # moves go together, in a savepoint, and nothing else the caller has pending.
        with savepoint(
            self.session,
            lambda exc: Conflict(
                APPROVAL_ENTITY,
                "una delle giornate e' cambiata nel frattempo: riapri la pagina e riprova",
            ),
        ):
            approval = self.repo.add(
                Approval(
                    contract_id=data.contract_id,
                    channel=data.channel,
                    sender=data.sender,
                    received_at=data.received_at,
                    message_id=self._message_header(data),
                    document_id=document.id,
                    excerpt=data.excerpt,
                    origin={"kind": "manual"},
                )
            )
            for day in days:
                day.approval_id = approval.id
                if day.status == "proposed":
                    day.status = "approved"
        for day in days:
            self.session.refresh(day)
        self.session.refresh(approval)
        detail = ApprovalDetail.model_validate(
            {
                **ApprovalRead.model_validate(approval).model_dump(),
                "title_document": document.title,
            }
        )
        recorded = [WorkUnitRead.model_validate(day) for day in days]
        self.activities.record(
            CONTRACT_ENTITY,
            data.contract_id,
            "approval_recorded",
            actor,
            {"days": len(days), "channel": data.channel},
        )
        self.session.commit()
        return ApprovalRecorded(approval=detail, work_units=recorded)

    def _days_to_approve(self, data: ApprovalRecord) -> list[WorkUnit]:
        days: list[WorkUnit] = []
        refused: list[str] = []
        for day_id in sorted(data.work_unit_ids):
            # Locked, in a fixed order: two recordings over the same day wait for each other
            # and the second then sees the first one's approval instead of overwriting it.
            day = self.session.get(WorkUnit, day_id, with_for_update=True, populate_existing=True)
            if day is None or day.contract_id != data.contract_id:
                raise NotFound(WORK_UNIT_ENTITY, day_id)
            if day.status not in APPROVABLE_STATUSES or day.approval_id is not None:
                refused.append(f"{day.date.isoformat()} ({label(WORK_UNIT_STATUS, day.status)})")
            days.append(day)
        if refused:
            raise Conflict(
                APPROVAL_ENTITY,
                "queste giornate non possono ricevere un'approvazione: " + ", ".join(refused),
                work_units=refused,
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
            if document.current_version < 1:
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
        if not GmailRepository(self.session).message_linked_to(
            message.id, "organization", customer_id
        ):
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
        title = f"Email di {message.from_address}: {message.subject}".replace("\x00", "")[
            :TITLE_MAX_LENGTH
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
            type="document",
            title=title,
            data=text.encode("utf-8"),
            content_type="text/plain",
            actor=actor,
            origin={"gmail_message_id": str(message.id), "message_id": message.message_id_header},
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
