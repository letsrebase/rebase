"""The sales register of the period: issued invoices and credit notes with their VAT, then
the numbers that were annulled or never used (spec accountant export § 1, C3 § 6).

Rows are placed by the date printed on the document. That is a placement, not a ruling on
the IVA period of a sale: which date governs it (issue or operation) is still «to verify» in
the frame's A5 register, and the export neither computes nor names it.
One row per invoice and per `(rate, nature)` group, the grouping and the rounding the XML
carries (`build_recap`), so an invoice's rows add up to its taxable amount and its VAT. The
row prints the document type the invoice stored. An invoice whose stored totals differ from
its lines (an import brings the totals it was issued with) gets one more row with the
difference, so the register adds up to the figures the year register sums.

The annulled numbers and the declared gaps sit in a second file: an annulled invoice keeps
its number and counts for nothing, a gap is a number the register will never carry (spec 9
§ 3.2), and the accountant asks about both when a number is missing. A gap is placed in the
period that holds the next number after it, so a run of monthly exports lists each gap once;
a period with no document lists none.
"""

from collections import defaultdict
from decimal import Decimal

from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    PartContext,
    PartOutput,
)
from pigrocrm.core.accountant_export.repository import ExportRepository
from pigrocrm.core.analytics.year_csv import PAYMENT
from pigrocrm.core.csv_cells import (
    BOM,
    amount_cell,
    identifier_cell,
    row_line,
    text_cell,
)
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.totals import ComputedLine, build_recap
from pigrocrm.core.money import sum_money

REGISTER_FILE = "registro-vendite.csv"
STRUCK_FILE = "numeri-annullati-e-non-usati.csv"

REGISTER_COLUMNS = (
    "Numero",
    "Data emissione",
    "Cliente",
    "Partita IVA",
    "Codice fiscale",
    "Tipo documento",
    "Aliquota IVA",
    "Natura",
    "Riferimento normativo",
    "Imponibile",
    "Imposta",
    "Pagamento",
    "Data incasso",
)
STRUCK_COLUMNS = ("Numero", "Data emissione", "Stato", "Data annullamento", "Motivo")
ANNULLED = "Annullata"
NOT_USED = "Non usato"
RECONCILIATION = "Differenza rispetto ai totali del documento"


