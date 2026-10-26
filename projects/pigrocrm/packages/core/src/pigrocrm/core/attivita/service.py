from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.attivita.repository import AttivitaRepository
from pigrocrm.core.attivita.schemas import (
    ATTIVITA_SORTS,
    GIORNI_PROSSIMI,
    RIFERIMENTI,
    AttivitaCreate,
    AttivitaGruppo,
    AttivitaListQuery,
    AttivitaOggi,
    AttivitaOggiQuery,
    AttivitaPage,
    AttivitaRead,
    AttivitaUpdate,
)
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import encode_cursor, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.people.models import Person
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

ENTITY: EntityType = "attivita"

# The two closed states, and the one open one. As a frozenset rather than repeated
# literals because three methods ask the same question.
CHIUSI = frozenset({"completata", "annullata"})

# Which table each reference column points at, so `_check_riferimento` is a loop and not
# four near-identical blocks. The order is `RIFERIMENTI`'s.
_TABELLE: dict[str, type[Customer] | type[Person] | type[Deal] | type[Invoice]] = {
    "customer_id": Customer,
    "person_id": Person,
    "deal_id": Deal,
    "invoice_id": Invoice,
}


class AttivitaService:
    """Commitments: create, rewrite, close, and the two ways of closing.

    Every write goes through `actor.require_write`, and none of these operations is on
    the MCP ban list of slice 3 §11: nothing here consumes a number, touches the fiscal
    register or rewrites a rate. An activity created in error is archived; one that is
    no longer needed is *cancelled*, which is a different statement and the reason the
    state machine has three states.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = AttivitaRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    # ---- checks -----------------------------------------------------------------------

    def _check_riferimento(self, payload: dict[str, Any]) -> None:
        """At most one reference, and it must exist.

        The database has the same check (`ck_attivita_un_solo_riferimento`) and is the
        line that holds under concurrency; this is the one that turns a syntactically
        valid but unknown UUID into this project's own `NotFound` instead of a raw
        `ForeignKeyViolation` arriving from `flush()`.

        Zero references is legitimate and says so in the spec: «do March's e-invoicing»
        belongs to no customer.
        """
        named = [field for field in RIFERIMENTI if payload.get(field) is not None]
        if len(named) > 1:
            raise ValidationFailed(
                ENTITY,
                named[1],
                "un'attività può riferirsi a un solo cliente, persona, deal o fattura",
                expected=f"al massimo uno fra {', '.join(RIFERIMENTI)}",
            )
        for field in named:
            identifier = payload[field]
            if self.session.get(_TABELLE[field], identifier) is None:
                raise NotFound(field.removesuffix("_id"), identifier)

    def _check_assegnata(self, payload: dict[str, Any]) -> None:
        """`None` is accepted and means «whoever opens the list», not «nobody» -- the
        spec's own words. A supplied id has to be a user that exists, and an inactive
        one is refused: assigning work to a disabled account is a commitment nobody will
        ever see."""
        user_id = payload.get("assegnata_a")
        if user_id is None:
            return
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user", user_id)
        if not user.attivo:
            raise Conflict(ENTITY, "l'utente è disattivato", user_id=str(user_id))

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _require(self, attivita_id: UUID) -> Attivita:
        row = self.repo.get(attivita_id)
        if row is None:
            raise NotFound(ENTITY, attivita_id)
        return row

    # ---- writes -----------------------------------------------------------------------

    def create(self, data: AttivitaCreate, actor: Actor) -> AttivitaRead:
        actor.require_write("create_attivita")
        payload = data.model_dump()
        self._check_riferimento(payload)
        self._check_assegnata(payload)
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})
        # `origine` is not a field of `AttivitaCreate`: everything created through this
        # method is manual by definition. An automation writes its own rows with
        # `origine='automazione'` and a `regola`, and the day slice 7 §6 is built it will
        # do so through a method of its own -- not by letting a caller claim to be one.
        row = self.repo.add(Attivita(**payload, stato="aperta", origine="manuale"))
        self.activities.record(
            ENTITY, row.id, "created", actor, {"titolo": row.titolo, "scadenza": row.scadenza}
        )
        self.session.commit()
        return AttivitaRead.model_validate(row)

    def update(self, attivita_id: UUID, data: AttivitaUpdate, actor: Actor) -> AttivitaRead:
        actor.require_write("update_attivita")
        row = self._require(attivita_id)

        changes = supplied_changes(
            data,
            exclude={"custom_fields", "scadenza_da_rimuovere", "riferimento_da_rimuovere"},
        )
        reject_cleared_columns(ENTITY, Attivita, changes)

        # The two explicit clears, applied before the checks so that «move this activity
        # from the deal to nobody, and give it a date» is one coherent request.
        if data.scadenza_da_rimuovere:
            if "scadenza" in changes:
                raise ValidationFailed(
                    ENTITY,
                    "scadenza",
                    "non si può indicare una scadenza e chiederne la rimozione nella stessa "
                    "richiesta",
                    expected="una scadenza oppure scadenza_da_rimuovere",
                )
            changes["scadenza"] = None
        if data.riferimento_da_rimuovere:
            if any(field in changes for field in RIFERIMENTI):
                raise ValidationFailed(
                    ENTITY,
                    "riferimento_da_rimuovere",
                    "non si può collegare un'attività e scollegarla nella stessa richiesta",
                    expected="un riferimento oppure riferimento_da_rimuovere",
                )
            for field in RIFERIMENTI:
                changes[field] = None

        # The check reads the *result*, not the patch: a request that sets `deal_id` on
        # an activity already hanging off a customer would pass a check that only looked
        # at what was supplied, and then fail at the database.
        risultante = {
            field: changes.get(field, getattr(row, field))
            for field in (*RIFERIMENTI, "assegnata_a")
        }
        self._check_riferimento(risultante)
        self._check_assegnata(risultante)

        if data.custom_fields is not None:
            changes["custom_fields"] = self._validated_custom(data.custom_fields)

        for field, value in changes.items():
            setattr(row, field, value)
        if changes:
            self.activities.record(ENTITY, row.id, "updated", actor, {"campi": sorted(changes)})
        self.session.commit()
        return AttivitaRead.model_validate(row)

    def complete(self, attivita_id: UUID, actor: Actor) -> AttivitaRead:
        """«Done», with the day it was done.

        `today_local()` and never `date.today()`: slice 6B reduced this project to one
        clock and an AST scan fails the build on a second one. Idempotent on purpose --
        completing something already completed is what a second click on a checkbox is,
        and it must not move the date, because the date is when the work happened.
        """
        actor.require_write("complete_attivita")
        row = self._require(attivita_id)
        if row.stato == "completata":
            return AttivitaRead.model_validate(row)
        if row.stato == "annullata":
            raise Conflict(
                ENTITY,
                "un'attività annullata non si completa: riaprila prima",
                attivita_id=str(attivita_id),
            )
        row.stato = "completata"
        row.completata_il = today_local()
        self.activities.record(ENTITY, row.id, "completata", actor, {"il": row.completata_il})
        self.session.commit()
        return AttivitaRead.model_validate(row)

    def cancel(self, attivita_id: UUID, actor: Actor) -> AttivitaRead:
        """«It is no longer needed», which is not «it never existed».

        The whole reason the third state exists: a commitment that stopped mattering
        carries the fact that it stopped, and the same row deleted carries nothing.
        Completing is not available afterwards without reopening, so the two closed
        states cannot be confused for one another.
        """
        actor.require_write("cancel_attivita")
        row = self._require(attivita_id)
        if row.stato == "annullata":
            return AttivitaRead.model_validate(row)
        row.stato = "annullata"
        # Cleared, because the check constraint ties the date to the completed state and
        # because «cancelled on» is not a thing this table records: what is recorded is
        # that it was cancelled, in the timeline, with who did it.
        row.completata_il = None
        self.activities.record(ENTITY, row.id, "annullata", actor)
        self.session.commit()
        return AttivitaRead.model_validate(row)

    def reopen(self, attivita_id: UUID, actor: Actor) -> AttivitaRead:
        """Back to `aperta`, from either closed state.

        Exists because both closures are reversible decisions about the future, unlike
        anything in slice 3: I completed the wrong row, or the client came back. The
        completion date goes with it -- keeping it would leave a row that is open and
        also says when it was finished.
        """
        actor.require_write("reopen_attivita")
        row = self._require(attivita_id)
        if row.stato == "aperta":
            return AttivitaRead.model_validate(row)
        row.stato = "aperta"
        row.completata_il = None
        self.activities.record(ENTITY, row.id, "riaperta", actor)
        self.session.commit()
        return AttivitaRead.model_validate(row)

    def soft_delete(self, attivita_id: UUID, actor: Actor) -> None:
        """For the typo, not for the change of plan -- which is `cancel`."""
        actor.require_write("archive_attivita")
        row = self._require(attivita_id)
        row.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, row.id, "archived", actor)
        self.session.commit()

    def restore(self, attivita_id: UUID, actor: Actor) -> AttivitaRead:
        actor.require_write("restore_attivita")
        row = self.repo.get(attivita_id, include_deleted=True)
        if row is None:
            raise NotFound(ENTITY, attivita_id)
        # Recorded only if it really was archived: an unconditional «restored» would
        # claim a recovery that never happened -- the guard `PersonService.restore`
        # carries, for the same reason.
        was_deleted = row.deleted_at is not None
        row.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, row.id, "restored", actor)
        self.session.commit()
        return AttivitaRead.model_validate(row)

    def get(self, attivita_id: UUID, actor: Actor) -> AttivitaRead:
        return AttivitaRead.model_validate(self._require(attivita_id))

    def oggi(self, actor: Actor, query: AttivitaOggiQuery) -> AttivitaOggi:
        """The four sections of the Oggi panel, cut on the emitter's today.

        `query.assegnata_a` narrows every section to one person's; `None` is the whole space.
        The day is read once and every section is cut on it, so a request that straddles
        midnight cannot list the same commitment under two headings.
        """
        giorno = today_local()

        def gruppo(**finestra: Any) -> AttivitaGruppo:
            totale, righe = self.repo.aperte_nella_finestra(
                assegnata_a=query.assegnata_a, limit=query.limit, **finestra
            )
            return AttivitaGruppo(
                totale=totale, items=[AttivitaRead.model_validate(riga) for riga in righe]
            )

        return AttivitaOggi(
            oggi=giorno,
            in_ritardo=gruppo(prima_del=giorno),
            scadono_oggi=gruppo(dal=giorno, al=giorno),
            prossimi_sette_giorni=gruppo(
                dal=giorno + timedelta(days=1), al=giorno + timedelta(days=GIORNI_PROSSIMI)
            ),
            senza_scadenza=gruppo(senza_scadenza=True),
        )

    # `list` stays the last method of this class -- the unconditional project rule: the
    # name is rebound in the class namespace, so any method defined after it whose
    # return annotation is a bare `list[...]` would resolve `list` to this method and
    # fail at import time.
    def list(self, query: AttivitaListQuery, actor: Actor) -> AttivitaPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        spec = ATTIVITA_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return AttivitaPage(
            items=[AttivitaRead.model_validate(row) for row in items],
            next_cursor=next_cursor,
        )
