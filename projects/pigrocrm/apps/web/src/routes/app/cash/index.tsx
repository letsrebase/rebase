import { createFileRoute } from '@tanstack/react-router'
import { CashPage } from '@/features/cash/CashPage'

export const Route = createFileRoute('/app/cash/')({
  component: CashPage,
})
