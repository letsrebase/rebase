import { createFileRoute, redirect } from '@tanstack/react-router'
import { attributionOf } from '@/lib/attribution'
import { ProfilePanel } from '@/features/settings/ProfilePanel'

/**
 * `?from=digest` is on every link of the weekly digest mail (it is how PostHog recognises a
 * return from the mail), and its opt-out link used to land on the switch here. The switch
 * lives in Notifiche now (REB-771's rule R4, REB-735), so a visit carrying `from` is sent there
 * with it; a plain visit stays on Profilo. `from` is the only parameter that link carries, and
 * the search is typed to it: an open `Record<string, unknown>` would leak an index signature
 * into every `navigate({ search })` of the app.
 */
export const Route = createFileRoute('/app/settings/profile')({
  validateSearch: (search: Record<string, unknown>): { from?: string } => ({
    from: attributionOf(search),
  }),
  beforeLoad: ({ search }) => {
    if (search.from === 'digest') {
      throw redirect({ to: '/app/settings/notifications', search: { from: search.from }, replace: true })
    }
  },
  component: ProfilePanel,
})
