import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { SharedExportPage } from '@/features/export/SharedExportPage'
import { takeVerifyToken } from '@rebase/analytics/verify-token'

function SharedExportRoute() {
  const { t } = Route.useSearch()
  // Computed once per mount: `main.tsx` took the token out of the address before anything
  // rendered, and `takeVerifyToken` hands it over once; the search param is only the fallback for
  // whatever reaches the page without it (a test rendering the route directly).
  const [token] = useState(() => takeVerifyToken() ?? t)
  return <SharedExportPage token={token} />
}

/**
 * `/app/shared-export?t=...` (REB-1047): the page an accountant opens from a share link, with no
 * account. One of the public routes of the shell (`PUBLIC_APP_ROUTES`), like `/app/verify`.
 */
export const Route = createFileRoute('/app/shared-export')({
  validateSearch: (search: Record<string, unknown>): { t: string } => ({
    t: typeof search.t === 'string' ? search.t : '',
  }),
  component: SharedExportRoute,
})
