import { createFileRoute } from '@tanstack/react-router'
import { PurchasesPage } from '@/features/purchases/PurchasesPage'
import { parsePurchasesSearch } from '@/features/purchases/search'

export const Route = createFileRoute('/app/purchases/')({
  component: PurchasesPage,
  // The filters travel in the URL (the brief: the period above all), so a bookmark or a link
  // from the cash schedule lands on the same view.
  validateSearch: parsePurchasesSearch,
})
