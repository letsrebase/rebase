"""The income taxes' balances and advances, written as due items (REB-1074, taxes design § 2).

For each legal entity, tax and year this writes the **balance** of the tax year and the
**advances** of the tax year as due items out, the tax office the counterparty with the tax
named, on the dates the pack gives (`fiscal/advances.py`). The source kinds are `tax_balance` and
`tax_advance`, both restricted (so is `chamber_fee`, the srl's annual chamber of commerce fee,
REB-1077, written by the same refresh; see `_chamber_fee_payment`): they are the fiscal position,
so the due items' own gate (an administrator whose scope covers the legal entity, an agent only
with `full_access`; the schedule and the Home leave them out for anybody else) applies to every
read and settlement of one.

**What an item says.** `forecast` while the base is an estimate, `expected` once a declared
figure fixes it, `settled` when paid (`DueItemService`'s own settlement). The base of a balance is
the declared figure, else the estimate, less the advances paid for the year. The base of an
advance is, by the **historical** method (the default), the declared tax of the previous year,
else its estimate; by the **forecast** method (`TaxAdvanceMethod`, chosen per legal entity and
year) the current year's estimate. A declared tax is read as the net tax the return shows after
deductions, credits, withholdings and excesses, which is what the law makes the advance base
(A5 row «The advance base is the declared tax net of ...», verified).

**A missing figure gives no item and says why.** A tax with no declared figure and no estimate
(IRPEF, IRES and IRAP have no estimate yet), an ISA flag the profile does not state, a calendar
without the year, a year end the dates are not stated for: each is a `TaxItemNotWritten` in the
report, never a zero and never a guess.

**Recomputing.** The boring option: `refresh_tax_due_items` is idempotent and recomputes
everything from the figures as they stand, so it is called where a figure or a choice changes
(`DeclaredTaxFigureService.set_declared_tax_figure`, `TaxAdvanceMethodService`) and by the daily
pass (`automations/daily.py`), which is what picks up an estimate that moved because an invoice
was collected. An item whose amount, date, state or text is as it should be is left alone; one
that is not is dropped and its replacement created. An item a payment touched is never changed
and never dropped: it is frozen at what it owed.

**The difference an advance already paid leaves.** When the base moves after an advance was paid
(a declared figure replacing the estimate it was computed on), the paid advance no longer equals
what the new base makes it. The difference is carried to the next item of that tax: a first
advance's to the second, a second's to the balance of the year, whose amount, being the tax less
what was actually paid, already holds it. The item that carries it says so in its description and
asks a person to confirm: the legal treatment is «to verify» (taxes design § 2, A5). An advance
that comes out at or below zero after the carry is not written, and the credit goes on.

**Which payments.** Only those whose date is from today to `HORIZON_DAYS` ahead are created, so
a tax that was due before the legal entity used the product is not invented overdue; an item
that exists is kept up to date whatever its date. The tax years looked at are the two before
today's year and the one after it, and a balance whose advances were never written counts the
advances as paid in the amounts the plan gives them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import (
    CHAMBER_FEE_SOURCE_KIND,
    TAX_ADVANCE_SOURCE_KIND,
    TAX_BALANCE_SOURCE_KIND,
    DueItem,
)
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.advance_schemas import TaxItemNotWritten, TaxItemPart, TaxItemsReport
from pigrocrm.core.fiscal.advances import (
    INCOME_TAXES,
    AdvanceDates,
    AdvanceRule,
    Missing,
    advance_amounts,
    advance_dates,
    advance_rule,
    balance_date,
)
from pigrocrm.core.fiscal.basis import year_basis
from pigrocrm.core.fiscal.law import DeclaredTax
from pigrocrm.core.fiscal.models import DEFAULT_ADVANCE_METHOD, DeclaredTaxFigure
from pigrocrm.core.fiscal.pack import FiscalPack, resolve_pack
from pigrocrm.core.fiscal.repository import (
    DeclaredTaxFigureRepository,
    FiscalProfileRepository,
    TaxAdvanceMethodRepository,
)
from pigrocrm.core.fiscal.schemas import FiscalProfileRead
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import round_money

ZERO = Decimal("0.00")
YEARS_BEFORE = 2
YEARS_AFTER = 1
# How far ahead a payment that has no item yet is written.
HORIZON_DAYS = 400
# The identity of a tax due item's slot: every payment of a tax year has one, so the item that
# stands for it, and the ones it replaced, are found again whatever its amount becomes.
_SLOT_NAMESPACE = uuid5(NAMESPACE_URL, "pigrocrm:tax-due-items")

TAX_LABEL: dict[DeclaredTax, str] = {
    "irpef": "IRPEF",
    "substitute_tax": "imposta sostitutiva",
    "ires": "IRES",
    "irap": "IRAP",
    "inps": "INPS",
}
RULE_NAME = "tax_due_items"

State = Literal["forecast", "expected"]


def slot_id(kind: str, legal_entity_id: UUID, tax: str, year: int, part: TaxItemPart) -> UUID:
    """The `source_id` of the item that stands for one payment of one tax year."""
    return uuid5(_SLOT_NAMESPACE, f"{kind}|{legal_entity_id}|{tax}|{year}|{part}")


@dataclass(frozen=True)
class Figure:
    """A tax of a year as the readings give it: the amount and whether a return fixed it."""

    amount: Decimal
    state: State


@dataclass(frozen=True)
class Wanted:
    """One due item as it should stand now."""

    amount: Decimal
    due: dt.date
    state: State
    description: str
    rule: str


@dataclass(frozen=True)
class Payment:
    """One payment of one tax year: where its item is, what it should say, and why it has none
    (`why`, only when a figure or a parameter is missing)."""

    tax: str
    year: int
    part: TaxItemPart
    due: dt.date | None
    wanted: Wanted | None
    why: str | None = None


@dataclass(frozen=True)
class _Part:
    part: TaxItemPart
    due: dt.date
    amount: Decimal
    frozen: bool
    carry_in: Decimal


@dataclass(frozen=True)
class _Advances:
    """The advances of one tax year as the plan stands: each instalment with its date and
    amount, the base's state, and what the paid ones leave still owed (`leftover`)."""

    parts: tuple[_Part, _Part]
    single: bool
    state: State
    method: str
    leftover: Decimal

    @property
    def total(self) -> Decimal:
        return sum((part.amount for part in self.parts), ZERO)


