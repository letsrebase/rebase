import { createFileRoute } from '@tanstack/react-router'
import { CostCategoriesPanel } from '@/features/settings/CostCategoriesPanel'

export const Route = createFileRoute('/app/settings/cost-categories')({
  component: CostCategoriesPanel,
})
