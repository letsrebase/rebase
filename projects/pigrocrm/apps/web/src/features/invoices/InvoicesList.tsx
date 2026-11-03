import { Link, useNavigate } from '@tanstack/react-router'
import { Receipt } from 'lucide-react'
import { useState } from 'react'
import { DataTable } from '@/components/DataTable'
import { FilterChips, FilterRow } from '@/components/FilterRow'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { buildInvoiceColumns } from '@/features/invoices/columns'
import { ImportInvoicesButton } from '@/features/invoices/ImportInvoicesDialog'
import { LoadMoreInvoices } from '@/features/invoices/LoadMoreInvoices'
import { NewProformaButton } from '@/features/invoices/NewProformaDialog'
import { useLegalEntitiesToName } from '@/lib/legalEntity'
import {
  INVOICE_STATE_LABELS,
  PAYMENT_STATE_LABELS,
  useInvoicesList,
  type InvoiceStatus,
  type InvoiceType,
  type PaymentStatus,
} from '@/features/invoices/queries'

/** `all` is a UI-only value: the API filter is simply absent when nothing is
 *  selected, and a `Select` needs a non-empty string to represent "no filter". The
 *  state filter has no such sentinel any more -- it is a row of chips, where "no
 *  filter" is `null` (see `FilterChips`). */
const ANY = 'all'

const TYPE_OPTIONS = [
  { value: ANY, label: 'Tutti i tipi' },
  { value: 'invoice', label: 'Fatture' },
  { value: 'proforma', label: 'Proforma' },
]

/** The five fiscal states, as chips, in the order `INVOICE_STATE_LABELS` declares them:
 *  that map is next to the tones and the transitions on the server, so a state added
 *  there appears here without this file changing. */
const Statuses = Object.entries(INVOICE_STATE_LABELS).map(([value, label]) => ({
  value: value as InvoiceStatus,
  label,
}))

/** The two payment states, as a second chip row (REB-325: a filter for the unpaid
 *  invoices). Built from the same map the `Pagamento` column's pill reads,
 *  so the chip and the pill can never spell a state two ways. */
const Receipts = Object.entries(PAYMENT_STATE_LABELS).map(([value, label]) => ({
  value: value as PaymentStatus,
  label,
}))

/**
 * Exported so `index.test.tsx` can render the list without a router: the route
 * component below is what reads `overdue` out of the URL, and this is what draws the
 * page. The split is the same one `CustomersPage`/`PeoplePage` already make for their
 * own `?search=` term.
 */
export function InvoicesList({ overdue }: { overdue?: boolean }) {
  const navigate = useNavigate()
  const [type, setType] = useState(ANY)
  const [status, setStatus] = useState<InvoiceStatus | null>(null)
  const [collection, setCollection] = useState<PaymentStatus | null>(null)

  // Cast at the boundary, not in the state: `Select` deals in strings, and the type
  // option list is built from the same constants the API's literals come from, so a
  // value that is not a legal `InvoiceType` cannot be produced here. `status` needs no
  // cast at all since the chips carry the literal itself.
  //
  // `overdue` comes from the URL rather than from a control, because it is a
  // drill-through and not a filter of this page's own: the operational dashboard counts
  // overdue invoices with `_overdue_predicate` in `InvoiceRepository`, and this list is
  // the same predicate evaluated on the same rows (criterion 2). Re-deciding "overdue"
  // here -- comparing `due_date` against today's date in the browser -- is how the
  // count and the list start disagreeing on an invoice due today.
  //
  // «Tutte» is every document that stands on its own. A consumed proforma does not: its
  // number lives on the invoice it became, and listing both doubled every issued proforma
  // (ORB-169). So with no state chosen the server leaves them out; the «Consumata» chip
  // asks for exactly them and gets them.
  //
  // `collection` is the payment row, orthogonal to the state row: «Emessa» + «Incassato»
  // is a legal pair and sends both. What «Da incassare» selects is decided on the server
  // (`InvoiceRepository.list`, REB-325): an issued fattura nobody has paid, the rows the
  // dashboard's «Da incassare» card counts, and never a draft or a proforma although
  // every row carries `unpaid` from birth. Deciding that here would be the second
  // definition of a receivable this product has, and the two would drift.
  const invoices = useInvoicesList({
    type: type === ANY ? undefined : (type as InvoiceType),
    status: status ?? undefined,
    payment_status: collection ?? undefined,
    exclude_consumed: status === null ? true : undefined,
    overdue,
  })

  // With the customer: this is the one screen that mixes every customer's invoices, so
  // a row has to say whose it is (ORB-98). The Fatture tab on a customer's or a deal's
  // page draws the same columns without it -- see `InvoicesTab`.
  // And the legal entity's name beside the number, only under «Tutte le aziende» from the
  // second legal entity on (REB-625): with one selected the sidebar already says whose.
  const columns = buildInvoiceColumns({ customer: true, legal_entities: useLegalEntitiesToName() })

  return (
    <>
      <PageHeader
        icon={Receipt}
        title="Fatture"
        description="Una fattura si emette da un deal, da un cliente o da qui, creando una proforma da confermare ed emettere. Le proforma si distinguono dal riferimento al posto del numero: non sono documenti fiscali finché non vengono emesse."
        actions={
          <>
            <ImportInvoicesButton />
            <NewProformaButton />
          </>
        }
      >
        <FilterRow>
          <FilterChips
            label="Filtra per stato"
            allLabel="Tutte"
            options={Statuses}
            value={status}
            onChange={setStatus}
          />

          <FilterChips
            label="Filtra per incasso"
            allLabel="Tutte"
            options={Receipts}
            value={collection}
            onChange={setCollection}
          />

          <Select value={type} onValueChange={setType}>
            <SelectTrigger className="w-48" aria-label="Filtra per tipo">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TYPE_OPTIONS.map((entry) => (
                <SelectItem key={entry.value} value={entry.value}>
                  {entry.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterRow>
      </PageHeader>

      <div className="px-8 pb-8">
        {overdue === true && (
          <p
            role="status"
            className="mb-4 flex flex-wrap items-center gap-2 border bg-card px-3 py-2 text-sm"
          >
            <span>
              <strong>Scadute e non incassate</strong> — solo le fatture emesse la cui scadenza è
              passata e che non risultano incassate. Togli il filtro per vedere tutte le fatture.
            </span>
            <Button variant="outline" size="sm" asChild>
              <Link to="/app/invoices" search={{}}>
                Rimuovi il filtro
              </Link>
            </Button>
          </p>
        )}

        <DataTable
          label="Fatture"
          columns={columns}
          data={invoices.items}
          isLoading={invoices.isLoading}
          isError={invoices.isError}
          error={invoices.error}
          onRowClick={(row) =>
            void navigate({ to: '/app/invoices/$invoiceId', params: { invoiceId: row.id } })
          }
          emptyMessage="Nessuna fattura."
        />
        {invoices.hasMore && (
          <LoadMoreInvoices
            shown={invoices.items.length}
            isFetchingMore={invoices.isFetchingMore}
            hasError={invoices.loadMoreError}
            error={invoices.error}
            onLoadMore={invoices.loadMore}
          />
        )}
      </div>
    </>
  )
}
