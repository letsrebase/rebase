"""`contract_expiring` and `renewal_window_open` (spec 2026-10-26 §3.4, REB-768).

A contract counts while it is not archived, is not `terminato` or `scaduto`
(`ContractService.set_state`; `bozza` and `attivo` both count, a draft with a date still
ends) and has not ended (`fine` not before today): a contract somebody closed, or let lapse,
is not «expiring» and its renewal is not a decision any more. The billing types read the
same contracts differently and keep counting the terminated ones.

**The two types are independent.** Mastro stepped one aside for the other once the renewal
decision was due (#702); the spec lists two types with their own ladders and their own
switches, so each is raised on its own measure and a person who wants one calm turns that
one off.

**`renewal_window_open`'s window closes on `renewal_deadline`** (`contracts/dates.py`: the
last day the renewal clause can still be exercised, `preavviso_rinnovo_giorni` before the
end) and the measure is the days left before it, an `until` ladder like `contract_expiring`.
The data does not say when the window *opens*, so it is not derived: the ladder's `warning`
rung is a lead time, the days before the deadline from which the reminder starts (30 by
default), which the space and each person can move. A
contract with no renewal clause or no end date has no deadline and is not raised; one whose
deadline has passed has no window left and is not either. `irrevocability_window_end` is
about the notice a termination would serve, not a date to act by, and is not read.

Recipients are the owner of the customer (`links.py`), scoped to the contract's azienda.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import Select, exists, select

from pigrocrm.core.contracts.dates import renewal_deadline
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.mail import giorno_breve
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.links import LinkResolver

EXPIRING = REMINDER_TYPES["contract_expiring"]
RENEWAL = REMINDER_TYPES["renewal_window_open"]


# A contract closed by hand or lapsed: nothing is left to expire or renew.
_OVER = ("terminato", "scaduto")


def _live() -> Select[Contract]:
    return select(Contract).where(Contract.deleted_at.is_(None), Contract.stato.not_in(_OVER))


def _has_end_date(ctx: DetectorContext) -> bool:
    return bool(
        ctx.session.scalar(
            select(
                exists().where(
                    Contract.deleted_at.is_(None),
                    Contract.stato.not_in(_OVER),
                    Contract.fine.is_not(None),
                )
            )
        )
    )


def _has_renewal_clause(ctx: DetectorContext) -> bool:
    return bool(
        ctx.session.scalar(
            select(
                exists().where(
                    Contract.deleted_at.is_(None),
                    Contract.stato.not_in(_OVER),
                    Contract.fine.is_not(None),
                    Contract.tipo_rinnovo != "nessuno",
                )
            )
        )
    )


def giorni(count: int) -> str:
    """`1 giorno`, `20 giorni`: the Italian plural, so no detail ever reads «1 giorni»."""
    return "1 giorno" if count == 1 else f"{count} giorni"


def _detection(
    ctx: DetectorContext,
    resolver: LinkResolver,
    contract: Contract,
    *,
    type_key: str,
    measure: int,
    title: str,
    detail: str,
) -> Detection:
    links = resolver.resolve(customer_id=contract.customer_id)
    customer = f" · {links.customer_name}" if links.customer_name else ""
    return Detection(
        type=type_key,
        subject_id=contract.id,
        measure=float(measure),
        recipients=ctx.directory.recipients((links.customer_owner_id,), contract.azienda_id),
        title=title,
        detail=f"{detail}{customer}",
        entity="contract",
        entity_id=contract.id,
        azienda_id=contract.azienda_id,
        customer_id=contract.customer_id,
    )


def _resolver(ctx: DetectorContext, contracts: list[Contract]) -> LinkResolver:
    resolver = LinkResolver(ctx.session)
    resolver.preload(customers=(contract.customer_id for contract in contracts))
    return resolver


def detect_expiring(ctx: DetectorContext) -> list[Detection]:
    """Contracts ending today or within `bound` days; one that ended yesterday is over."""
    horizon = ctx.today + timedelta(days=ctx.bound(EXPIRING))
    contracts = list(
        ctx.session.scalars(
            _live()
            .where(Contract.fine.is_not(None), Contract.fine >= ctx.today, Contract.fine <= horizon)
            .order_by(Contract.fine, Contract.id)
        )
    )
    resolver = _resolver(ctx, contracts)
    found: list[Detection] = []
    for contract in contracts:
        assert contract.fine is not None
        left = (contract.fine - ctx.today).days
        when = "oggi" if left == 0 else f"fra {giorni(left)}"
        found.append(
            _detection(
                ctx,
                resolver,
                contract,
                type_key="contract_expiring",
                measure=left,
                title=f"Contratto in scadenza: {contract.titolo}",
                detail=f"Finisce {giorno_breve(contract.fine)}, {when}",
            )
        )
    return found


def _deadlines(
    contracts: list[Contract], today: date, horizon: date
) -> list[tuple[Contract, date]]:
    found = [
        (contract, deadline)
        for contract in contracts
        if (deadline := renewal_deadline(contract)) is not None and today <= deadline <= horizon
    ]
    return sorted(found, key=lambda pair: (pair[1], pair[0].id))


def detect_renewal_window(ctx: DetectorContext) -> list[Detection]:
    """Contracts whose renewal deadline is today or within `bound` days.

    `renewal_deadline` is a function of two columns, so the rows are narrowed in SQL by the
    only thing every deadline implies (the contract has not ended and a renewal rule is
    set) and the date arithmetic is the library's, not a second copy of it in a query."""
    horizon = ctx.today + timedelta(days=ctx.bound(RENEWAL))
    contracts = list(
        ctx.session.scalars(
            _live().where(
                Contract.fine.is_not(None),
                Contract.fine >= ctx.today,
                Contract.tipo_rinnovo != "nessuno",
            )
        )
    )
    pairs = _deadlines(contracts, ctx.today, horizon)
    resolver = _resolver(ctx, [contract for contract, _ in pairs])
    found: list[Detection] = []
    for contract, deadline in pairs:
        assert contract.fine is not None
        left = (deadline - ctx.today).days
        when = "oggi" if left == 0 else f"fra {giorni(left)}"
        found.append(
            _detection(
                ctx,
                resolver,
                contract,
                type_key="renewal_window_open",
                measure=left,
                title=f"Termine di rinnovo: {contract.titolo}",
                detail=(
                    f"Il termine per il rinnovo è {giorno_breve(deadline)}, {when} "
                    f"(il contratto finisce {giorno_breve(contract.fine)})"
                ),
            )
        )
    return found


DETECTORS = (
    Detector(EXPIRING, detect_expiring, applies=_has_end_date),
    Detector(RENEWAL, detect_renewal_window, applies=_has_renewal_clause),
)
