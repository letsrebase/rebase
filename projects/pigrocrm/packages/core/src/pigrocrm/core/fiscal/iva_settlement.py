"""The periodic IVA settlement and the due item it writes (REB-1021, suppliers design § 6).

Per period, for a legal entity on a pack with a periodic IVA (`FiscalPack.has_periodic_iva`,
`it-standard`):

    own balance = IVA due on the sales of the period
                + IVA the buyer owes on its reverse-charge purchases
                - the deductible IVA on the purchases registered in the period
                - the credit the previous period left
    result      = own balance + the 1% a quarterly settlement adds to a debit
                + the small debit the previous period left to be paid now

A positive result is paid to the tax office on the date of the pack's calendar and becomes a
due item (`source_kind` `iva_settlement`, tax `iva`, direction out), unless it does not
exceed the law's limit and the period may wait: then it is carried into the next period's
payment. A negative result is a credit carried forward: the next period's settlement takes it in
(`credit_in`, D.P.R. 100/1998 art. 1 c. 1 and c. 4), so it is never a due item and never a credit
line of an F24 (REB-1100: the Agenzia's monthly and quarterly codes say «importi a credito
compensati: NON COMPILARE»; a credit reaches an F24 only as a compensation a person makes, with
6099 or 6036-6038). Every figure of the law is the pack's
(`IvaSettlementLaw`), every date its calendar's: nothing here is a number of the statute.

Which period a document is in: a purchase by the `iva_period` a person chose; a sale by its
issue date. The sales register is kept by the month of the *operation* (art. 23 DPR 633/72 until
31 December 2026, art. 86 c. 1 of the IVA testo unico, D.Lgs. 10/2026, from 1 January 2027: the
same words), and the invoice's date is the date of the operation, whatever day the file is sent
to the SdI (Agenzia delle Entrate, circolare 14/E of 17 June 2019 § 3.1: «la data del documento
dovrà sempre essere valorizzata con la data dell'operazione»; A5 «IVA period date for sales»).
So `sales_conditions` places a sale by `Invoice.issue_date`, which the e-invoice's `Data` carries
too, and no operation date is stored. The one care is the person's: an invoice that covers several
operations on different dates is not one operation, and the invoice header's help says that the
date is the operation's (the MCP tool text has no room for it: the tool list is at its budget).

A settlement is recomputed whenever it is settled again, from the first period ever settled
through the target, so the carry-over is never stale; a year keeps the frequency it was first
settled in, and a new one starts the year after. Its due item follows while nothing was paid on
it; a payment already recorded freezes the item, and a recomputation that would change it is
refused. A year before the first one settled is never invented: a carry-over starts at the first
settled period.
"""

import datetime as dt
from calendar import monthrange
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.calendar import CalendarDate, tax_calendar
from pigrocrm.core.fiscal.law import IvaSettlementLaw
from pigrocrm.core.fiscal.models import FiscalProfile, IvaSettlement
from pigrocrm.core.fiscal.pack import FiscalPack, resolve_pack
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import IvaPeriod, IvaSettlementRead
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.money import ZERO_MONEY, round_money, sum_money
from pigrocrm.core.purchases.models import VAT_KINDS, Purchase, PurchaseVatLine

ENTITY = "iva_settlement"
SOURCE_KIND = "iva_settlement"
TAX = "iva"
_HUNDRED = Decimal("100")
_MONTHS_PER_QUARTER = 3
_MONTHS_PER_YEAR = 12
_QUARTERS = 4


@dataclass(frozen=True)
class Bounds:
    start: dt.date
    end: dt.date


@dataclass(frozen=True)
class Figures:
    """What a period's settlement came to, from the register totals and the carry-over."""

    surcharge: Decimal
    result: Decimal
    outcome: str


def period_bounds(frequency: str, year: int, number: int) -> Bounds:
    """The first and last day of month `number` or of quarter `number` of `year`."""
    if frequency == "monthly":
        first_month = last_month = number
    else:
        last_month = number * _MONTHS_PER_QUARTER
        first_month = last_month - _MONTHS_PER_QUARTER + 1
    return Bounds(
        dt.date(year, first_month, 1), dt.date(year, last_month, monthrange(year, last_month)[1])
    )


