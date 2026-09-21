import { useNavigate } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { DataTable } from '@/components/DataTable'
import { buildInvoiceColumns } from './columns'
import { LoadMoreInvoices } from './LoadMoreInvoices'
import { useInvoicesForOwner, type InvoiceOwner } from './queries'

/**
 * The Fatture tab on a customer's or a deal's detail page.
 *
 * It reads through `useInvoicesForOwner`, which is a thin wrapper over the same hook
 * the list page uses, so both share one cache entry and one query-key shape: an invoice
 * issued from here appears on the list page without a second round trip, and the two
 * screens cannot drift into disagreeing about the same rows.
 *
 * `actions` is the tab's own top-right slot -- where a customer page puts «Nuova
 * fattura». It lives here and not in the page header because the header is shared by
 * every tab: a button that creates an invoice would be offered from Panoramica, from
 * Timeline and from Documenti too, next to the verbs that really do belong to the whole
 * record. The tab knows nothing about what it is given; it only reserves the row.
 */
export function InvoicesTab({ owner, actions }: { owner: InvoiceOwner; actions?: ReactNode }) {
  const navigate = useNavigate()
  const invoices = useInvoicesForOwner(owner)

  const table = (
    <>
      <DataTable
        columns={buildInvoiceColumns()}
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
    </>
  )

  if (actions === undefined) return table

  return (
    <div className="space-y-4">
      <div className="flex justify-end">{actions}</div>
      {table}
    </div>
  )
}
