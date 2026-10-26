import { createFileRoute, redirect } from '@tanstack/react-router'
import { ProfilePanel } from '@/features/settings/ProfilePanel'

/**
 * `?da=digest` is on every link of the weekly digest mail (it is how PostHog recognises a
 * return from the mail), and its opt-out link used to land on the switch here. The switch
 * lives in Notifiche now (REB-771's rule R4, REB-735), so a visit carrying `da` is sent there
 * with it; a plain visit stays on Profilo. `da` is the only parameter that link carries, and
 * the search is typed to it: an open `Record<string, unknown>` would leak an index signature
 * into every `navigate({ search })` of the app.
 */
export const Route = createFileRoute('/app/settings/profile')({
  validateSearch: (search: Record<string, unknown>): { da?: string } => ({
    da: typeof search.da === 'string' ? search.da : undefined,
  }),
  beforeLoad: ({ search }) => {
    if (search.da === 'digest') {
      throw redirect({ to: '/app/settings/notifications', search: { da: search.da }, replace: true })
    }
  },
  component: ProfilePanel,
})