def _computed(line: InvoiceLine) -> ComputedLine:
    return ComputedLine(
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


def _party(invoice: Invoice) -> tuple[str, str | None, str | None]:
    """The customer as the document printed it: `(name, partita IVA, codice fiscale)`."""
    customer = (invoice.snapshot or {}).get("customer")
    if customer is None:
        return "Cliente non disponibile", None, None
    return customer["legal_name"], customer.get("vat_number"), customer.get("tax_code")


def _number(invoice: Invoice) -> str:
    return identifier_cell(f"{invoice.year}/{invoice.number}")


def _register_rows(
    invoice: Invoice, lines: list[InvoiceLine]
) -> list[tuple[list[str], Decimal, Decimal]]:
    """The invoice's rows with the taxable amount and the VAT each carries."""
    name, vat_number, tax_code = _party(invoice)
    head = [
        _number(invoice),
        invoice.issue_date.isoformat() if invoice.issue_date else "",
        text_cell(name),
        identifier_cell(vat_number),
        identifier_cell(tax_code),
        invoice.sdi_document_type,
    ]
    tail = [
        PAYMENT.get(invoice.payment_status, invoice.payment_status),
        invoice.payment_date.isoformat() if invoice.payment_date else "",
    ]
    rows = [
        (
            [
                *head,
                amount_cell(group.vat_rate),
                text_cell(group.vat_nature),
                text_cell(group.legal_reference),
                amount_cell(group.taxable_amount),
                amount_cell(group.vat_amount),
                *tail,
            ],
            group.taxable_amount,
            group.vat_amount,
        )
        for group in build_recap([_computed(line) for line in lines])
    ]
    # The document's stored totals are what the year register sums. An invoice written here
    # agrees with its lines by construction; one imported from elsewhere carries the totals it
    # was issued with, and a difference is shown as a row of its own so the register still
    # adds up to them.
    taxable_gap = invoice.taxable_amount - sum_money(taxable for _, taxable, _ in rows)
    vat_gap = invoice.vat_amount - sum_money(vat for _, _, vat in rows)
    if taxable_gap or vat_gap:
        rows.append(
            (
                [
                    *head,
                    "",
                    text_cell(RECONCILIATION),
                    "",
                    amount_cell(taxable_gap),
                    amount_cell(vat_gap),
                    *tail,
                ],
                taxable_gap,
                vat_gap,
            )
        )
    return rows


def build_sales_register(ctx: PartContext) -> PartOutput:
    legal_entity = ctx.legal_entity
    if legal_entity is None:  # pragma: no cover - the registry marks the part as an entity's
        raise RuntimeError("the sales register belongs to one legal entity")
    repo = ExportRepository(ctx.session)
    documents = repo.numbered_invoices(legal_entity.id, ctx.period.date_from, ctx.period.date_to)
    issued = [invoice for invoice in documents if invoice.status == "issued"]
    lines = repo.lines([invoice.id for invoice in issued])

    out = [
        BOM,
        row_line(
            [
                text_cell(
                    f"Registro delle vendite {ctx.period.date_from.isoformat()} - "
                    f"{ctx.period.date_to.isoformat()} - {legal_entity.name}"
                )
            ]
        ),
        row_line([text_cell(column) for column in REGISTER_COLUMNS]),
    ]
    rows = 0
    taxable_amounts: list[Decimal] = []
    vat_amounts: list[Decimal] = []
    for invoice in issued:
        for cells, taxable, vat in _register_rows(invoice, lines[invoice.id]):
            out.append(row_line(cells))
            rows += 1
            taxable_amounts.append(taxable)
            vat_amounts.append(vat)
    taxable_total = sum_money(taxable_amounts)
    vat_total = sum_money(vat_amounts)
    out.append(
        row_line(
            ["Totale"] + [""] * 8 + [amount_cell(taxable_total), amount_cell(vat_total)] + [""] * 2
        )
    )

    output = PartOutput()
    output.files.append(BundleFile(REGISTER_FILE, "".join(out).encode("utf-8")))
    output.entries.append(IndexEntry(REGISTER_FILE, rows, "rows"))

    struck = _struck_file(ctx, repo, documents)
    output.files.append(BundleFile(STRUCK_FILE, struck[0].encode("utf-8")))
    output.entries.append(IndexEntry(STRUCK_FILE, struck[1], "rows"))
    return output


def _struck_file(
    ctx: PartContext, repo: ExportRepository, documents: list[Invoice]
) -> tuple[str, int]:
    legal_entity = ctx.legal_entity
    assert legal_entity is not None
    numbers: dict[int, list[int]] = defaultdict(list)
    for invoice in documents:
        if invoice.year is not None and invoice.number is not None:
            numbers[invoice.year].append(invoice.number)

    entries: list[tuple[int, int, list[str]]] = []
    for invoice in documents:
        if invoice.status != "cancelled":
            continue
        assert invoice.year is not None and invoice.number is not None
        entries.append(
            (
                invoice.year,
                invoice.number,
                [
                    _number(invoice),
                    invoice.issue_date.isoformat() if invoice.issue_date else "",
                    ANNULLED,
                    invoice.cancelled_at.isoformat() if invoice.cancelled_at else "",
                    text_cell(invoice.cancellation_reason),
                ],
            )
        )
    for year, present in numbers.items():
        # From the last number the period before this one carried, so every declared gap
        # lands in exactly one period: the one that holds the next number after it.
        after = repo.last_number_before(legal_entity.id, year, ctx.period.date_from)
        for gap in repo.declared_gaps(legal_entity.id, year, after, max(present)):
            entries.append(
                (
                    year,
                    gap.number,
                    [
                        identifier_cell(f"{year}/{gap.number}"),
                        "",
                        NOT_USED,
                        "",
                        text_cell(gap.reason),
                    ],
                )
            )
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    out = [
        BOM,
        row_line([text_cell(column) for column in STRUCK_COLUMNS]),
        *(row_line(cells) for _, _, cells in entries),
    ]
    return "".join(out), len(entries)
