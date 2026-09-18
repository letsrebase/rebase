import { Link, useNavigate, useSearch } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { ApiError } from '@/lib/api'
import { takeEntraToken } from '@/lib/entra-token'
import { useEnter } from '@/lib/member'

/** Where the mail's link lands. The token is posted from here, once, and never fetched
 *  by the link itself: a scanner that opens every link in a message does not run this
 *  page, so it cannot spend the token.
 *
 *  Driven by the mutation's promise rather than its `onSuccess`/`isError`: under
 *  `StrictMode` the effect's double-invoke unsubscribes and resubscribes the mutation
 *  observer, and `@tanstack/query-core` does not re-add an observer that
 *  `onUnsubscribe` already dropped from the running mutation, so those callbacks never
 *  fire again. The promise `mutateAsync` returns does not depend on the observer. */
export function Entra() {
  const { t: fromSearch } = useSearch({ strict: false }) as { t?: string }
  // Lazy initialiser: computed once per mount, whatever else re-renders this
  // component, so `takeEntraToken`'s one-shot read is not repeated. (React's Strict
  // Mode calls a `useState` initialiser twice in development to check it is pure; the
  // second call's result is discarded, so this stays a single effective read.) Falls
  // back to the search param for whatever reaches this page without `main.tsx` having
  // stripped the token first (REB-273), the story this file's own tests render.
  const [t] = useState<string | undefined>(() => takeEntraToken() ?? fromSearch)
  const navigate = useNavigate()
  const { mutateAsync } = useEnter()
  const started = useRef(false)
  const [failure, setFailure] = useState<'invalid' | 'other' | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (started.current || !t) return
    started.current = true
    mutateAsync(t).then(
      () => void navigate({ to: '/io', replace: true }),
      (error: unknown) => {
        const refusal = error instanceof ApiError ? error : null
        setFailure(refusal?.status === 401 ? 'invalid' : 'other')
        setMessage(refusal?.message ?? null)
      },
    )
  }, [t, mutateAsync, navigate, attempt])

  function retry() {
    started.current = false
    setFailure(null)
    setMessage(null)
    setAttempt((n) => n + 1)
  }

  if (!t || failure === 'invalid') {
    return (
      <div className="mx-auto max-w-xl space-y-4 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Questo link non funziona</h1>
        <p role="alert" className="text-muted-foreground">
          Il link non è più valido: vale quindici minuti e una volta sola.
        </p>
        <Link to="/accedi" className="text-sm underline underline-offset-2">
          Chiedine un altro
        </Link>
      </div>
    )
  }

  if (failure === 'other') {
    return (
      <div className="mx-auto max-w-xl space-y-4 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Non siamo riusciti a farti entrare</h1>
        <p role="alert" className="text-muted-foreground">
          {message ?? 'Qualcosa è andato storto. Riprova.'}
        </p>
        <Button onClick={retry}>Riprova</Button>
      </div>
    )
  }

  return <p className="text-center text-sm text-muted-foreground">Un attimo, ti facciamo entrare…</p>
}
