"""The two tax reminders of the space group (taxes design § 4, REB-1078):
`tax_payment_due` and `tax_figure_missing`.

**Admins whose scope covers the legal entity, nobody else** (`admins_seeing`): a tax item is the
fiscal position, so it is read the way the due items' own gate reads it (an administrator whose
scope covers the legal entity). Both types sit under the `taxes` module.

**No amount, ever.** A title and a detail go out on email, Slack and push as written here, so
they name the tax and the date and carry no figure; the amount is on the due item in the app.

**`tax_payment_due`** is one reminder per unsettled tax due item (`tax_balance`, `tax_advance`,
`tax_instalment`) dated from today on; its measure is the days to the date, so the catalogue's
`until` ladder (14 and 3 days) says warning and serious. A settled or dropped item is not
open and raises nothing; one past its date is not a «before a date» any more and is left to the
schedule, which draws it overdue.

**`tax_figure_missing`** is one reminder per legal entity, income tax and tax year whose return
the pack says is filed (`fiscal/law_payment.py`, `return_filing_date`) and for which no declared
figure was typed once that date has passed; its measure is the days since the filing date.
Only the latest tax year whose filing date has passed is looked at, so a legal entity is never
told about years before it used the product, and a legal entity created after the filing date
is not asked for it. A tax whose return the pack carries no rule for raises nothing: there is
no date to measure from, and none is guessed.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select

from pigrocrm.core.due_items.models import (
    OPEN_STATES,
    TAX_ADVANCE_SOURCE_KIND,
    TAX_BALANCE_SOURCE_KIND,
    DueItem,
)
from pigrocrm.core.fiscal.advances import INCOME_TAXES, fiscal_year_end
from pigrocrm.core.fiscal.law import DeclaredTax
from pigrocrm.core.fiscal.law_payment import ReturnForm
from pigrocrm.core.fiscal.models import DeclaredTaxFigure, FiscalProfile
from pigrocrm.core.fiscal.pack import FiscalPack, resolve_pack
from pigrocrm.core.fiscal.payment_rules import return_filing_date
from pigrocrm.core.fiscal.tax_items import TAX_LABEL
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.space import admins_seeing
from pigrocrm.core.reminders.detectors.text import format_days

PAYMENT_DUE = REMINDER_TYPES["tax_payment_due"]
FIGURE_MISSING = REMINDER_TYPES["tax_figure_missing"]

# The due item source kinds that are a tax to pay to the tax office, by the word the title
# opens with.
TAX_DUE_KINDS: dict[str, str] = {
    TAX_BALANCE_SOURCE_KIND: "Saldo",
    TAX_ADVANCE_SOURCE_KIND: "Acconto",
    "tax_instalment": "Rata",
}
_FORM: dict[DeclaredTax, ReturnForm] = {
    "irpef": "redditi_pf",
    "substitute_tax": "redditi_pf",
    "ires": "redditi_sc",
    "irap": "irap",
}
_LABELS: dict[str, str] = {str(tax): label for tax, label in TAX_LABEL.items()}
# The tax years back from the one before this year that a filing date can belong to.
_YEARS_BACK = 2


def _day(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _tax_name(tax: str | None) -> str:
    if tax is None:
        return "imposta"
    return _LABELS.get(tax, tax.upper())


# -- tax_payment_due ----------------------------------------------------------------------


def detect_payment_due(ctx: DetectorContext) -> list[Detection]:
    rows = ctx.session.scalars(
        select(DueItem)
        .where(
            DueItem.source_kind.in_(tuple(TAX_DUE_KINDS)),
            DueItem.state.in_(OPEN_STATES),
            DueItem.due_date.is_not(None),
            DueItem.due_date >= ctx.today,
            DueItem.due_date <= ctx.today + timedelta(days=ctx.bound(PAYMENT_DUE)),
        )
        .order_by(DueItem.due_date, DueItem.id)
    )
    found: list[Detection] = []
    for item in rows:
        recipients = admins_seeing(ctx, item.legal_entity_id)
        if not recipients or item.due_date is None:
            continue
        days = (item.due_date - ctx.today).days
        name = f"{TAX_DUE_KINDS[item.source_kind]} {_tax_name(item.tax)}"
        found.append(
            Detection(
                type="tax_payment_due",
                subject_id=item.id,
                measure=float(days),
                recipients=recipients,
                title=f"{name} da pagare il {_day(item.due_date)}",
                detail=(
                    f"{name} scade il {_day(item.due_date)}, fra {format_days(days)}: "
                    "l'importo è nello scadenzario"
                    if days
                    else f"{name} scade oggi, {_day(item.due_date)}: l'importo è nello scadenzario"
                ),
                entity="fiscal",
                entity_id=item.legal_entity_id,
                legal_entity_id=item.legal_entity_id,
            )
        )
    return found


# -- tax_figure_missing -------------------------------------------------------------------


def _entities_with_pack(ctx: DetectorContext) -> list[tuple[LegalEntity, FiscalPack]]:
    rows = ctx.session.execute(
        select(LegalEntity, FiscalProfile)
        .join(FiscalProfile, FiscalProfile.legal_entity_id == LegalEntity.id)
        .where(LegalEntity.active.is_(True))
        .order_by(LegalEntity.is_default.desc(), LegalEntity.name, LegalEntity.id)
    )
    found: list[tuple[LegalEntity, FiscalPack]] = []
    for legal_entity, profile in rows:
        try:
            found.append((legal_entity, resolve_pack(profile.pack_id, profile.pack_version)))
        except KeyError:
            # A version this build does not ship: nothing to read, and the others go on.
            continue
    return found


def _declared(ctx: DetectorContext) -> set[tuple[UUID, str, int]]:
    return {
        (row.legal_entity_id, row.tax, row.tax_year)
        for row in ctx.session.scalars(select(DeclaredTaxFigure))
    }


def figure_subject(legal_entity_id: UUID, tax: str, year: int) -> UUID:
    """One occurrence per legal entity, tax and tax year: the next year's is a new one."""
    return uuid5(legal_entity_id, f"{tax}|{year}")


