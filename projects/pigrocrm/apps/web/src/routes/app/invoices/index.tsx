import { createFileRoute } from '@tanstack/react-router'
import { InvoicesList } from '@/features/invoices/InvoicesList'
import { booleanSearchParam } from '@/lib/searchParams'

function InvoicesPage() {
  const { scadute } = Route.useSearch()
  return <InvoicesList scadute={scadute} />
}

export const Route = createFileRoute('/app/invoices/')({
  component: InvoicesPage,
  // Declared so the operational dashboard can link here with its "scaduto e non incassato"
  // drill-through: `<Link to={...} search={...} />` is typed against this function's
  // *return* type. `scadute` is the only search param this list reads.
  validateSearch: (search: Record<string, unknown>): { scadute?: boolean } => ({
    scadute: booleanSearchParam(search.scadute),
  }),
})
