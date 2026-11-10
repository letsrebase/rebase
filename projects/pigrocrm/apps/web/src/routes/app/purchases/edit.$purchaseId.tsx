import { createFileRoute } from '@tanstack/react-router'
import { PurchaseFormPage } from '@/features/purchases/PurchaseFormPage'

function EditPurchaseRoute() {
  const { purchaseId } = Route.useParams()
  return <PurchaseFormPage purchaseId={purchaseId} />
}

/** The same form as «Nuovo acquisto», seeded with the purchase it edits. Not nested under the
 *  purchase's own page, which is a page and not a layout. */
export const Route = createFileRoute('/app/purchases/edit/$purchaseId')({
  component: EditPurchaseRoute,
})