def compute_figures(
    law: IvaSettlementLaw,
    *,
    frequency: str,
    number: int,
    sales_vat: Decimal,
    reverse_charge_vat: Decimal,
    purchase_vat: Decimal,
    credit_in: Decimal,
    debit_in: Decimal,
) -> Figures:
    """The surcharge, the signed result and what it comes to, for one period. Pure."""
    own = sales_vat + reverse_charge_vat - purchase_vat - credit_in
    surcharge = ZERO_MONEY
    if frequency == "quarterly" and own > 0 and number in law.quarterly_surcharge_periods:
        surcharge = round_money(own * law.quarterly_surcharge_percent / _HUNDRED)
    result = round_money(own + surcharge + debit_in)
    carry_periods = (
        law.monthly_carry_periods if frequency == "monthly" else law.quarterly_carry_periods
    )
    if result < 0:
        outcome = "credit"
    elif result == 0:
        outcome = "nil"
    elif number in carry_periods and result <= law.carry_forward_limit:
        outcome = "carried"
    else:
        outcome = "due"
    return Figures(surcharge, result, outcome)


def carry_in(previous: IvaSettlement | None) -> tuple[Decimal, Decimal]:
    """`(credit, small debit)` the previous period leaves to this one."""
    credit = (
        -previous.result if previous is not None and previous.outcome == "credit" else ZERO_MONEY
    )
    debit = (
        previous.result if previous is not None and previous.outcome == "carried" else ZERO_MONEY
    )
    return credit, debit


def deductible_vat(vat: Decimal, share: Decimal) -> Decimal:
    """The part of one purchase line's VAT the register deducts."""
    return round_money(vat * share / _HUNDRED)


def sales_conditions(legal_entity_id: UUID, bounds: Bounds) -> list[ColumnElement[bool]]:
    """Which invoices are the sales register of a period: the legal entity's invoices (never a
    proforma) dated in it, a sale being placed by its issue date and not its operation date
    (module docstring). The status is the caller's: the settlement counts the issued ones, the
    register also lists the annulled numbers."""
    return [
        Invoice.legal_entity_id == legal_entity_id,
        Invoice.type == "invoice",
        Invoice.deleted_at.is_(None),
        Invoice.issue_date >= bounds.start,
        Invoice.issue_date <= bounds.end,
    ]


def purchase_conditions(legal_entity_id: UUID, bounds: Bounds) -> list[ColumnElement[bool]]:
    """Which purchases are the purchase register of a period: the legal entity's registered
    invoices and credit notes whose `iva_period` falls in it."""
    return [
        Purchase.legal_entity_id == legal_entity_id,
        Purchase.kind.in_(VAT_KINDS),
        Purchase.status == "registered",
        Purchase.deleted_at.is_(None),
        Purchase.iva_period >= bounds.start,
        Purchase.iva_period <= bounds.end,
    ]


def sales_vat(session: Session, legal_entity_id: UUID, bounds: Bounds) -> Decimal:
    """The IVA due on the sales issued in the period. A sale under split payment owes none: the
    buyer pays it to the tax office itself (art. 17-ter c. 1 DPR 633/72, A5)."""
    rows = session.execute(
        select(Invoice.vat_amount).where(
            *sales_conditions(legal_entity_id, bounds),
            Invoice.status == "issued",
            Invoice.split_payment.is_(False),
        )
    ).scalars()
    return sum_money(rows)


def purchase_vat(
    session: Session, legal_entity_id: UUID, bounds: Bounds
) -> tuple[Decimal, Decimal]:
    """`(reverse-charge VAT owed, deductible VAT)` of the purchases registered in the period,
    each line at its deductible share."""
    rows = session.execute(
        select(
            Purchase.reverse_charge, PurchaseVatLine.vat_amount, PurchaseVatLine.deductible_share
        )
        .join(PurchaseVatLine, PurchaseVatLine.purchase_id == Purchase.id)
        .where(*purchase_conditions(legal_entity_id, bounds))
    ).all()
    owed = sum_money(vat for reverse, vat, _ in rows if reverse)
    deductible = sum_money(deductible_vat(vat, share) for _, vat, share in rows)
    return owed, deductible


def payment_date(pack: FiscalPack, frequency: str, year: int, number: int) -> CalendarDate | None:
    """The calendar date a period is paid on: the pack's, moved off a non-working day. A
    month is paid in the next one (December's in January), the first three quarters on the
    quarterly dates in order, the fourth with the annual settlement the year after."""
    if frequency == "monthly":
        pay_year, pay_month = (year + 1, 1) if number == _MONTHS_PER_YEAR else (year, number + 1)
        wanted = [
            entry
            for entry in tax_calendar(pack, pay_year) or []
            if entry.tax == TAX
            and entry.kind == "periodic_payment"
            and entry.cadence == "monthly"
            and entry.nominal_date.month == pay_month
        ]
        return wanted[0] if wanted else None
    if number == _QUARTERS:
        annual = [
            entry
            for entry in tax_calendar(pack, year + 1) or []
            if entry.tax == TAX and entry.kind == "annual_balance" and entry.cadence == "quarterly"
        ]
        return annual[0] if annual else None
    quarterly = [
        entry
        for entry in tax_calendar(pack, year) or []
        if entry.tax == TAX and entry.kind == "quarterly_payment" and entry.cadence == "quarterly"
    ]
    return quarterly[number - 1] if number <= len(quarterly) else None


