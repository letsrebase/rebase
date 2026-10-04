from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.timetracking.categories import CostCategoryService
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.models import Cost
from pigrocrm.core.timetracking.repository import CostRepository
from pigrocrm.core.timetracking.schemas import (
    CostCreate,
    CostListQuery,
    CostPage,
    CostRead,
    CostUpdate,
)

# Typed as the fields module's own EntityType (not a bare `str`), matching
# TimeEntryService.ENTITY/DealService.ENTITY exactly: passing a plain `str` into
# `specs_for` fails mypy strict, which requires the narrower Literal type.
ENTITY: EntityType = "cost"


class CostService:
    """Real money out, with a receipt.

    Not a variant of `TimeEntryService`, and the three differences are the ones that
    matter (§4.2): a cost is money that actually left towards somebody else and has a
    document proving it, while an hour's cost is an internal notional figure derived
    from a rate you chose; an hour is also *potential revenue* and a cost never is; and
    an hour has a quantity comparable with an estimate while a cost has only money.

    The labour cost never produces a row here and is never counted twice: an external
    consultant who invoices you their hours is a `cost` in category "Consulenza
    esterna", and their hours -- if you record them at all -- carry
    `costo_applicato = NULL`. The two sets are disjoint by construction, and Task
    4B-4's test proves no path lets the same expense in from both sides (§7.2).
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CostRepository(session)
        self.deals = DealRepository(session)
        self.aziende = AziendaService(session)
        self.documents = DocumentRepository(session)
        self.categories = CostCategoryService(session)
        self.locks = PeriodLockService(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    def _check_importo(self, importo: Decimal | None) -> None:
        """Zero is refused because it is neither a cost nor a correction. Checked here
        as well as by `ck_costs_importo_non_zero`, not instead of it: this raises the
        project's own `ValidationFailed` naming the field, the CHECK covers every other
        write path including raw SQL.

        `None` is refused separately, and it is a different sentence: since A14 was
        closed an `Update` schema can spell "clear this column", and a caller who spells
        it here means something the column cannot hold -- `costs.importo` is `NOT NULL`,
        and an emptied amount is not a zero amount. `reject_cleared_columns` would catch
        it too, one line later and with the generic message; this branch runs first so
        the refusal names the amount."""
        if importo is None:
            raise ValidationFailed(
                ENTITY, "importo", "l'importo non può essere svuotato", expected="un importo"
            )
        if importo == 0:
            raise ValidationFailed(
                ENTITY, "importo", "importo nullo", expected="un importo diverso da zero"
            )

    def _check_refs(self, deal_id: UUID | None, document_id: UUID | None) -> None:
        """Every foreign key validated in both create and update, optional ones
        included -- skipped only when the caller supplies nothing, never when they
        supply a value. Without this any syntactically valid UUID reaches `flush()` and
        comes back as a raw `ForeignKeyViolation`."""
        if deal_id is not None and self.deals.get(deal_id) is None:
            raise NotFound("deal", deal_id)
        if document_id is not None and self.documents.get(document_id) is None:
            raise NotFound("document", document_id)

    def _azienda_for(
        self, deal_id: UUID | None, wanted: UUID | None, *, kept: UUID | None = None
    ) -> UUID | None:
        """A cost's azienda (REB-623, spec §1.7): the deal's when it has a deal, and a
        different one named beside it is refused rather than overruled in silence;
        without a deal, the one named, which must exist and be active, or `None`, a
        shared cost. `kept` is the azienda the cost already has: an edit that leaves it
        where it is passes even once that azienda is deactivated, so the history of a
        closed azienda stays editable while nothing new is filed under it (spec §3)."""
        if deal_id is not None:
            # The deal a cost already hangs on may be archived since: its azienda still
            # answers, and an update of such a cost is a domain error at worst, never
            # an assertion.
            deal = self.deals.get(deal_id, include_deleted=True)
            if deal is None:
                raise NotFound("deal", deal_id)
            if wanted is not None and wanted != deal.azienda_id:
                raise ValidationFailed(
                    ENTITY,
                    "azienda_id",
                    "un costo su un deal prende l'azienda del deal",
                    expected=f"nessuna azienda, oppure {deal.azienda_id}",
                )
            # The deal's azienda may be closed since the deal was created: a cost already
            # there stays editable, a new one is refused like a deal or a document.
            if deal.azienda_id == kept:
                return deal.azienda_id
            return self.aziende.inherited(deal.azienda_id, ENTITY)
        if wanted is None:
            return None
        azienda = self.aziende.resolve(wanted)
        if not azienda.attiva and wanted != kept:
            raise ValidationFailed(
                ENTITY,
                "azienda_id",
                "l'azienda non e' attiva: una spesa si assegna a un'azienda attiva",
                expected="l'id di un'azienda attiva, oppure nessuna per una spesa condivisa",
            )
        return azienda.id

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _update_custom_fields(self, cost: Cost, provided: dict[str, Any]) -> dict[str, Any]:
        """Identical contract to `DealService._update_custom_fields` and
        `TimeEntryService._update_custom_fields`; see either for the full reasoning."""
        active_by_key = {spec.key: spec for spec in self.fields.specs_for(ENTITY)}
        to_remove: set[str] = set()
        for key, value in provided.items():
            if value is not None:
                continue
            spec = active_by_key.get(key)
            if spec is not None and spec.required:
                raise ValidationFailed(
                    ENTITY, key, "campo obbligatorio", expected="un valore non vuoto"
                )
            to_remove.add(key)
        to_set = {k: v for k, v in provided.items() if v is not None}
        touched = [spec for spec in active_by_key.values() if spec.key in to_set]
        validated = validate_custom_fields(ENTITY, touched, to_set)
        merged = {k: v for k, v in cost.custom_fields.items() if k not in to_remove}
        merged.update(validated)
        return merged

    def _require(self, cost_id: UUID, *, include_deleted: bool = False) -> Cost:
        cost = self.repo.get(cost_id, include_deleted=include_deleted)
        if cost is None:
            raise NotFound(ENTITY, cost_id)
        return cost

    def create(self, data: CostCreate, actor: Actor) -> CostRead:
        actor.require_write("create_cost")
        self._check_importo(data.importo)
        self._check_refs(data.deal_id, data.document_id)
        self.categories.require_active(data.category_id)
        self.locks.assert_writable(ENTITY, "data", data.data)

        cost = self.repo.add(
            Cost(
                deal_id=data.deal_id,
                azienda_id=self._azienda_for(data.deal_id, data.azienda_id),
                category_id=data.category_id,
                data=data.data,
                # The **total paid**, VAT included: under the flat-rate regime input VAT
                # is not deductible, so it is cost in every sense and recording the net
                # would understate it by 22% (§4.4).
                importo=data.importo,
                descrizione=data.descrizione,
                fornitore=data.fornitore,
                document_id=data.document_id,
                custom_fields=self._validated_custom(data.custom_fields or {}),
            )
        )
        self.activities.record(
            ENTITY,
            cost.id,
            "created",
            actor,
            {
                "importo": str(cost.importo),
                "deal_id": str(cost.deal_id) if cost.deal_id else None,
            },
        )
        self.session.commit()
        return CostRead.model_validate(cost)

    def update(self, cost_id: UUID, data: CostUpdate, actor: Actor) -> CostRead:
        actor.require_write("update_cost")
        cost = self._require(cost_id)
        changes = supplied_changes(data, exclude={"custom_fields"})
        # Before the generic guard, deliberately: `importo` is the one `NOT NULL` column
        # on this table a caller plausibly tries to empty, and it deserves the message
        # that names it rather than the catch-all one.
        if "importo" in changes:
            self._check_importo(changes["importo"])
        reject_cleared_columns(ENTITY, Cost, changes)
        # `_check_refs` already reads values rather than keys, so an explicit
        # `deal_id: null` -- now reachable -- passes through it as "clear it" instead of
        # becoming a lookup for `None`.
        self._check_refs(changes.get("deal_id"), changes.get("document_id"))
        if "deal_id" in changes or "azienda_id" in changes:
            # Moved to another deal, off a deal, or given an azienda of its own: the
            # azienda follows the deal when there is one and the request otherwise.
            deal_id = changes.get("deal_id", cost.deal_id)
            # Off a deal with no azienda named: shared, not the old deal's.
            kept = None if "deal_id" in changes else cost.azienda_id
            wanted = changes.get("azienda_id", kept)
            # `kept` only while the cost stays where it is: a move onto another deal is a
            # new assignment, and a closed azienda takes none (Greptile, PR #509). A
            # `deal_id` sent back unchanged, as a client echoing the row does, is no move.
            moved = "deal_id" in changes and changes["deal_id"] != cost.deal_id
            kept_azienda = None if moved else cost.azienda_id
            changes["azienda_id"] = self._azienda_for(deal_id, wanted, kept=kept_azienda)
        if changes.get("category_id") is not None:
            self.categories.require_active(changes["category_id"])
        self.locks.assert_writable(ENTITY, "data", cost.data, changes.get("data"))

        if data.custom_fields is not None:
            changes["custom_fields"] = self._update_custom_fields(cost, data.custom_fields)
        for key, value in changes.items():
            setattr(cost, key, value)
        self.activities.record(ENTITY, cost.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return CostRead.model_validate(cost)

    def soft_delete(self, cost_id: UUID, actor: Actor) -> None:
        actor.require_write("delete_cost")
        cost = self._require(cost_id)
        self.locks.assert_writable(ENTITY, "data", cost.data)
        cost.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, cost.id, "deleted", actor)
        self.session.commit()

    def restore(self, cost_id: UUID, actor: Actor) -> CostRead:
        actor.require_write("restore_cost")
        cost = self._require(cost_id, include_deleted=True)
        self.locks.assert_writable(ENTITY, "data", cost.data)
        was_deleted = cost.deleted_at is not None
        cost.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, cost.id, "restored", actor)
        self.session.commit()
        return CostRead.model_validate(cost)

    def get(self, cost_id: UUID, actor: Actor) -> CostRead:
        return CostRead.model_validate(self._require(cost_id))

    # `list` stays the last method in this class -- the unconditional project rule.
    def list(self, query: CostListQuery, actor: Actor) -> CostPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        return CostPage(
            items=[CostRead.model_validate(c) for c in items],
            next_cursor=items[-1].id if has_more and items else None,
        )
