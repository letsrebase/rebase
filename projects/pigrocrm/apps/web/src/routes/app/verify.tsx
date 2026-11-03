import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { EnterPage } from '@/features/auth/EnterPage'
import { takeVerifyToken } from '@rebase/analytics/verify-token'

function EnterRoute() {
  const { t } = Route.useSearch()
  // Lazy initialiser: computed once per mount, whatever else re-renders this
  // component, so `takeVerifyToken`'s one-shot read is not repeated. (React's Strict
  // Mode calls a `useState` initialiser twice in development to check it is pure; the
  // second call's result is discarded, so this stays a single effective read.)
  const [token] = useState(() => takeVerifyToken() ?? t)
  return <EnterPage token={token} />
}

export const Route = createFileRoute('/app/verify')({
  validateSearch: (search: Record<string, unknown>): { t: string } => ({
    t: typeof search.t === 'string' ? search.t : '',
  }),
  component: EnterRoute,
})