def annual_return_date(pack: FiscalPack, year: int) -> dt.date | None:
    """The day the annual IVA settlement is paid in `year` (the pack's `annual_balance`, after a
    non-working day moves it): the last day the previous year's periods are kept in the daily
    pass's window."""
    for entry in tax_calendar(pack, year) or []:
        if entry.tax == TAX and entry.kind == "annual_balance":
            return entry.due_date
    return None


def label(frequency: str, year: int, number: int) -> str:
    """Words for a period, in the product's language."""
    if frequency == "monthly":
        return f"{_MONTHS_IT[number - 1]} {year}"
    return f"{number}° trimestre {year}"


_MONTHS_IT = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)


class IvaSettlementService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.profiles = FiscalProfileRepository(session)
        self.due_items = DueItemService(session)
        self.activities = ActivityService(session)
        # The periods the last lenient run left alone because a payment froze their item.
        self.frozen: list[str] = []

    def settle_iva_period(
        self,
        period: IvaPeriod,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
        commit: bool = True,
    ) -> IvaSettlementRead | None:
        """Computes the settlement of `period` (a month or a quarter by the legal entity's
        frequency) with every earlier period back to the first one settled, stores them and
        writes the due item of each that comes to a payment. `None` when the entity's pack has
        no periodic IVA (a flat-rate entity has none). An administrator's act: the figures are
        the fiscal position. `commit=False` leaves the commit to the caller, which owns the
        transaction the settlement is one step of (closing the period, REB-1036)."""
        actor.require_admin("settle_iva_period")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        profile = self._profile(legal_entity.id, legal_entity.name)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        law = pack.law_for(period.year)
        if law is None or law.iva is None:
            return None
        last = _QUARTERS if profile.iva_frequency == "quarterly" else _MONTHS_PER_YEAR
        if period.number > last:
            raise ValidationFailed(
                ENTITY,
                "number",
                "il periodo non esiste: l'azienda liquida "
                + ("per trimestre" if profile.iva_frequency == "quarterly" else "per mese"),
                expected=f"un numero da 1 a {last}",
            )
        if period_bounds(profile.iva_frequency, period.year, period.number).end >= today_local():
            raise ValidationFailed(
                ENTITY,
                "number",
                "il periodo non e' ancora finito: si liquida dopo l'ultimo giorno",
                expected="un periodo gia' concluso",
            )
        self._lock(profile)
        target = self._chain(
            legal_entity.id, profile, pack, period, from_year=period.year, lenient=False
        )
        assert target is not None  # noqa: S101 - the target year has a law and a period
        # Who settled it and when; never the amounts, which are the fiscal position.
        self.activities.record(ENTITY, target.id, "iva_period_settled", actor)
        if commit:
            self.session.commit()
            self.session.refresh(target)
        return self._read(target)

    def settle_elapsed_periods(
        self, legal_entity_id: UUID, today: dt.date
    ) -> tuple[bool, list[str]]:
        """The daily pass's step for one legal entity (no actor, no commit): settles the latest
        period of the current year that has ended, which recomputes every earlier one, and
        keeps the previous year in the window until the annual return's date. Idempotent.
        Answers `(settled, frozen)`: whether anything was settled and the periods whose due
        item has a payment recorded and would change, which are left as they are and reported
        and not raised. A pack with no periodic IVA settles nothing."""
        profile = self._profile(legal_entity_id, str(legal_entity_id))
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        if not pack.has_periodic_iva:
            return False, []
        frequency = profile.iva_frequency
        deadline = annual_return_date(pack, today.year)
        include_previous = deadline is not None and today <= deadline
        if frequency == "monthly":
            elapsed = today.month - 1
        else:
            elapsed = (today.month - 1) // _MONTHS_PER_QUARTER
        if elapsed >= 1:
            target = IvaPeriod(year=today.year, number=elapsed)
        elif include_previous:
            target = IvaPeriod(
                year=today.year - 1,
                number=_QUARTERS if frequency == "quarterly" else _MONTHS_PER_YEAR,
            )
        else:
            return False, []
        law = pack.law_for(target.year)
        if law is None or law.iva is None:
            return False, []
        self._lock(profile)
        self.frozen = []
        settled = self._chain(
            legal_entity_id,
            profile,
            pack,
            target,
            from_year=today.year - 1 if include_previous else today.year,
            lenient=True,
        )
        return settled is not None, self.frozen

    def list_iva_settlements(
        self, year: int, actor: Actor, *, legal_entity_id: UUID | None = None
    ) -> list[IvaSettlementRead]:
        """The settlements stored for one legal entity's year, in period order. An
        administrator's read: the figures are the fiscal position. Computes and writes
        nothing."""
        actor.require_admin("list_iva_settlements")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        rows = self.session.scalars(
            select(IvaSettlement)
            .where(IvaSettlement.legal_entity_id == legal_entity.id, IvaSettlement.year == year)
            .order_by(IvaSettlement.period_start, IvaSettlement.frequency)
        )
        return [self._read(row) for row in rows]

    # ---- the chain --------------------------------------------------------------------------

    def _profile(self, legal_entity_id: UUID, name: str) -> FiscalProfile:
        profile = self.profiles.get(legal_entity_id)
        if profile is None:
            raise NotFound(
                "fiscal_profile",
                str(legal_entity_id),
                message=(
                    f"l'azienda {name} non ha un profilo fiscale: "
                    "compilalo in Impostazioni, Aziende"
                ),
            )
        return profile

    def _chain(
        self,
        legal_entity_id: UUID,
        profile: FiscalProfile,
        pack: FiscalPack,
        period: IvaPeriod,
        *,
        from_year: int,
        lenient: bool,
    ) -> IvaSettlement | None:
        """Every year from the first one settled (or `from_year`) up to the target's, each in
        the frequency it was settled in, so a credit or a small debit carried over a year end
        is recomputed and never read from a row that has gone stale. A year with no rows takes
        the frequency the entity has now; a year the pack has no law for is skipped. Answers
        the settlement of `period`."""
        frequency = profile.iva_frequency
        previous: IvaSettlement | None = None
        target: IvaSettlement | None = None
        for year in range(
            self._first_year(legal_entity_id, from_year, period.year), period.year + 1
        ):
            year_law = pack.law_for(year)
            if year_law is None or year_law.iva is None:
                continue
            stored = self._stored_frequencies(legal_entity_id, year)
            if len(stored) > 1 or (year == period.year and stored not in (set(), {frequency})):
                raise ValidationFailed(
                    ENTITY,
                    "number",
                    f"il {year} ha gia' periodi liquidati con un'altra cadenza: una cadenza "
                    "diversa vale dall'anno dopo",
                    expected="la cadenza con cui l'anno e' gia' stato liquidato",
                )
            year_frequency = next(iter(stored)) if stored else frequency
            year_last = _QUARTERS if year_frequency == "quarterly" else _MONTHS_PER_YEAR
            for number in range(1, (period.number if year == period.year else year_last) + 1):
                target = self._settle(
                    legal_entity_id,
                    pack,
                    year_law.iva,
                    year_frequency,
                    year,
                    number,
                    previous,
                    lenient=lenient,
                )
                previous = target
        return target

    # ---- one period ------------------------------------------------------------------------

    def _settle(
        self,
        legal_entity_id: UUID,
        pack: FiscalPack,
        law: IvaSettlementLaw,
        frequency: str,
        year: int,
        number: int,
        previous: IvaSettlement | None,
        *,
        lenient: bool,
    ) -> IvaSettlement:
        """One period. Lenient (the daily pass): a due item with a payment recorded that the
        recomputation would change is left as it was, the row with it, and the period is
        reported in `frozen` instead of raising."""
        if not lenient:
            return self._apply(legal_entity_id, pack, law, frequency, year, number, previous)
        try:
            with self.session.begin_nested():
                return self._apply(legal_entity_id, pack, law, frequency, year, number, previous)
        except Conflict:
            self.frozen.append(label(frequency, year, number))
            return self.session.scalars(
                select(IvaSettlement).where(
                    IvaSettlement.legal_entity_id == legal_entity_id,
                    IvaSettlement.frequency == frequency,
                    IvaSettlement.year == year,
                    IvaSettlement.period_number == number,
                )
            ).one()

    def _apply(
        self,
        legal_entity_id: UUID,
        pack: FiscalPack,
        law: IvaSettlementLaw,
        frequency: str,
        year: int,
        number: int,
        previous: IvaSettlement | None,
    ) -> IvaSettlement:
        bounds = period_bounds(frequency, year, number)
        due = payment_date(pack, frequency, year, number)
        if due is None:
            raise ValidationFailed(
                ENTITY,
                "number",
                "il calendario fiscale non ha la scadenza di questo periodo",
                expected="un periodo di un anno di cui il calendario ha le date",
            )
        sales = sales_vat(self.session, legal_entity_id, bounds)
        reverse_charge, deductible = purchase_vat(self.session, legal_entity_id, bounds)
        credit_in, debit_in = carry_in(previous)
        figures = compute_figures(
            law,
            frequency=frequency,
            number=number,
            sales_vat=sales,
            reverse_charge_vat=reverse_charge,
            purchase_vat=deductible,
            credit_in=credit_in,
            debit_in=debit_in,
        )
        row = self.session.scalars(
            select(IvaSettlement).where(
                IvaSettlement.legal_entity_id == legal_entity_id,
                IvaSettlement.frequency == frequency,
                IvaSettlement.year == year,
                IvaSettlement.period_number == number,
            )
        ).first()
        if row is None:
            row = IvaSettlement(
                legal_entity_id=legal_entity_id,
                frequency=frequency,
                year=year,
                period_number=number,
                period_start=bounds.start,
                period_end=bounds.end,
            )
            self.session.add(row)
        row.pack_id = pack.id
        row.pack_version = pack.version
        row.sales_vat = sales
        row.reverse_charge_vat = reverse_charge
        row.purchase_vat = deductible
        row.credit_in = credit_in
        row.debit_in = debit_in
        row.surcharge = figures.surcharge
        row.result = figures.result
        row.outcome = figures.outcome
        row.payment_due_on = due.due_date
        self.session.flush()
        self._write_due_item(row, label(frequency, year, number))
        return row

    def _write_due_item(self, row: IvaSettlement, words: str) -> None:
        item = self.due_items.repo.by_source(SOURCE_KIND, row.id)
        if row.outcome != "due":
            if item is not None and item.state != "dropped":
                self.due_items.drop(item.id, f"IVA {words}: la liquidazione e' stata ricalcolata")
            return
        if item is None:
            self.due_items.create(
                DueItemCreate(
                    legal_entity_id=row.legal_entity_id,
                    direction="out",
                    counterparty_type="tax_office",
                    tax=TAX,
                    amount=row.result,
                    description=f"IVA {words}",
                    state="expected",
                    source_kind=SOURCE_KIND,
                    source_id=row.id,
                    own_date=row.payment_due_on,
                )
            )
            return
        self.due_items.restate(item.id, amount=row.result, own_date=row.payment_due_on)

    # ---- plumbing --------------------------------------------------------------------------

    def _first_year(self, legal_entity_id: UUID, from_year: int, target_year: int) -> int:
        """The first year the entity has a stored settlement in, or `from_year` when it has
        none: the chain starts there, so nothing before it is carried in."""
        first = self.session.scalar(
            select(func.min(IvaSettlement.year)).where(
                IvaSettlement.legal_entity_id == legal_entity_id
            )
        )
        return (
            min(first, from_year, target_year) if first is not None else min(from_year, target_year)
        )

    def _stored_frequencies(self, legal_entity_id: UUID, year: int) -> set[str]:
        return set(
            self.session.scalars(
                select(IvaSettlement.frequency)
                .where(
                    IvaSettlement.legal_entity_id == legal_entity_id,
                    IvaSettlement.year == year,
                )
                .distinct()
            )
        )

    def _lock(self, profile: FiscalProfile) -> None:
        """Two settlements of one entity queue: the second reads what the first wrote."""
        self.session.execute(
            select(FiscalProfile.id).where(FiscalProfile.id == profile.id).with_for_update()
        )

    def _read(self, row: IvaSettlement) -> IvaSettlementRead:
        read = IvaSettlementRead.model_validate(row)
        item: DueItem | None = self.due_items.repo.by_source(SOURCE_KIND, row.id)
        if item is not None and item.state != "dropped":
            read.due_item_id = item.id
        return read


__all__ = [
    "Bounds",
    "Figures",
    "IvaSettlementService",
    "annual_return_date",
    "carry_in",
    "compute_figures",
    "deductible_vat",
    "label",
    "payment_date",
    "period_bounds",
    "purchase_conditions",
    "purchase_vat",
    "sales_conditions",
    "sales_vat",
]
