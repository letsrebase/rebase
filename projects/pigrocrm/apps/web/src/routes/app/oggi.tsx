import { createFileRoute, redirect } from '@tanstack/react-router'

// «Oggi» is the first view of the Attività page since REB-873. The path keeps answering
// because the Monday digest, bookmarks and old links point at it (every route that existed
// keeps answering); the redirect is the router's own, so the address bar shows the page.
export const Route = createFileRoute('/app/oggi')({
  beforeLoad: () => {
    throw redirect({ to: '/app/activities', search: { vista: 'oggi' } })
  },
})