@dataclass
class _Context:
    legal_entity: LegalEntity
    profile: FiscalProfileRead
    pack: FiscalPack
    methods: dict[int, str]
    today: dt.date
    declared_by_year: dict[int, dict[str, DeclaredTaxFigure]] = field(default_factory=dict)
    estimates_by_year: dict[int, dict[DeclaredTax, Decimal | None]] = field(default_factory=dict)
    advances: dict[tuple[DeclaredTax, int], _Advances | Missing] = field(default_factory=dict)


def _money(amount: Decimal) -> str:
    return f"{amount:+.2f}".replace(".", ",")


class TaxDueItemService:
    """The writer's half, with no actor and no commit: its callers have asked for an
    administrator (`set_declared_tax_figure`, `set_tax_advance_method`) or run in the system
    scope (the daily pass), and commit with what they changed."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.dues = DueItemService(session)
        self.methods = TaxAdvanceMethodRepository(session)
        self.declared = DeclaredTaxFigureRepository(session)

    # ---- the write --------------------------------------------------------------------------

    def refresh_tax_due_items(
        self, legal_entity_id: UUID, *, today: dt.date | None = None
    ) -> TaxItemsReport:
        """Brings the legal entity's tax due items in step with its figures, as the module
        docstring says. Idempotent. The legal entity's row is locked first, so two refreshes
        queue instead of both writing the same items. Flushes, never commits."""
        legal_entity = self.session.execute(
            select(LegalEntity).where(LegalEntity.id == legal_entity_id).with_for_update()
        ).scalar_one_or_none()
        if legal_entity is None:
            raise NotFound("legal_entity", legal_entity_id)
        report = TaxItemsReport()
        profile_row = FiscalProfileRepository(self.session).get(legal_entity.id)
        if profile_row is None:
            return report
        profile = FiscalProfileRead.model_validate(profile_row)
        context = _Context(
            legal_entity=legal_entity,
            profile=profile,
            pack=resolve_pack(profile.pack_id, profile.pack_version),
            methods=self.methods.for_entity(legal_entity.id),
            today=today if today is not None else today_local(),
        )
        for payment in self._payments(context):
            self._sync(context, payment, report)
        return report

    # ---- what each payment should be --------------------------------------------------------

    def _payments(self, context: _Context) -> list[Payment]:
        payments: list[Payment] = []
        first = context.today.year - YEARS_BEFORE
        for year in range(first, context.today.year + YEARS_AFTER + 1):
            law = context.pack.law_for(year)
            if law is None:
                continue
            for tax in law.taxes:
                if tax not in INCOME_TAXES:
                    continue
                payments.extend(self._advance_payments(context, tax, year))
                payments.append(self._balance_payment(context, tax, year))
            if law.chamber_fee is not None:
                payments.append(self._chamber_fee_payment(context, year))
        return payments

    def _advance_dates(
        self, context: _Context, tax: DeclaredTax, year: int
    ) -> AdvanceDates | Missing:
        entity = context.legal_entity
        return advance_dates(
            context.pack,
            context.pack.law_for(year),
            tax,
            year,
            entity.fiscal_year_end_month,
            entity.fiscal_year_end_day,
        )

    def _balance_date(self, context: _Context, tax: DeclaredTax, year: int) -> dt.date | Missing:
        entity = context.legal_entity
        return balance_date(
            context.pack,
            context.pack.law_for(year),
            tax,
            year,
            entity.fiscal_year_end_month,
            entity.fiscal_year_end_day,
        )

    def _declared(self, context: _Context, tax: DeclaredTax, year: int) -> DeclaredTaxFigure | None:
        if year not in context.declared_by_year:
            context.declared_by_year[year] = {
                row.tax: row for row in self.declared.for_year(context.legal_entity.id, year)
            }
        return context.declared_by_year[year].get(tax)

    def _estimate(self, context: _Context, tax: DeclaredTax, year: int) -> Decimal | None:
        if year not in context.estimates_by_year:
            # Lazily, because the analytics facade imports the declared figures' service, which
            # calls this one.
            from pigrocrm.core.analytics.service import AnalyticsService

            context.estimates_by_year[year] = AnalyticsService(self.session).estimated_taxes(
                year, context.legal_entity, context.profile
            )
        return context.estimates_by_year[year].get(tax)

    def _advance_base(
        self, context: _Context, tax: DeclaredTax, year: int, method: str
    ) -> Figure | Missing:
        label = TAX_LABEL[tax]
        if method == "forecast":
            estimate = self._estimate(context, tax, year)
            if estimate is None:
                return Missing(
                    f"the forecast method needs the {label} estimate of {year}, which cannot "
                    "be computed (no estimate for this tax, or a parameter of the profile is "
                    "missing)"
                )
            return Figure(estimate, "forecast")
        declared = self._declared(context, tax, year - 1)
        if declared is not None and declared.tax_due is not None:
            return Figure(declared.tax_due, "expected")
        estimate = self._estimate(context, tax, year - 1)
        if estimate is not None:
            return Figure(estimate, "forecast")
        return Missing(
            f"no declared {label} and no estimate for {year - 1}, the base of the {year} advances"
        )

    def _frozen(self, kind: str, slot: UUID) -> DueItem | None:
        """The live item of a slot when a payment touched it: it owes what it owes."""
        live = self._live(kind, slot)
        if live is None:
            return None
        paid = live.state == "settled" or self.dues.repo.totals(live.id).settled != ZERO
        return live if paid else None

    def _live(self, kind: str, slot: UUID) -> DueItem | None:
        live = [i for i in self.dues.repo.items_of_source(kind, slot) if i.state != "dropped"]
        return live[-1] if live else None

    def _advances(self, context: _Context, tax: DeclaredTax, year: int) -> _Advances | Missing:
        """The advances of `tax` for `year`, with the difference a paid instalment leaves
        carried to the next one. Cached for the refresh: the balance reads it too."""
        key = (tax, year)
        if key not in context.advances:
            context.advances[key] = self._plan_advances(context, tax, year)
        return context.advances[key]

    def _plan_advances(self, context: _Context, tax: DeclaredTax, year: int) -> _Advances | Missing:
        method = context.methods.get(year, DEFAULT_ADVANCE_METHOD)
        law = context.pack.law_for(year)
        rule = advance_rule(law, tax, context.profile.isa)
        if isinstance(rule, Missing):
            return rule
        dates = self._advance_dates(context, tax, year)
        if isinstance(dates, Missing):
            return dates
        base = self._advance_base(context, tax, year, method)
        if isinstance(base, Missing):
            return base
        return self._chain(context, tax, year, rule, dates, base, method)

    def _chain(
        self,
        context: _Context,
        tax: DeclaredTax,
        year: int,
        rule: AdvanceRule,
        dates: AdvanceDates,
        base: Figure,
        method: str,
    ) -> _Advances:
        amounts = advance_amounts(rule, base.amount)
        carry = ZERO
        parts: list[_Part] = []
        instalments: tuple[tuple[TaxItemPart, dt.date, Decimal], ...] = (
            ("first_advance", dates.first_advance, amounts.first),
            ("second_advance", dates.second_advance, amounts.second),
        )
        for part, due, ideal in instalments:
            owed = ideal + carry
            frozen = self._frozen(
                TAX_ADVANCE_SOURCE_KIND,
                slot_id(TAX_ADVANCE_SOURCE_KIND, context.legal_entity.id, tax, year, part),
            )
            if frozen is not None:
                parts.append(_Part(part, due, frozen.amount, frozen=True, carry_in=carry))
                carry = owed - frozen.amount
            elif owed > 0:
                parts.append(_Part(part, due, owed, frozen=False, carry_in=carry))
                carry = ZERO
            else:
                # Nothing to pay on this date: a credit goes on to the next one.
                parts.append(_Part(part, due, ZERO, frozen=False, carry_in=carry))
                carry = owed
        return _Advances(
            parts=(parts[0], parts[1]),
            single=amounts.single,
            state=base.state,
            method=method,
            leftover=carry,
        )

    def _advance_payments(self, context: _Context, tax: DeclaredTax, year: int) -> list[Payment]:
        plan = self._advances(context, tax, year)
        if isinstance(plan, Missing):
            dates = self._advance_dates(context, tax, year)
            return [
                Payment(
                    tax,
                    year,
                    part,
                    None if isinstance(dates, Missing) else _advance_date(dates, part),
                    None,
                    plan.reason,
                )
                for part in ("first_advance", "second_advance")
            ]
        payments: list[Payment] = []
        for item in plan.parts:
            wanted = None
            if item.amount > 0 and not item.frozen:
                wanted = Wanted(
                    amount=item.amount,
                    due=item.due,
                    state=plan.state,
                    description=_advance_text(tax, year, item, plan.single),
                    rule=f"{TAX_ADVANCE_SOURCE_KIND}:{plan.method}:{tax}:{year}:{item.part}",
                )
            payments.append(Payment(tax, year, item.part, item.due, wanted))
        return payments

    def _balance_payment(self, context: _Context, tax: DeclaredTax, year: int) -> Payment:
        due = self._balance_date(context, tax, year)
        if isinstance(due, Missing):
            return Payment(tax, year, "balance", None, None, due.reason)
        label = TAX_LABEL[tax]
        declared = self._declared(context, tax, year)
        plan = self._advances(context, tax, year)
        amount: Decimal
        state: State
        carry = ZERO
        if declared is not None and declared.balance is not None:
            # The accountant's balance already accounts for what was really paid.
            amount, state = declared.balance, "expected"
        elif declared is not None and declared.tax_due is not None:
            if declared.advances is not None:
                advances = declared.advances
            elif isinstance(plan, Missing):
                return Payment(
                    tax,
                    year,
                    "balance",
                    due,
                    None,
                    f"the advances of {year} are not known: {plan.reason}",
                )
            else:
                advances, carry = plan.total, plan.leftover
            amount, state = declared.tax_due - advances, "expected"
        else:
            estimate = self._estimate(context, tax, year)
            if estimate is None:
                return Payment(
                    tax,
                    year,
                    "balance",
                    due,
                    None,
                    f"no declared {label} and no estimate for {year}",
                )
            if isinstance(plan, Missing):
                return Payment(
                    tax,
                    year,
                    "balance",
                    due,
                    None,
                    f"the advances of {year} are not known: {plan.reason}",
                )
            amount, state, carry = estimate - plan.total, "forecast", plan.leftover
        amount = round_money(amount)
        if amount <= 0:
            reason = (
                f"the {label} balance of {year} is a credit of {-amount}: credits are not due "
                "items here"
                if amount < 0
                else None
            )
            return Payment(tax, year, "balance", due, None, reason)
        text = f"Saldo {label} {year}"
        if carry != 0:
            text += _carry_text(carry)
        return Payment(
            tax,
            year,
            "balance",
            due,
            Wanted(
                amount=amount,
                due=due,
                state=state,
                description=text,
                rule=f"{TAX_BALANCE_SOURCE_KIND}:{tax}:{year}",
            ),
        )

    def _chamber_fee_payment(self, context: _Context, year: int) -> Payment:
        """The srl's annual chamber of commerce fee of `year` (REB-1077, `law_chamber_fee`).

        The law reads the turnover of the previous year's IRAP return (MIMIT note 9347 of
        16 January 2026). No return is held here, so the stand-in is the revenue `year_basis`
        gives for `year - 1`, which is what makes the item a `forecast`. The pack has no law
        for that earlier year, so the basis is read under the fee year's accrual rule, which
        is the same one (art. 83 TUIR). A company's first year pays a fixed amount by another
        rule and the entity stores no incorporation date: that case is not modelled. The date
        is the first income-tax advance's, the F24 code the payment rules' (3850)."""
        entity = context.legal_entity
        fee_law = context.pack.law_for(year)
        assert fee_law is not None and fee_law.chamber_fee is not None
        dates = advance_dates(
            context.pack,
            fee_law,
            "ires",
            year,
            entity.fiscal_year_end_month,
            entity.fiscal_year_end_day,
        )
        if isinstance(dates, Missing):
            return Payment("chamber_fee", year, "chamber_fee", None, None, dates.reason)
        due = dates.first_advance

        def none(reason: str) -> Payment:
            return Payment("chamber_fee", year, "chamber_fee", due, None, reason)

        province = (entity.province or "").strip()
        if not province:
            return none("the legal entity has no province of the registered office")
        chamber = fee_law.chamber_fee.chamber_for(province)
        if chamber is None:
            return none(
                f"no chamber of commerce is mapped to the province {province.upper()}: "
                "to verify with the accountant"
            )
        stand_in = replace(context.pack, laws=(replace(fee_law, from_year=year - 1),))
        basis = year_basis(self.session, entity, year - 1, stand_in)
        if basis is None:
            return none(f"the revenue of {year - 1}, the turnover the fee is read on, is unknown")
        fee = fee_law.chamber_fee.fee(year, basis.revenue, province)
        if fee is None:
            return none(f"the pack carries no chamber fee law for {year}")
        rules = context.pack.payment_rules_for(year)
        try:
            code = rules.f24_code("chamber_fee").code if rules is not None else None
        except KeyError:
            code = None
        if code is None:
            return none("the pack carries no F24 code for the chamber fee")
        return Payment(
            "chamber_fee",
            year,
            "chamber_fee",
            due,
            Wanted(
                amount=round_money(fee),
                due=due,
                state="forecast",
                description=(
                    f"Diritto camerale {year}, {chamber.name} (F24 codice tributo {code}), "
                    f"sul fatturato {year - 1} come stima"
                ),
                rule=f"{CHAMBER_FEE_SOURCE_KIND}:{year}",
            ),
        )

    # ---- one slot ---------------------------------------------------------------------------

    def _sync(self, context: _Context, payment: Payment, report: TaxItemsReport) -> None:
        kind = {
            "balance": TAX_BALANCE_SOURCE_KIND,
            "chamber_fee": CHAMBER_FEE_SOURCE_KIND,
        }.get(payment.part, TAX_ADVANCE_SOURCE_KIND)
        slot = slot_id(kind, context.legal_entity.id, payment.tax, payment.year, payment.part)
        current = self._live(kind, slot)
        if current is not None and self._frozen(kind, slot) is not None:
            report.kept += 1
            return
        wanted = payment.wanted
        if wanted is None:
            if current is not None:
                self.dues.drop(current.id, "non piu' dovuto")
                report.dropped += 1
            if payment.why is not None and self._reportable(context, payment):
                report.not_written.append(
                    TaxItemNotWritten(
                        tax=payment.tax, year=payment.year, part=payment.part, reason=payment.why
                    )
                )
            return
        if current is not None:
            if (current.amount, current.own_date, current.state, current.description) == (
                wanted.amount,
                wanted.due,
                wanted.state,
                wanted.description,
            ):
                report.kept += 1
                return
            self.dues.drop(current.id, "importo ricalcolato")
            report.dropped += 1
        elif not self._in_window(context, wanted.due):
            return
        self.dues.create(
            DueItemCreate(
                legal_entity_id=context.legal_entity.id,
                direction="out",
                counterparty_type="tax_office",
                tax=payment.tax,
                amount=wanted.amount,
                description=wanted.description,
                state=wanted.state,
                source_kind=kind,
                source_id=slot,
                source_rule=wanted.rule if wanted.state == "forecast" else None,
                forecast_key=slot if wanted.state == "forecast" else None,
                own_date=wanted.due,
            )
        )
        report.written += 1

    @staticmethod
    def _in_window(context: _Context, due: dt.date) -> bool:
        """Whether a payment that has no item yet is worth one: its date is not past and not
        further than the horizon."""
        return context.today <= due <= context.today + dt.timedelta(days=HORIZON_DAYS)

    def _reportable(self, context: _Context, payment: Payment) -> bool:
        """Whether a payment with no item is worth saying why: one dated in the window, and one
        the pack gives no date for only when its tax year is this year or a later one, since
        a year long past has nothing to pay."""
        if payment.due is None:
            return payment.year >= context.today.year
        return self._in_window(context, payment.due)


def _advance_date(dates: AdvanceDates, part: TaxItemPart) -> dt.date:
    return dates.first_advance if part == "first_advance" else dates.second_advance


def _carry_text(carry: Decimal) -> str:
    return f". Include {_money(carry)} euro di differenza sull'acconto gia' pagato: da confermare"


def _advance_text(tax: DeclaredTax, year: int, item: _Part, single: bool) -> str:
    if single:
        words = "in unica soluzione"
    else:
        words = "1a rata" if item.part == "first_advance" else "2a rata"
    text = f"Acconto {TAX_LABEL[tax]} {year}, {words}"
    return text + _carry_text(item.carry_in) if item.carry_in != 0 else text


__all__ = [
    "HORIZON_DAYS",
    "TAX_LABEL",
    "TaxDueItemService",
    "slot_id",
]
