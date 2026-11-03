import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (the Italian page names retired in the English rename): `legal-entities.tsx`
// is the page now. Bookmarks, mailed links and the stubs of the 2026-09-21 rename that
// still point here (`issuer`, `fiscal`) keep working.
export const Route = createFileRoute('/app/settings/aziende')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/legal-entities' })
  },
})
