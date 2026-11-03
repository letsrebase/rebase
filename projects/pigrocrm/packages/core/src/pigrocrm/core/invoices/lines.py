"""From the lines a caller typed to the lines and totals that are stored.

Functions of their arguments (and, for `persist_lines`, of the repository they write
through): the regime strategy is resolved once by the caller and passed in, so a write
asks the fiscal pack one question once.
"""

from collections.abc import Sequence

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.regime import RegimeStrategy
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.invoices.constants import ENTITY
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import InvoiceLineIn
from pigrocrm.core.invoices.totals import (
    MONEY_MAX_EXCLUSIVE,
    ComputedLine,
    build_recap,
    line_total,
    overflows_money_column,
    sum_totals,
)


def compute_lines(
    lines: Sequence[InvoiceLineIn],
    profile: FiscalSnapshot,
    strategy: RegimeStrategy,
    *,
    country_customer: str,
) -> tuple[ComputedLine, ...]:
    """Caller input plus the regime's answer, renumbered from 1.

    Renumbering here is what maintains contiguity: the unique constraint on
    `(invoice_id, numero_linea)` and the `>= 1` check are the database's half, and
    no single-row `CHECK` can see the other rows.

    `country_customer` is the customer's country as it is *now*: the `Natura` is
    decided when the lines are computed and copied unchanged by `issue`, so a
    proforma whose customer changed country is repaired by replacing its lines.
    """
    computed: list[ComputedLine] = []
    for index, line in enumerate(lines, start=1):
        rate, vat_nature, reference = strategy.resolve_line_vat(
            line.vat_rate, profile, country_customer=country_customer
        )
        total_price = line_total(
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount_percentage=line.discount_percentage,
            discount_amount=line.discount_amount,
        )
        # The factors mirror `Numeric(12, 6)` in the schema; their product goes to
        # `invoice_lines.total_price`, which is `Numeric(12, 2)`, and no Pydantic bound
        # on two factors can express a bound on their product. Refused here, before
        # anything is flushed: Postgres answers an overflow with `NumericValueOutOfRange`,
        # which SQLAlchemy raises as `DataError` and not `IntegrityError`, so no handler
        # catches it. The line is named because an invoice may carry `MAX_LINES` of them.
        if overflows_money_column(total_price):
            raise ValidationFailed(
                ENTITY,
                "lines",
                f"l'importo della riga {index} non e' rappresentabile: quantita per "
                "prezzo unitario, al netto degli sconti, supera il massimo",
                expected=f"un importo con valore assoluto inferiore a {MONEY_MAX_EXCLUSIVE}",
            )
        computed.append(
            ComputedLine(
                line_number=index,
                description=line.description,
                quantity=line.quantity,
                unit_of_measure=line.unit_of_measure,
                unit_price=line.unit_price,
                discount_percentage=line.discount_percentage,
                discount_amount=line.discount_amount,
                total_price=total_price,
                vat_rate=rate,
                vat_nature=vat_nature,
                legal_reference=reference,
            )
        )
    return tuple(computed)


def computed_from_stored(lines: Sequence[InvoiceLine]) -> tuple[ComputedLine, ...]:
    """The stored lines as `ComputedLine`s, untouched: `issue` copies the amounts the
    customer agreed to rather than recomputing them."""
    return tuple(
        ComputedLine(
            line_number=r.line_number,
            description=r.description,
            quantity=r.quantity,
            unit_of_measure=r.unit_of_measure,
            unit_price=r.unit_price,
            discount_percentage=r.discount_percentage,
            discount_amount=r.discount_amount,
            total_price=r.total_price,
            vat_rate=r.vat_rate,
            vat_nature=r.vat_nature,
            legal_reference=r.legal_reference,
        )
        for r in lines
    )


def apply_totals(
    invoice: Invoice,
    computed: Sequence[ComputedLine],
    profile: FiscalSnapshot,
    strategy: RegimeStrategy,
) -> None:
    """Compute and **store**. Never recomputed by a client: a total computed in the
    browser is the structural defect inherited from the previous system, and on an
    invoice it costs more."""
    recap = build_recap(computed)
    taxable_amount, vat_amount, total = sum_totals(recap)
    # The stamp duty is stored but does not enter the total: `DatiBollo` declares that
    # the issuer settled it virtually (spec 7.2).
    stamp_duty = strategy.stamp_duty(recap, profile)

    # Summation reaches the same overflow with no oversized line anywhere:
    # `InvoiceCreate.lines` admits `MAX_LINES` of them, and two hundred lines of fifty
    # million each are two hundred ordinary amounts whose sum is not. Checked before a
    # single attribute is assigned, so a refused invoice leaves no unstorable value on a
    # mapped object for the next autoflush to find.
    for field, value in (
        ("taxable_amount", taxable_amount),
        ("vat_amount", vat_amount),
        ("total", total),
        ("stamp_duty", stamp_duty),
    ):
        if overflows_money_column(value):
            raise ValidationFailed(
                ENTITY,
                field,
                f"la somma delle righe non e' rappresentabile: {field} supera il massimo",
                expected=f"un importo con valore assoluto inferiore a {MONEY_MAX_EXCLUSIVE}",
            )

    invoice.taxable_amount = taxable_amount
    invoice.vat_amount = vat_amount
    invoice.total = total
    invoice.stamp_duty = stamp_duty


def persist_lines(
    repo: InvoiceRepository, invoice: Invoice, computed: Sequence[ComputedLine]
) -> None:
    """Replace the invoice's stored lines with `computed`."""
    repo.clear_lines(invoice.id)
    for line in computed:
        repo.add_line(
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=line.line_number,
                description=line.description,
                quantity=line.quantity,
                unit_of_measure=line.unit_of_measure,
                unit_price=line.unit_price,
                discount_percentage=line.discount_percentage,
                discount_amount=line.discount_amount,
                total_price=line.total_price,
                vat_rate=line.vat_rate,
                vat_nature=line.vat_nature,
                legal_reference=line.legal_reference,
            )
        )
