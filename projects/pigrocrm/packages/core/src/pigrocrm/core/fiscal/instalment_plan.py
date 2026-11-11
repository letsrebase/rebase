"""The only writer of `tax_instalment_plans` (REB-1075, taxes design § 2 and § 5).

An administrator chooses, for a legal entity, a tax year and one income tax (or every income tax of
the year together), in how many monthly instalments the June balance of that tax year and the
first advance of the next one are paid. The November advance is never split. The law is the
pack's: which payments may be split, the last day (16 December of the year the return is filed),
the day of each instalment and the interest it carries (`payment_rules.instalment_schedule`,
`plan_math.py`). The plan only stores the number: the dates and the interest are never copied.

Choosing recomputes the legal entity's tax due items in the same transaction
(`TaxDueItemService`), which write the instalments in place of the single items and answer what
they could not write and why. **Changing or removing a plan is refused once a payment, even a
part of one, is recorded on one of its instalments** (the boring option: the paid ones cannot be
moved without rewriting what was paid, and the unpaid ones are not re-split around them). The
payment is reversed first, or the plan is left as it is; choosing the number already stored is
not a change. A payment made in one go before a plan was chosen is left as it is and the plan
does not apply to it.

The service asks for an administrator first, before it looks anything up; an agent credential
passes only with `full_access` (`agent_actions.py`). Method names are unique across the services
on purpose: `FORBIDDEN_SERVICE_CALLS` of the MCP ban test finds the privileged tools by name.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.advances import INCOME_TAXES, Missing, balance_date
from pigrocrm.core.fiscal.law import DeclaredTax
from pigrocrm.core.fiscal.pack import FiscalPack, resolve_pack
from pigrocrm.core.fiscal.plan_math import plan_schedule
from pigrocrm.core.fiscal.plan_schemas import (
    TaxInstalmentPlanRead,
    TaxInstalmentPlanResult,
    TaxInstalmentPlanSet,
)
from pigrocrm.core.fiscal.repository import FiscalProfileRepository, TaxInstalmentPlanRepository
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.service import LegalEntityService

ENTITY = "tax_instalment_plan"


class TaxInstalmentPlanService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = TaxInstalmentPlanRepository(session)
        self.activities = ActivityService(session)

    def set_tax_instalment_plan(
        self,
        data: TaxInstalmentPlanSet,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
    ) -> TaxInstalmentPlanResult:
        """Splits the June payments of `data.year` in `data.instalments` instalments, replacing
        a plan chosen before; one instalment removes it. One legal entity's, always: it resolves
        to the only one when none is named and is required, by name, when the space has several."""
        actor.require_admin("set_tax_instalment_plan")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        # The row the refresh locks, taken before the checks below read what a payment may be
        # changing: two writers of the legal entity's tax items queue instead of racing.
        self.session.refresh(legal_entity, with_for_update=True)
        profile = FiscalProfileRepository(self.session).get(legal_entity.id)
        if profile is None:
            # As `get_fiscal_estimate` answers: nothing is computed without a profile.
            raise NotFound("fiscal_profile", legal_entity.id)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        law = pack.law_for(data.year)
        taxes: list[DeclaredTax] = [
            t for t in (law.taxes if law is not None else ()) if t in INCOME_TAXES
        ]
        if data.tax is not None:
            if data.tax not in taxes:
                raise ValidationFailed(
                    ENTITY,
                    "tax",
                    f"{data.tax} non e' un'imposta sul reddito di questa azienda nel {data.year}",
                    expected=", ".join(taxes) or "nessuna",
                )
            taxes = [data.tax]
        if not taxes:
            raise ValidationFailed(
                ENTITY,
                "year",
                "per questo anno non ci sono imposte sul reddito",
                expected="un anno",
            )
        stored = self.repo.for_entity(legal_entity.id)
        items = TaxDueItemService(self.session)
        # Every check first, so a refusal leaves nothing half done.
        for tax in taxes:
            existing = stored.get((tax, data.year))
            changed = existing is not None and existing.instalments != data.instalments
            if changed and items.instalments_touched(legal_entity.id, tax, data.year):
                raise Conflict(
                    ENTITY,
                    "una rata di questo piano ha pagamenti registrati: il piano non si "
                    "cambia ne' si toglie finche' restano; si annullano prima",
                    tax=tax,
                    year=data.year,
                )
            if data.instalments > 1:
                self._require_allowed(legal_entity, pack, tax, data)
        before = {key[0]: row.instalments for key, row in stored.items() if key[1] == data.year}
        written: list[tuple[UUID, str]] = []
        for tax in taxes:
            if data.instalments == 1:
                removed = self.repo.delete(legal_entity.id, tax, data.year)
                if removed is not None:
                    written.append((removed.id, tax))
            else:
                row = self.repo.save(legal_entity.id, tax, data.year, data.instalments)
                if before.get(tax) != data.instalments:
                    written.append((row.id, tax))
        report = items.refresh_tax_due_items(legal_entity.id)
        for row_id, tax_name in written:
            # Who chose and when, for which tax and year; never an amount.
            self.activities.record(
                ENTITY,
                row_id,
                "tax_instalment_plan_set",
                actor,
                {"year": data.year, "tax": tax_name, "instalments": data.instalments},
            )
        self.session.commit()
        plans = [
            TaxInstalmentPlanRead.model_validate(row)
            for (_, year), row in sorted(self.repo.for_entity(legal_entity.id).items())
            if year == data.year
        ]
        return TaxInstalmentPlanResult(
            legal_entity_id=legal_entity.id, year=data.year, plans=plans, items=report
        )

    def _require_allowed(
        self,
        legal_entity: LegalEntity,
        pack: FiscalPack,
        tax: DeclaredTax,
        data: TaxInstalmentPlanSet,
    ) -> None:
        """Refuses a number of instalments the law does not allow from the balance's day."""
        year_end = (legal_entity.fiscal_year_end_month, legal_entity.fiscal_year_end_day)
        due = balance_date(pack, pack.law_for(data.year), tax, data.year, *year_end)
        if isinstance(due, Missing):
            raise ValidationFailed(
                ENTITY, "year", f"non c'e' la data del saldo di questo anno: {due.reason}"
            )
        schedule = plan_schedule(pack, tax, data.year, year_end, "balance", due)
        if isinstance(schedule, Missing):
            raise ValidationFailed(
                ENTITY, "tax", f"questa imposta non si rateizza: {schedule.reason}"
            )
        if len(schedule) < max(data.instalments, 2):
            raise ValidationFailed(
                ENTITY,
                "instalments",
                (
                    f"dal {due.isoformat()} la legge consente al massimo {len(schedule)} rate"
                    if len(schedule) > 1
                    else f"dal {due.isoformat()} la legge non consente di rateizzare"
                ),
                expected=f"da 2 a {len(schedule)}" if len(schedule) > 1 else "nessuna rata",
            )
