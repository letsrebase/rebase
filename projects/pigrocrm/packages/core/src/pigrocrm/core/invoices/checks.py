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
from pigrocrm.core.invoices.schemas import ANNO_MAX, ANNO_MIN, InvoiceImport
from pigrocrm.core.money import round_money

# What stays writable once a `fattura` has left the `bozza` state (spec 4). Everything
# else on the row is frozen, and an attempt raises `ImmutableField` naming the field.
# `stato_pagamento`/`data_incasso` are absent because they have their own method, which
# is what makes their agreement checkable; `stato` is absent for the same reason.
MUTABLE_AFTER_ISSUE: frozenset[str] = frozenset({"note_interne", "custom_fields"})


def is_editable(invoice: Invoice) -> bool:
    """A `fattura` is editable only as a `bozza`; a `proforma` is editable until it
    is `consumata`. That asymmetry is the point of a proforma (spec 5): agree the
    amount, correct it as many times as needed, without touching the register."""
    if invoice.tipo == "proforma":
        return invoice.stato in ("bozza", "confermata")
    return invoice.stato == "bozza"


def require_editable(invoice: Invoice, field: str) -> None:
    if not is_editable(invoice):
        raise ImmutableField(
            ENTITY,
            field,
            f"una fattura in stato '{invoice.stato}' e' un documento fiscale: "
            "si corregge con un annullamento e una nuova emissione, non con una modifica",
        )


def check_competenza(da: date | None, a: date | None) -> None:
    """The accrual period as it will be stored: both ends or neither, in order.

    Checked on the *merged* row by `update`, not on the request, because completing
    a period whose other end is already stored is a one-field change. The two table
    CHECKs (`ck_invoices_competenza_together`, `ck_invoices_competenza_ordered`)
    refuse the same shapes on every other write path; this is what turns a
    violation into a `ValidationFailed` naming the field instead of a poisoned
    session.
    """
    if (da is None) != (a is None):
        missing = "competenza_a" if a is None else "competenza_da"
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
            "competenza_a",
            "il periodo di competenza finisce prima di cominciare",
            expected=f"una data dal {da.isoformat()} in poi",
        )


def check_issue_date(data_emissione: date, today: date) -> None:
    """Two limits, both from spec 6.2.

    `data_emissione` is a `date` in the issuer's own calendar, never the UTC projection
    of an instant: `toISOString()` on 31 December at 23:30 CET yields 1 January, which
    puts an immutable document in the wrong fiscal year. `today` therefore comes from
    `oggi_in_italia()` (see `clock.py`), never a bare `date.today()`.
    """
    if data_emissione > today:
        raise ValidationFailed(
            ENTITY,
            "data_emissione",
            "una fattura non si emette con data futura",
            expected=f"una data non successiva a {today.isoformat()}",
        )
    if data_emissione < date(today.year, 1, 1):
        raise ValidationFailed(
            ENTITY,
            "data_emissione",
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
        if source.stato != "confermata":
            raise Conflict(
                ENTITY,
                "solo una proforma confermata si converte in fattura",
                stato_attuale=source.stato,
                stato_richiesto="confermata",
            )
    elif source.stato != "bozza":
        raise Conflict(
            ENTITY,
            f"una fattura in stato '{source.stato}' e' gia' stata emessa: "
            "una correzione e' un annullamento e una nuova fattura",
            stato_attuale=source.stato,
        )


def check_import_date(data_emissione: date, today: date) -> None:
    """Only "not in the future" (§3.2 rule 5). `check_issue_date` also refuses a closed
    year, and a closed year is exactly what an import fills."""
    if data_emissione > today:
        raise ValidationFailed(
            ENTITY,
            "data_emissione",
            "una fattura non si importa con data futura",
            expected=f"una data non successiva a {today.isoformat()}",
        )


def check_transmission_date(quando: date, data_emissione: date | None, today: date) -> None:
    """The two facts a delivery date has to satisfy: not in the future, not before the
    invoice it delivers.

    Shared by `mark_transmitted_externally` and `import_issued` because the column is
    the same column: it is immutable once written and `annul` reads it to decide whether
    a correction is still possible, so a delivery dated tomorrow, or before the
    emission, could never be corrected.
    """
    if quando > today:
        raise ValidationFailed(
            ENTITY,
            "trasmessa_esternamente_il",
            "una consegna non si registra con data futura",
            expected=f"una data non successiva a {today.isoformat()}",
        )
    if data_emissione is not None and quando < data_emissione:
        raise ValidationFailed(
            ENTITY,
            "trasmessa_esternamente_il",
            f"la consegna non puo' precedere l'emissione ({data_emissione.isoformat()})",
            expected=f"una data dal {data_emissione.isoformat()} in poi",
        )


def check_register_year(anno: int) -> None:
    """Bound `anno` before it reaches a lock or a query.

    Every other `anno` in this domain is bounded the same way (`InvoiceImport.anno`,
    `InvoiceListQuery.anno`), and the register methods are the one place a bare `int`
    from a caller reaches `lock_counter`'s raw `INSERT INTO invoice_counters` or a
    register query directly. An out-of-range value would otherwise surface as a Postgres
    `integer out of range` `DataError`, or silently create junk counter and gap rows for
    a year nothing else will ever ask about.
    """
    if not ANNO_MIN <= anno <= ANNO_MAX:
        raise ValidationFailed(
            ENTITY,
            "anno",
            "anno fuori dal registro",
            expected=f"un anno fra {ANNO_MIN} e {ANNO_MAX}",
        )


def check_positive_total(totale: Decimal) -> None:
    """A TD01 at zero or below is not an invoice (spec 6.2); the same refusal for what
    `issue` computes and for what an import declares."""
    if totale <= ZERO:
        raise ValidationFailed(
            ENTITY,
            "totale",
            "una TD01 a zero o negativa non e' una fattura",
            expected="un totale maggiore di zero",
        )


def check_declared_totals(data: InvoiceImport) -> None:
    """`imponibile == Σ prezzo_totale` and `imponibile + imposta == totale`, to the
    cent (§3.3).

    **The stamp duty is not in the identity.** `sum_totals` stores
    `totale = imponibile + imposta` and keeps `bollo` alongside, because
    `DatiBollo/BolloVirtuale` declares that the *issuer* settled the stamp virtually:
    charging it to the customer would need a line of its own with `Natura N1`, which is
    out of scope. So an imported invoice is checked against the same identity a natively
    issued one satisfies, and the previous system's register is the confirming fact: its
    «Totale» column always equals «Imp. Reddito». `bollo` is still checked
    (non-negative) and stored; it is never added.
    """
    somma_righe = round_money(sum((r.prezzo_totale for r in data.righe), Decimal("0")))
    if somma_righe != round_money(data.imponibile):
        raise ValidationFailed(
            ENTITY,
            "imponibile",
            f"l'imponibile dichiarato ({data.imponibile}) non e' la somma delle righe "
            f"({somma_righe})",
            expected="imponibile uguale alla somma dei prezzi totali di riga",
        )
    if data.bollo < ZERO:
        raise ValidationFailed(
            ENTITY,
            "bollo",
            f"il bollo dichiarato ({data.bollo}) e' negativo",
            expected="un bollo maggiore o uguale a zero",
        )
    atteso = round_money(data.imponibile + data.imposta)
    if atteso != round_money(data.totale):
        raise ValidationFailed(
            ENTITY,
            "totale",
            f"il totale dichiarato ({data.totale}) non e' imponibile + imposta "
            f"({atteso}): il bollo si dichiara a parte e non entra nel totale",
            expected="totale uguale a imponibile + imposta",
        )
    check_positive_total(data.totale)