def detect_figure_missing(ctx: DetectorContext) -> list[Detection]:
    declared = ctx.once(_declared)
    found: list[Detection] = []
    for legal_entity, pack in ctx.once(_entities_with_pack):
        recipients = admins_seeing(ctx, legal_entity.id)
        if not recipients:
            continue
        created = legal_entity.created_at.date()
        for tax in INCOME_TAXES:
            filed = _latest_filed(ctx.today, legal_entity, pack, tax)
            if filed is None:
                continue
            year, due = filed
            if due < created or (legal_entity.id, tax, year) in declared:
                continue
            days = (ctx.today - due).days
            name = TAX_LABEL[tax]
            found.append(
                Detection(
                    type="tax_figure_missing",
                    subject_id=figure_subject(legal_entity.id, tax, year),
                    measure=float(days),
                    recipients=recipients,
                    title=f"Cifra dichiarata mancante: {name} {year}",
                    detail=(
                        f"La dichiarazione {year} andava presentata entro il {_day(due)} e non "
                        f"c'è la cifra dichiarata di {name}: inseriscila, o resta la stima"
                    ),
                    entity="fiscal",
                    entity_id=legal_entity.id,
                    legal_entity_id=legal_entity.id,
                )
            )
    return found


def _latest_filed(
    today: date, legal_entity: LegalEntity, pack: FiscalPack, tax: DeclaredTax
) -> tuple[int, date] | None:
    """The latest tax year of `tax` whose return the pack dates and whose date is before
    `today`, with that date; `None` when the pack states the tax in no such year."""
    for year in range(today.year, today.year - _YEARS_BACK - 1, -1):
        law = pack.law_for(year)
        if law is None or tax not in law.taxes:
            continue
        period_end = fiscal_year_end(
            year, legal_entity.fiscal_year_end_month, legal_entity.fiscal_year_end_day
        )
        filing = return_filing_date(pack, _FORM[tax], period_end)
        if filing is not None and filing.due_date < today:
            return year, filing.due_date
    return None


DETECTORS = (
    Detector(PAYMENT_DUE, detect_payment_due),
    Detector(FIGURE_MISSING, detect_figure_missing),
)
