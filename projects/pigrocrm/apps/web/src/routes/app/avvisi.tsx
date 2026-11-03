import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support: the page is `reminders.tsx` since the English rename of 2026-11-02.
// Push notifications and bookmarks that carry `/app/avvisi` keep answering.
export const Route = createFileRoute('/app/avvisi')({
  beforeLoad: () => {
    throw redirect({ to: '/app/reminders', replace: true })
  },
})
