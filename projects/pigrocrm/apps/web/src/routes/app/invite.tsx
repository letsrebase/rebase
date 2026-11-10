import { createFileRoute } from '@tanstack/react-router'
import { InviteEntry } from '@/features/auth/InvitePage'

export const Route = createFileRoute('/app/invite')({
  validateSearch: (search: Record<string, unknown>): { t: string } => ({
    t: typeof search.t === 'string' ? search.t : '',
  }),
  component: function InviteRoute() {
    const { t } = Route.useSearch()
    return <InviteEntry t={t} />
  },
})
