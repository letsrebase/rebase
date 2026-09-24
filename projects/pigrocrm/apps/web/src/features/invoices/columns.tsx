import type { ColumnDef } from '@tanstack/react-table'
import { DateCell, MoneyCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { StatusPill } from '@/components/StatusPill'
import { InvoiceStateBadge } from './InvoiceStateBadge'
import { formatDate, formatInvoiceNumber, formatMoney, formatPeriod } from './format'
import {
  INVOICE_TYPE_LABELS,
  PAYMENT_STATE_LABELS,
  PAYMENT_STATE_TONE,
  type Invoice,
  type StatoPagamento,
} from './queries'

const EMPTY = '—'

/**
 * `cliente` adds the «Cliente» column (ORB-98). Opt-in rather than always there because
 * the same columns draw two screens: the list page, which mixes every customer's
 * invoices and is where a row has to say whose it is, and the Fatture tab inside a
 * customer's or a deal's page, where every row belongs to the customer already named in
 * the title and the column would only repeat it on each line.
 */
export interface InvoiceColumnOptions {
  cliente?: boolean
}

/**
 * The name the API resolved for the row, or the dash. `customer_ragione_sociale` is
 * `str | None` on the server: the association always exists (`customer_id` is NOT NULL),
 * the name may fail to resolve, and that reads as the same em dash every other empty
 * cell shows rather than as an empty string a reader could take for a nameless customer.
 * `''` gets the same dash: `ragione_sociale` is a `SafeStr` with no minimum length, so an
 * empty name is a value the column can legitimately hold, and a blank cell would read as
 * a rendering fault rather than as what it is.
 */
function customerName(row: Invoice): string {
  return textOrDash(row.customer_ragione_sociale)
}

/** `null`, `undefined` and `''` are all "nothing here", as everywhere else in this table. */
function textOrDash(value: string | null | undefined): string {
  return value === null || value === undefined || value === '' ? EMPTY : value
}

/**
 * The accrual period the document declares (ORB-61), as the detail page states it, or
 * the dash. One helper for the accessor and the cell, as `customerName` above: the two
 * halves of a column disagreeing about what nothing looks like is a defect this file
 * has already shipped once (see the `data_emissione` column).
 */
function accrualPeriod(row: Invoice): string {
  return formatPeriod(row.competenza_da, row.competenza_a)
}

/** The causale, or the dash. */
function description(row: Invoice): string {
  return textOrDash(row.causale)
}

export function buildInvoiceColumns(
  options: InvoiceColumnOptions = {},
): ColumnDef<DataTableFeatures, Invoice>[] {
  const cliente: ColumnDef<DataTableFeatures, Invoice>[] = options.cliente
    ? [
        {
          header: 'Cliente',
          id: 'cliente',
          accessorFn: (row) => customerName(row),
          // Plain text, like «Tipo»: the table is auto-layout and `TableCell` already
          // says `whitespace-nowrap`, so a long name widens its column and the table
          // scrolls inside its frame, the way every other text column here behaves. A
          // `truncate` on an inline span in a `<td>` with no width would promise an
          // ellipsis the DOM cannot deliver.
          cell: ({ row }) => {
            const name = customerName(row.original)
            return name === EMPTY ? <span className="text-muted-foreground">{EMPTY}</span> : name
          },
        },
      ]
    : []

  return [
    {
      header: 'Numero',
      id: 'numero',
      // A fiscal number for an invoice, a reference for a proforma. The two are
      // different kinds of identity, not two spellings of one -- which is why
      // `formatInvoiceNumber` cannot derive a reference from a number.
      accessorFn: (row) => formatInvoiceNumber(row),
    },
    ...cliente,
    {
      header: 'Descrizione',
      id: 'descrizione',
      accessorFn: (row) => description(row),
      // The one flexible column in this table (ORB-130). A causale runs to 200
      // characters, and a column sized to its content, or even to a fixed maximum,
      // pushed the other eight past the frame at 1440px (measured: 24rem left the table
      // 280px wider than its box). So it is sized the other way round: the header asks
      // for `100%`, which in an auto-layout table means "whatever the others leave", and
      // the span is a block of width zero whose minimum is the whole cell, so the text
      // truncates at exactly the width the column got. `truncate` works here where it
      // did not on the customer's name because the block has a width to clip against.
      //
      // The 12rem inside the `max()` is a floor: without it the column shrank to its
      // header at 1280px (measured, 81px) and read as an ellipsis, which defeats the
      // column. A percentage resolves to zero while the table measures its minimum, so
      // the floor is the only part of the minimum the cell contributes; at 1440px with the
      // sidebar open it costs about 70px of horizontal scroll inside the frame, which the
      // table already handles, and from 1512px up nothing scrolls. The whole causale
      // stays reachable as the cell's title.
      meta: { width: '100%' },
      cell: ({ row }) => {
        const causale = description(row.original)
        if (causale === EMPTY) return <span className="text-muted-foreground">{EMPTY}</span>
        return (
          <span className="block w-0 min-w-[max(100%,12rem)] truncate" title={causale}>
            {causale}
          </span>
        )
      },
    },
    {
      header: 'Tipo',
      id: 'tipo',
      accessorFn: (row) => INVOICE_TYPE_LABELS[row.tipo as keyof typeof INVOICE_TYPE_LABELS],
    },
    {
      header: 'Stato',
      id: 'stato',
      // `importata={false}` suppresses only the uninformative «importata» pill an
      // "esterno" row would otherwise get (ORB-130: in a register where most rows came
      // from the previous system the word explains nothing a reader of the list can act
      // on -- the detail page keeps it, as the reason its XML actions are missing). A
      // "fatturapa" row's own badge ignores the flag and still shows here: it names a
      // fact the list can act on (design 2026-09-23 §5 item 4/§7 item 6).
      cell: ({ row }) => <InvoiceStateBadge invoice={row.original} importata={false} />,
    },
    {
      header: 'Data',
      id: 'data_emissione',
      // The accessor keeps the formatted string -- it is what a future sort or export
      // reads, and it is what this column's own tests assert -- while `cell` adds the
      // calendar icon the reference puts before every date (design spec §4). `DateCell`
      // formats the raw ISO value itself, through `lib/dates.ts`, which renders the
      // identical string this accessor does.
      accessorFn: (row) => formatDate(row.data_emissione),
      cell: ({ row }) => <DateCell value={row.original.data_emissione} />,
    },
    {
      header: 'Competenza',
      id: 'competenza',
      // Beside the date the document was issued on (ORB-126): invoicing runs late here,
      // so the two often name different months, and a list showing only the second does
      // not say what a row is about. Plain text: the emission date is the one column with
      // a calendar icon, and a second one two columns over would make two different kinds
      // of value read as the same.
      accessorFn: (row) => accrualPeriod(row),
      cell: ({ row }) => {
        const period = accrualPeriod(row.original)
        return period === EMPTY ? <span className="text-muted-foreground">{EMPTY}</span> : period
      },
    },
    {
      header: 'Totale',
      id: 'totale',
      // `formatMoney` parses the decimal string into integer cents; nothing here ever
      // sees a float. `meta.align` right-aligns the header over the digits as well --
      // `MoneyCell` alone could only align what is inside the cell.
      accessorFn: (row) => formatMoney(row.totale),
      meta: { align: 'right' },
      cell: ({ row }) => <MoneyCell>{formatMoney(row.original.totale)}</MoneyCell>,
    },
    {
      header: 'Pagamento',
      id: 'stato_pagamento',
      // A proforma is never collected. Showing "Da incassare" beside something nobody
      // owes is the kind of small lie a fiscal list should not tell, so it reads as
      // absent rather than as unpaid -- and as the plain dash, never as a pill: a pill
      // is a claim about a state, and there is no state here to claim.
      accessorFn: (row) =>
        row.tipo === 'proforma'
          ? EMPTY
          : PAYMENT_STATE_LABELS[row.stato_pagamento as keyof typeof PAYMENT_STATE_LABELS],
      cell: ({ row }) => {
        if (row.original.tipo === 'proforma') {
          return <span className="text-muted-foreground">{EMPTY}</span>
        }
        // Both maps are total over `StatoPagamento`, so neither read takes a fallback:
        // one would be a branch the types make unreachable.
        const stato = row.original.stato_pagamento as StatoPagamento
        return <StatusPill tone={PAYMENT_STATE_TONE[stato]}>{PAYMENT_STATE_LABELS[stato]}</StatusPill>
      },
    },
  ]
}
