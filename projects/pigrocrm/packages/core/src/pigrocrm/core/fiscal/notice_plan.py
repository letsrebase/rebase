"""The only writer of `tax_notice_plans` and of their due items (REB-1075, taxes design § 2 and
§ 5).

A notice from the tax office («avviso bonario», «cartella») that grants a payment in instalments
is typed by an administrator: what it is, the tax, the total it asks before interest and each
instalment's day and interest. The plan is stored as typed and writes one due item out per
instalment, source kind `notice_plan`, state `expected` (the notice fixes what is owed), the tax
office the counterparty with the tax named. The principal is split equally over the instalments
(the last takes the cent difference) and each instalment adds its own interest, so an item's
amount is what that instalment costs on its day. Nothing is computed from the law here: the notice
is the source, and a date in the past is an instalment already late, shown as such.

The items are the plan's and are written with it, not recomputed: `TaxDueItemService` never
touches them. **A plan with a payment recorded on one of its items is neither changed nor
deleted** (the boring option: the paid ones cannot be rewritten without rewriting what was paid);
the payment is reversed first. Before any payment, changing a plan drops its items and writes the
new ones, deleting it drops them and removes the row (the dropped items stay in the history).

Every action is an administrator's whose scope covers the legal entity, an agent's only with
`full_access` (`agent_actions.py`); the service asks first, before it looks anything up. Method
names are unique across the services on purpose: `FORBIDDEN_SERVICE_CALLS` of the MCP ban test
finds the privileged tools by name.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.due_items.models import NOTICE_PLAN_SOURCE_KIND, DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.models import TaxNoticePlan
from pigrocrm.core.fiscal.plan_schemas import (
    NoticeInstalmentRead,
    NoticeInstalmentSet,
    TaxInstalmentPlanRead,
    TaxNoticePlanRead,
    TaxNoticePlanSet,
    TaxNoticePlanUpdate,
    TaxPlansRead,
)
from pigrocrm.core.fiscal.repository import TaxInstalmentPlanRepository, TaxNoticePlanRepository
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.money import round_money

ENTITY = "tax_notice_plan"
ZERO = Decimal("0.00")
_SLOT_NAMESPACE = uuid5(NAMESPACE_URL, "pigrocrm:tax-notice-plans")


def notice_slot_id(plan_id: UUID, number: int) -> UUID:
    """The `source_id` of the item of instalment `number` of a notice plan."""
    return uuid5(_SLOT_NAMESPACE, f"{plan_id}|{number}")


def _stored(instalments: list[NoticeInstalmentSet]) -> list[dict[str, Any]]:
    return [
        {"due_date": item.due_date.isoformat(), "interest": str(item.interest)}
        for item in instalments
    ]


def _amounts(total: Decimal, stored: list[dict[str, Any]]) -> list[NoticeInstalmentRead]:
    """What each instalment of a plan costs: the principal split equally, the last taking the
    cent difference, and its interest on top."""
    count = len(stored)
    each = round_money(total / count)
    paid = ZERO
    out: list[NoticeInstalmentRead] = []
    for number, entry in enumerate(stored, start=1):
        principal = total - paid if number == count else each
        paid += principal
        interest = Decimal(entry["interest"])
        out.append(
            NoticeInstalmentRead(
                number=number,
                due_date=dt.date.fromisoformat(entry["due_date"]),
                principal=principal,
                interest=interest,
                amount=principal + interest,
            )
        )
    return out


def _read(row: TaxNoticePlan) -> TaxNoticePlanRead:
    return TaxNoticePlanRead(
        id=row.id,
        legal_entity_id=row.legal_entity_id,
        label=row.label,
        tax=row.tax,
        total=row.total,
        instalments=_amounts(row.total, row.instalments),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _text(label: str, item: NoticeInstalmentRead, count: int) -> str:
    text = f"{label}, rata {item.number} di {count}"
    if item.interest > 0:
        text += f", con {item.interest} euro di interessi".replace(".", ",")
    return text[:255]


class TaxNoticePlanService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = TaxNoticePlanRepository(session)
        self.dues = DueItemService(session)
        self.activities = ActivityService(session)

    def create_tax_notice_plan(
        self,
        data: TaxNoticePlanSet,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
    ) -> TaxNoticePlanRead:
        """Records a notice's plan and writes its instalments as due items. One legal entity's,
        always: it resolves to the only one when none is named and is required, by name, when the
        space has several."""
        actor.require_admin("create_tax_notice_plan")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        row = TaxNoticePlan(
            legal_entity_id=legal_entity.id,
            label=data.label,
            tax=data.tax,
            total=data.total,
            instalments=_stored(data.instalments),
        )
        self._require_splittable(row.total, row.instalments)
        self.repo.add(row)
        self._write_items(row)
        self.activities.record(ENTITY, row.id, "tax_notice_plan_created", actor, {"tax": row.tax})
        self.session.commit()
        self.session.refresh(row)
        return _read(row)

    def update_tax_notice_plan(
        self, plan_id: UUID, changes: TaxNoticePlanUpdate, actor: Actor
    ) -> TaxNoticePlanRead:
        """Changes what is passed; `instalments` replaces the whole list. Refused when a payment
        is recorded on one of its items."""
        actor.require_admin("update_tax_notice_plan")
        row = self._require(plan_id)
        given = changes.model_dump(exclude_unset=True)
        if not given:
            raise ValidationFailed(ENTITY, "changes", "niente da cambiare", expected="un campo")
        if any(given.get(key, True) is None for key in ("label", "tax", "total", "instalments")):
            raise ValidationFailed(
                ENTITY, "changes", "questi campi non si svuotano", expected="un valore"
            )
        self._require_untouched(row)
        label = changes.label if changes.label is not None else row.label
        tax = changes.tax if changes.tax is not None else row.tax
        total = changes.total if changes.total is not None else row.total
        instalments = (
            _stored(changes.instalments) if changes.instalments is not None else row.instalments
        )
        # Checked on the new values, before the old items are dropped.
        self._require_splittable(total, instalments)
        self._drop_items(row)
        row.label, row.tax, row.total, row.instalments = label, tax, total, instalments
        self.session.flush()
        self._write_items(row)
        self.activities.record(ENTITY, row.id, "tax_notice_plan_updated", actor, {"tax": row.tax})
        self.session.commit()
        self.session.refresh(row)
        return _read(row)

    def delete_tax_notice_plan(self, plan_id: UUID, actor: Actor) -> None:
        """Drops the plan's items and removes it. Refused when a payment is recorded on one of
        them: the payment is reversed first."""
        actor.require_admin("delete_tax_notice_plan")
        row = self._require(plan_id)
        self._require_untouched(row)
        self._drop_items(row)
        self.activities.record(ENTITY, row.id, "tax_notice_plan_deleted", actor, {"tax": row.tax})
        self.repo.delete(row)
        self.session.commit()

    def list_tax_plans(self, actor: Actor, *, legal_entity_id: UUID | None = None) -> TaxPlansRead:
        """Every instalment plan and every notice plan of one legal entity: the plans are the
        fiscal position, so an administrator's and, for an agent, `full_access`'s."""
        actor.require_admin("list_tax_plans")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        instalment_plans = TaxInstalmentPlanRepository(self.session).for_entity(legal_entity.id)
        return TaxPlansRead(
            legal_entity_id=legal_entity.id,
            instalment_plans=[
                TaxInstalmentPlanRead.model_validate(row)
                for _, row in sorted(instalment_plans.items())
            ],
            notice_plans=[_read(row) for row in self.repo.for_entity(legal_entity.id)],
        )

    # ---- the items --------------------------------------------------------------------------

    def _require(self, plan_id: UUID) -> TaxNoticePlan:
        row = self.repo.get(plan_id)
        if row is None:
            raise NotFound(ENTITY, plan_id)
        return row

    @staticmethod
    def _require_splittable(total: Decimal, instalments: list[dict[str, Any]]) -> None:
        if any(item.principal <= 0 for item in _amounts(total, instalments)):
            raise ValidationFailed(
                ENTITY,
                "total",
                "il totale e' troppo piccolo per essere diviso in tutte queste rate",
                expected="almeno un centesimo per rata",
            )

    def _live_items(self, row: TaxNoticePlan) -> list[DueItem]:
        live: list[DueItem] = []
        for number in range(1, len(row.instalments) + 1):
            for item in self.dues.repo.items_of_source(
                NOTICE_PLAN_SOURCE_KIND, notice_slot_id(row.id, number)
            ):
                if item.state != "dropped":
                    live.append(item)
        return live

    def _require_untouched(self, row: TaxNoticePlan) -> None:
        for item in self._live_items(row):
            if item.state == "settled" or self.dues.repo.totals(item.id).settled != ZERO:
                raise Conflict(
                    ENTITY,
                    "una rata di questo piano ha pagamenti registrati: il piano non si cambia "
                    "ne' si elimina finche' restano; si annullano prima",
                    id=str(row.id),
                )

    def _drop_items(self, row: TaxNoticePlan) -> None:
        for item in self._live_items(row):
            self.dues.drop(item.id, "piano dell'avviso cambiato o eliminato")

    def _write_items(self, row: TaxNoticePlan) -> None:
        amounts = _amounts(row.total, row.instalments)
        for item in amounts:
            self.dues.create(
                DueItemCreate(
                    legal_entity_id=row.legal_entity_id,
                    direction="out",
                    counterparty_type="tax_office",
                    tax=row.tax,
                    amount=item.amount,
                    description=_text(row.label, item, len(amounts)),
                    state="expected",
                    source_kind=NOTICE_PLAN_SOURCE_KIND,
                    source_id=notice_slot_id(row.id, item.number),
                    own_date=item.due_date,
                )
            )
