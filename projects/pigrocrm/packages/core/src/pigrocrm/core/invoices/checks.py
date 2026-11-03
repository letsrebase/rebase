"""Rules about an invoice and its dates that read nothing but their arguments.

No session, no row lookup: the callers pass the values a write is about to store (and
`today`, read once per operation), so each refusal can be tested without a database and
two operations cannot disagree on what "today" is.
"""

from datetime import date
from decimal import Decimal

from pigrocrm.core.errors import Conflict, ImmutableField, ValidationFailed
from pigrocrm.core.invoices.constants import ENTITY, ZERO
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import YEAR_MAX, YEAR_MIN, InvoiceImport
from pigrocrm.core.money import round_money

# What stays writable once a `invoice` has left the `draft` state (spec 4). Everything
# else on the row is frozen, and an attempt raises `ImmutableField` naming the field.
# `payment_status`/`payment_date` are absent because they have their own method, which
# is what makes their agreement checkable; `status` is absent for the same reason.
MUTABLE_AFTER_ISSUE: frozenset[str] = frozenset({"note_interne", "custom_fields"})


def is_editable(invoice: Invoice) -> bool:
    """A `invoice` is editable only as a `draft`; a `proforma` is editable until it
    is `consumed`. That asymmetry is the point of a proforma (spec 5): agree the
    amount, correct it as many times as needed, without touching the register."""
    if invoice.type == "proforma":
        return invoice.status in ("draft", "confirmed")
    return invoice.status == "draft"


def require_editable(invoice: Invoice, field: str) -> None:
    if not is_editable(invoice):
        raise ImmutableField(
            ENTITY,
            field,
            f"una fattura in stato '{invoice.status}' e' un documento fiscale: "
            "si corregge con un annullamento e una nuova emissione, non con una modifica",
        )


def check_accrual(da: date | None, a: date | None) -> None:
    """The accrual period as it will be stored: both ends or neither, in order.

    Checked on the *merged* row by `update`, not on the request, because completing
    a period whose other end is already stored is a one-field change. The two table
    CHECKs (`ck_invoices_accrual_together`, `ck_invoices_accrual_ordered`)
    refuse the same shapes on every other write path; this is what turns a
    violation into a `ValidationFailed` naming the field instead of a poisoned
    session.
    """
    if (da is None) != (a is None):
        missing = "accrual_to" if a is None else "accrual_from"
        raise ValidationFailed(
            ENTITY,
            missing,
            "un periodo di competenza ha un inizio e una fine: indicali entrambi "
            "oppure nessuno dei due",
            expected="competenza_da e competenza_a insieme",
        )
    if da is not None and a is not None and a < da:
        raise ValidationFailed(
            ENTITY,
            "accrual_to",
            "il periodo di competenza finisce prima di cominciare",
            expected=f"una data dal {da.isoformat()} in poi",
        )


def check_issue_date(issue_date: date, today: date) -> None:
    """Two limits, both from spec 6.2.

    `issue_date` is a `date` in the issuer's own calendar, never the UTC projection
    of an instant: `toISOString()` on 31 December at 23:30 CET yields 1 January, which
    puts an immutable document in the wrong fiscal year. `today` therefore comes from
    `today_local()` (see `db/clock.py`), never a bare `date.today()`.
    """
    if issue_date > today:
        raise ValidationFailed(
            ENTITY,
            "issue_date",
            "una fattura non si emette con data futura",
            expected=f"una data non successiva a {today.isoformat()}",
        )
    if issue_date < date(today.year, 1, 1):
        raise ValidationFailed(
            ENTITY,
            "issue_date",
            "un anno chiuso e' chiuso: non si inserisce nel registro di un anno "
            "precedente dopo che ne e' iniziato uno nuovo",
            expected=f"una data dal {today.year}-01-01 in poi",
        )


def check_issuable(source: Invoice, *, from_proforma: bool) -> None:
    """Whether this row may still become a fiscal document.

    `issue` asks it twice (cheaply before taking the year's counter lock, and
    authoritatively after), and two copies of a refusal message drift.
    """
    if from_proforma:
        if source.status != "confirmed":
            raise Conflict(
                ENTITY,
                "solo una proforma confermata si converte in fattura",
                status_current=source.status,
                status_richiesto="confirmed",
            )
    elif source.status != "draft":
        raise Conflict(
            ENTITY,
            f"una fattura in stato '{source.status}' e' gia' stata emessa: "
            "una correzione e' un annullamento e una nuova fattura",
            status_current=source.status,
        )


