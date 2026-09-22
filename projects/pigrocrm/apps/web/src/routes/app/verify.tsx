import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { toProblem } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { homeAfterEntry } from '@/lib/entry-home'
import { takeEntraToken } from '@/lib/entra-token'
import { tenantPrefix } from '@/lib/tenant'

/**
 * The page a link by mail lands on (spec 2026-09-12 §6.2). It spends the token once,
 * then leaves for the home with a full navigation: the cookies were just set for this
 * basepath and the rest of the application should start from nothing but them. A dead
 * link (spent, expired, made up) shows the API's sentence and the way back to the login,
 * where another one can be asked.
 *
 * The token itself never comes from this component's own search params (REB-229):
 * `main.tsx` already took it out of the URL, before analytics or this route ever ran,
 * with `lib/entra-token.ts`'s `stripEntraToken`. `EnterRoute` below reads it back with
 * `takeEntraToken` and falls back to the search param only for whatever reaches this
 * component without that having happened first (a unit test rendering `EnterPage`
 * directly, as this file's own tests do).
 *
 * `go` is injectable because jsdom does not let a test spy on `window.location.assign`.
 * The token is spent once per mount, however often the page re-renders.
 */
export function EnterPage({
  token,
  go = (url) => window.location.assign(url),
}: {
  token: string
  go?: (url: string) => void
}) {
  const { enterWithLink } = useAuth()
  const [error, setError] = useState<string | null>(null)
  // Once per mount, whatever re-renders: `enterWithLink` is a fresh closure every time
  // `AuthProvider` renders (and it renders while the mutation is pending), so an effect
  // keyed on it would spend the token a second time and read its own 401 as a dead link.
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    void (async () => {
      try {
        await enterWithLink(token)
        go(await homeAfterEntry())
      } catch (caught) {
        setError(toProblem(caught).detail)
      }
    })()
  }, [token, enterWithLink, go])

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="inline-flex items-center text-2xl">
            <BrandMark className="mr-2.5 size-3.5" />
            {error ? 'Il link non funziona' : 'Un momento…'}
          </CardTitle>
          {!error && <CardDescription>Stiamo aprendo il tuo spazio.</CardDescription>}
        </CardHeader>
        {error && (
          <CardContent className="space-y-4">
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
            <Button asChild variant="outline" className="w-full">
              <a href={`${tenantPrefix}/app/login`}>Torna al login e chiedi un altro link</a>
            </Button>
          </CardContent>
        )}
      </Card>
    </div>
  )
}

function EnterRoute() {
  const { t } = Route.useSearch()
  // Lazy initialiser: computed once per mount, whatever else re-renders this
  // component, so `takeEntraToken`'s one-shot read is not repeated. (React's Strict
  // Mode calls a `useState` initialiser twice in development to check it is pure; the
  // second call's result is discarded, so this stays a single effective read.)
  const [token] = useState(() => takeEntraToken() ?? t)
  return <EnterPage token={token} />
}

export const Route = createFileRoute('/app/verify')({
  validateSearch: (search: Record<string, unknown>): { t: string } => ({
    t: typeof search.t === 'string' ? search.t : '',
  }),
  component: EnterRoute,
})
