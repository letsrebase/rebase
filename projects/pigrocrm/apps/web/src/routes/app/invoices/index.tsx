import { createFileRoute } from '@tanstack/react-router'
import { InvoicesList } from '@/features/invoices/InvoicesList'
import { legacyParam } from '@/lib/legacySearch'
import { booleanSearchParam } from '@/lib/searchParams'

function InvoicesPage() {
  const { overdue } = Route.useSearch()
  return <InvoicesList overdue={overdue} />
}

export const Route = createFileRoute('/app/invoices/')({
  component: InvoicesPage,
  // Declared so the operational dashboard can link here with its "scaduto e non incassato"
  // drill-through: `<Link to={...} search={...} />` is typed against this function's
  // *return* type. `overdue` is the only search param this list reads.
  validateSearch: (search: Record<string, unknown>): { overdue?: boolean } => ({
    // The Italian parameter is what the dashboard's links and mails written before the English rename say.
    overdue: booleanSearchParam(legacyParam(search, 'overdue', 'scadute')),
  }),
})