def check_import_date(issue_date: date, today: date) -> None:
    """Only "not in the future" (§3.2 rule 5). `check_issue_date` also refuses a closed
    year, and a closed year is exactly what an import fills."""
    if issue_date > today:
        raise ValidationFailed(
            ENTITY,
            "issue_date",
            "una fattura non si importa con data futura",
            expected=f"una data non successiva a {today.isoformat()}",
        )


def check_transmission_date(when: date, issue_date: date | None, today: date) -> None:
    """The two facts a delivery date has to satisfy: not in the future, not before the
    invoice it delivers.

    Shared by `mark_transmitted_externally` and `import_issued` because the column is
    the same column: it is immutable once written and `annul` reads it to decide whether
    a correction is still possible, so a delivery dated tomorrow, or before the
    emission, could never be corrected.
    """
    if when > today:
        raise ValidationFailed(
            ENTITY,
            "transmitted_externally_at",
            "una consegna non si registra con data futura",
            expected=f"una data non successiva a {today.isoformat()}",
        )
    if issue_date is not None and when < issue_date:
        raise ValidationFailed(
            ENTITY,
            "transmitted_externally_at",
            f"la consegna non puo' precedere l'emissione ({issue_date.isoformat()})",
            expected=f"una data dal {issue_date.isoformat()} in poi",
        )


def check_register_year(year: int) -> None:
    """Bound `year` before it reaches a lock or a query.

    Every other `year` in this domain is bounded the same way (`InvoiceImport.year`,
    `InvoiceListQuery.year`), and the register methods are the one place a bare `int`
    from a caller reaches `lock_counter`'s raw `INSERT INTO invoice_counters` or a
    register query directly. An out-of-range value would otherwise surface as a Postgres
    `integer out of range` `DataError`, or silently create junk counter and gap rows for
    a year nothing else will ever ask about.
    """
    if not YEAR_MIN <= year <= YEAR_MAX:
        raise ValidationFailed(
            ENTITY,
            "year",
            "anno fuori dal registro",
            expected=f"un anno fra {YEAR_MIN} e {YEAR_MAX}",
        )


def check_positive_total(total: Decimal) -> None:
    """A TD01 at zero or below is not an invoice (spec 6.2); the same refusal for what
    `issue` computes and for what an import declares."""
    if total <= ZERO:
        raise ValidationFailed(
            ENTITY,
            "total",
            "una TD01 a zero o negativa non e' una fattura",
            expected="un totale maggiore di zero",
        )


def check_declared_totals(data: InvoiceImport) -> None:
    """`imponibile == Σ prezzo_totale` and `imponibile + imposta == totale`, to the
    cent (§3.3).

    **The stamp duty is not in the identity.** `sum_totals` stores
    `totale = imponibile + imposta` and keeps `stamp_duty` alongside, because
    `DatiBollo/BolloVirtuale` declares that the *issuer* settled the stamp virtually:
    charging it to the customer would need a line of its own with `Natura N1`, which is
    out of scope. So an imported invoice is checked against the same identity a natively
    issued one satisfies, and the previous system's register is the confirming fact: its
    «Totale» column always equals «Imp. Reddito». `stamp_duty` is still checked
    (non-negative) and stored; it is never added.
    """
    somma_lines = round_money(sum((r.total_price for r in data.lines), Decimal("0")))
    if somma_lines != round_money(data.taxable_amount):
        raise ValidationFailed(
            ENTITY,
            "taxable_amount",
            f"l'imponibile dichiarato ({data.taxable_amount}) non e' la somma delle righe "
            f"({somma_lines})",
            expected="imponibile uguale alla somma dei prezzi totali di riga",
        )
    if data.stamp_duty < ZERO:
        raise ValidationFailed(
            ENTITY,
            "stamp_duty",
            f"il bollo dichiarato ({data.stamp_duty}) e' negativo",
            expected="un bollo maggiore o uguale a zero",
        )
    expected = round_money(data.taxable_amount + data.vat_amount)
    if expected != round_money(data.total):
        raise ValidationFailed(
            ENTITY,
            "total",
            f"il totale dichiarato ({data.total}) non e' imponibile + imposta "
            f"({expected}): il bollo si dichiara a parte e non entra nel totale",
            expected="totale uguale a imponibile + imposta",
        )
    check_positive_total(data.total)
