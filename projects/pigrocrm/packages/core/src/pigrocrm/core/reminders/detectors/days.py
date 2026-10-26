"""The four reminders about a contract's worked days and their approvals (spec 2026-10-26
§3.4, REB-768): `worked_without_approval`, `approval_unanswered`,
`approval_requirement_unmet` and `work_unit_outside_term`. The subject of each is the day
(`work_unit`), the thing somebody has to open and fix; recipients are the owner of the
contract's customer (`links.py`), scoped to the contract's azienda.

**What «approval» is in this schema.** A contract says one thing, `requires_prior_approval`;
an approval (`approvals`) is a received written approval with a `ricevuto_il`, linked from
the days it covers; a day moves to `lavorato_senza_approvazione` on its own when it is worked
on a requiring contract with none linked (`work_units/triggers.py`). The rest of Mastro's
approval clause (a coverage period, a scope, who may approve) was not ported, so:

- `worked_without_approval`: days in `lavorato_senza_approvazione` on a contract that still
  requires approvals. The measure is days since the day was worked (`data`): `serious` at
  once, `critical` from 3 days.
- `approval_unanswered`: days still `proposto`, with no approval linked, on a requiring
  contract: the approval has been asked for, by proposing the day, and nobody has answered.
  The measure is days since the day was proposed (its creation, in the installation's zone).
- `approval_requirement_unmet`: the one requirement the data can check, that the approval is
  **prior**: a day linked to an approval received after the day, or to an approval of another
  contract. Measure: days since the day.
- `work_unit_outside_term`: a day before the contract's `inizio` or after its `fine`.
  Measure: days since the day. It reads the contract's current term on every pass, so
  correcting the term clears it.

Only days that count are read: `rifiutato`, `revocato` and `non_fatturabile` are out of every
type, and `approval_unanswered` reads `proposto` alone. The coverage reminder
(`approval_coverage_expiring`) has no data behind it and is not built.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Date, Select, cast, exists, func, or_, select

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.mail import giorno_breve
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.links import LinkResolver
from pigrocrm.core.work_units.models import Approval, WorkUnit

WITHOUT_APPROVAL = REMINDER_TYPES["worked_without_approval"]
UNANSWERED = REMINDER_TYPES["approval_unanswered"]
UNMET = REMINDER_TYPES["approval_requirement_unmet"]
OUTSIDE_TERM = REMINDER_TYPES["work_unit_outside_term"]

# Days that are not a day worked or promised: refused, withdrawn, or written off.
_DEAD = ("rifiutato", "revocato", "non_fatturabile")


def _requiring(ctx: DetectorContext) -> bool:
    """«Applies when» of the approval types: the space has a live contract that requires
    approvals."""
    return bool(
        ctx.session.scalar(
            select(
                exists().where(
                    Contract.deleted_at.is_(None), Contract.requires_prior_approval.is_(True)
                )
            )
        )
    )


def _days(count: int) -> str:
    return "1 giorno" if count == 1 else f"{count} giorni"


def _rows(
    ctx: DetectorContext, query: Select[WorkUnit, Contract], *, day_cutoff: date | None
) -> list[tuple[WorkUnit, Contract]]:
    """Days with their contract, the contract not archived, the day no later than
    `day_cutoff` when given (the loosest rung of the ladder, never fewer than the day)."""
    stmt = query.join(Contract, Contract.id == WorkUnit.contract_id).where(
        Contract.deleted_at.is_(None), WorkUnit.stato.not_in(_DEAD)
    )
    if day_cutoff is not None:
        stmt = stmt.where(WorkUnit.data <= day_cutoff)
    return [
        (unit, contract)
        for unit, contract in ctx.session.execute(stmt.order_by(WorkUnit.data, WorkUnit.id))
    ]


def _detect(
    ctx: DetectorContext,
    rows: list[tuple[WorkUnit, Contract]],
    *,
    type_key: str,
    measure: Any,
    title: str,
    detail: Any,
) -> list[Detection]:
    resolver = LinkResolver(ctx.session)
    resolver.preload(customers=(contract.customer_id for _, contract in rows))
    found: list[Detection] = []
    for unit, contract in rows:
        links = resolver.resolve(customer_id=contract.customer_id)
        customer = f" · {links.customer_name}" if links.customer_name else ""
        found.append(
            Detection(
                type=type_key,
                subject_id=unit.id,
                measure=float(measure(unit)),
                recipients=ctx.directory.recipients(
                    (links.customer_owner_id,), contract.azienda_id
                ),
                title=f"{title}: {contract.titolo}, {giorno_breve(unit.data)}",
                detail=f"{detail(unit, contract)}{customer}",
                # The day has no page of its own: the reminder opens its contract.
                entity="contract",
                entity_id=contract.id,
                azienda_id=contract.azienda_id,
                customer_id=contract.customer_id,
            )
        )
    return found


def _since_the_day(ctx: DetectorContext, unit: WorkUnit) -> int:
    return max((ctx.today - unit.data).days, 0)


def detect_worked_without_approval(ctx: DetectorContext) -> list[Detection]:
    rows = _rows(
        ctx,
        select(WorkUnit, Contract).where(
            WorkUnit.stato == "lavorato_senza_approvazione",
            Contract.requires_prior_approval.is_(True),
        ),
        day_cutoff=ctx.today - timedelta(days=max(ctx.bound(WITHOUT_APPROVAL), 0)),
    )
    return _detect(
        ctx,
        rows,
        type_key="worked_without_approval",
        measure=lambda unit: _since_the_day(ctx, unit),
        title="Giornata senza approvazione",
        detail=lambda unit, contract: (
            f"Lavorata {_ago(_since_the_day(ctx, unit))} su un contratto che richiede "
            "l'approvazione preventiva e non ne ha una"
        ),
    )


def _ago(days: int) -> str:
    return "oggi" if days == 0 else "ieri" if days == 1 else f"{days} giorni fa"


def _proposed_before(ctx: DetectorContext, bound: int) -> datetime:
    """The first instant that is too recent for a day proposed `bound` days ago: the start
    of the local day after `today - bound`."""
    start = datetime.combine(
        ctx.today - timedelta(days=bound - 1), time.min, tzinfo=ZoneInfo(ctx.timezone)
    )
    return start.astimezone(UTC)


def _proposed_days(ctx: DetectorContext, unit: WorkUnit) -> int:
    local = unit.created_at.astimezone(ZoneInfo(ctx.timezone)).date()
    return max((ctx.today - local).days, 0)


def detect_approval_unanswered(ctx: DetectorContext) -> list[Detection]:
    cutoff = _proposed_before(ctx, max(ctx.bound(UNANSWERED), 0))
    stmt = select(WorkUnit, Contract).where(
        WorkUnit.stato == "proposto",
        WorkUnit.approval_id.is_(None),
        Contract.requires_prior_approval.is_(True),
        WorkUnit.created_at < cutoff,
    )
    return _detect(
        ctx,
        _rows(ctx, stmt, day_cutoff=None),
        type_key="approval_unanswered",
        measure=lambda unit: _proposed_days(ctx, unit),
        title="Approvazione senza risposta",
        detail=lambda unit, contract: (
            f"Giornata proposta {_ago(_proposed_days(ctx, unit))}, ancora in attesa "
            "dell'approvazione del cliente"
        ),
    )


def _received_day(zone: str) -> Any:
    return cast(func.timezone(zone, Approval.ricevuto_il), Date)


def detect_requirement_unmet(ctx: DetectorContext) -> list[Detection]:
    stmt = (
        select(WorkUnit, Contract)
        .join(Approval, Approval.id == WorkUnit.approval_id)
        .where(
            Contract.requires_prior_approval.is_(True),
            or_(
                Approval.contract_id != WorkUnit.contract_id,
                _received_day(ctx.timezone) > WorkUnit.data,
            ),
        )
    )
    rows = _rows(ctx, stmt, day_cutoff=ctx.today - timedelta(days=max(ctx.bound(UNMET), 0)))
    approvals = {
        approval.id: approval
        for approval in ctx.session.scalars(
            select(Approval).where(Approval.id.in_({unit.approval_id for unit, _ in rows}))
        )
    }

    def detail(unit: WorkUnit, contract: Contract) -> str:
        assert unit.approval_id is not None
        approval = approvals[unit.approval_id]
        if approval.contract_id != unit.contract_id:
            return "La giornata poggia su un'approvazione data per un altro contratto"
        received = approval.ricevuto_il.astimezone(ZoneInfo(ctx.timezone)).date()
        return (
            f"L'approvazione è arrivata {giorno_breve(received)}, dopo la giornata: "
            "il contratto la vuole preventiva"
        )

    return _detect(
        ctx,
        rows,
        type_key="approval_requirement_unmet",
        measure=lambda unit: _since_the_day(ctx, unit),
        title="Approvazione non conforme",
        detail=detail,
    )


def detect_outside_term(ctx: DetectorContext) -> list[Detection]:
    stmt = select(WorkUnit, Contract).where(
        or_(
            WorkUnit.data < Contract.inizio,
            (Contract.fine.is_not(None)) & (WorkUnit.data > Contract.fine),
        )
    )
    rows = _rows(ctx, stmt, day_cutoff=ctx.today - timedelta(days=max(ctx.bound(OUTSIDE_TERM), 0)))

    def detail(unit: WorkUnit, contract: Contract) -> str:
        if unit.data < contract.inizio:
            return f"Il contratto inizia {giorno_breve(contract.inizio)}, la giornata è prima"
        assert contract.fine is not None
        return f"Il contratto finisce {giorno_breve(contract.fine)}, la giornata è dopo"

    return _detect(
        ctx,
        rows,
        type_key="work_unit_outside_term",
        measure=lambda unit: _since_the_day(ctx, unit),
        title="Giornata fuori dal contratto",
        detail=detail,
    )


DETECTORS = (
    Detector(WITHOUT_APPROVAL, detect_worked_without_approval, applies=_requiring),
    Detector(UNANSWERED, detect_approval_unanswered, applies=_requiring),
    Detector(UNMET, detect_requirement_unmet, applies=_requiring),
    Detector(OUTSIDE_TERM, detect_outside_term),
)
