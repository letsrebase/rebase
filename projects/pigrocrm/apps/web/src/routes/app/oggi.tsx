import { createFileRoute, redirect } from '@tanstack/react-router'
import { attributionOf } from '@/lib/attribution'

/**
 * «Oggi» is the first view of the Tasks page since REB-873. The path keeps answering
 * because the Monday digest, bookmarks and old links point at it (every route that existed
 * keeps answering); the redirect is the router's own, so the address bar shows the page.
 *
 * `?da=digest` (now `?from=digest`) is on the digest's «Vai a Oggi» link (it is how PostHog recognises a return
 * from the mail), so it goes along, as the profile route's redirect does for the same
 * parameter. The search is typed to `from` alone: an open `Record<string, unknown>` would leak
 * an index signature into every `navigate({ search })` of the app.
 */
export const Route = createFileRoute('/app/oggi')({
  validateSearch: (search: Record<string, unknown>): { from?: string } => ({
    from: attributionOf(search),
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/app/activities', search: { view: 'today', from: search.from }, replace: true })
  },
})
