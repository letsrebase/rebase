import { createFileRoute } from '@tanstack/react-router'
import { AccountPage } from '@/features/cash/account/AccountPage'

export const Route = createFileRoute('/app/cash/accounts/$accountId')({
  component: AccountPage,
})
