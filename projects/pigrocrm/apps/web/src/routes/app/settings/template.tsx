import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support: the page is `templates.tsx` since the English rename of 2026-11-02.
// Bookmarks keep answering.
export const Route = createFileRoute('/app/settings/template')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/templates' })
  },
})
