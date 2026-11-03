import { createFileRoute, redirect } from '@tanstack/react-router'

/**
 * «Oggi» is the first view of the Tasks page since REB-873. The path keeps answering
 * because the Monday digest, bookmarks and old links point at it (every route that existed
 * keeps answering); the redirect is the router's own, so the address bar shows the page.
 *
 * `?da=digest` is on the digest's «Vai a Oggi» link (it is how PostHog recognises a return
 * from the mail), so it goes along, as the profile route's redirect does for the same
 * parameter. The search is typed to `da` alone: an open `Record<string, unknown>` would leak
 * an index signature into every `navigate({ search })` of the app.
 */
export const Route = createFileRoute('/app/oggi')({
  validateSearch: (search: Record<string, unknown>): { da?: string } => ({
    da: typeof search.da === 'string' ? search.da : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/app/activities', search: { vista: 'oggi', da: search.da }, replace: true })
  },
})
