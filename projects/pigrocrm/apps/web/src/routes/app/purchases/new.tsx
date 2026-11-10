import { createFileRoute } from '@tanstack/react-router'
import { PurchaseFormPage } from '@/features/purchases/PurchaseFormPage'
import { parseNewPurchaseSearch } from '@/features/purchases/formSearch'

function NewPurchaseRoute() {
  const defaults = Route.useSearch()
  return <PurchaseFormPage defaults={defaults} />
}

/** «Nuovo acquisto»: the form of a purchase that does not exist yet, with the file optional.
 *  Registering a file from «In arrivo» is this page with `inbox_item_id`. */
export const Route = createFileRoute('/app/purchases/new')({
  component: NewPurchaseRoute,
  validateSearch: parseNewPurchaseSearch,
})
