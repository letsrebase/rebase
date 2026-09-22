import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState, type ReactNode } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Skeleton } from '@rebase/ui/skeleton'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useAuth } from '@/lib/auth'
import { homeAfterEntry } from '@/lib/entry-home'
import { takeEntraToken } from '@/lib/entra-token'
import { tenantPrefix } from '@/lib/tenant'

type InvitationPeek = components['schemas']['InvitationPeek']

/** The page's own read of the token's state, one branch per render below. */
type State =
  | { kind: 'loading' }
  | { kind: 'peek'; peek: InvitationPeek }
  /** The invitation exists but is over (`invitation_expired` / `_revoked` / `_used`) or
   *  never did (`invitation_unknown`, a 404): the API's Italian sentence names which.
   *  `used` separates the one dead state whose next step is a plain login (the account
   *  already exists) from the ones that need a new link from the admin. */
  | { kind: 'dead'; detail: string; used: boolean }
  | { kind: 'error'; detail: string }

/** Whether a peek/accept failure is one of the invitation's own dead states. The
 *  `code` is what the domain problem document carries for exactly this discrimination
 *  (`STATUS_BY_CODE`, apps/api/src/pigrocrm_api/errors.py); anything else (a 5xx, the
 *  rate limiter's 429) is the page failing, not the invitation being dead. */
function isDeadCode(code: string): boolean {
  return (
    code === 'invitation_expired' ||
    code === 'invitation_revoked' ||
    code === 'invitation_used' ||
    code === 'invitation_unknown'
  )
}

function deadState(problem: { code: string; detail: string }): State {
  return { kind: 'dead', detail: problem.detail, used: problem.code === 'invitation_used' }
}

function errorState(problem: { detail: string }): State {
  return { kind: 'error', detail: problem.detail }
}

/** Centering wrapper, the same shape `EnterPage` draws: a card alone on the page. */
function PageShell({ children }: { children: ReactNode }) {
  return <div className="flex min-h-screen items-center justify-center p-4">{children}</div>
}

/**
 * The card a dead invitation renders (spec §1): its own sentence and the one next step
 * that fits the state. Shared by the peek's 410/404, the accept's race, and a link that
 * carried no token at all.
 */
function DeadInviteCard({ detail, used }: { detail: string; used: boolean }) {
  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="inline-flex items-center text-2xl">
          <BrandMark className="mr-2.5 size-3.5" />
          {"L'invito non funziona"}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-destructive text-sm" role="alert">
          {detail}
        </p>
        <Button asChild variant="outline" className="w-full">
          <a href={`${tenantPrefix}/app/login`}>
            {used ? 'Entra con la tua email' : 'Torna al login e chiedi un altro invito'}
          </a>
        </Button>
      </CardContent>
    </Card>
  )
}

/**
 * The page an invitation mail lands on (spec 2026-09-17 §1, REB-291). The mail's link is
 * `/app/invite?t=...` (`_invite_url` in routers/users.py), so this is the route the mail
 * points at; nothing else reaches it.
 *
 * Unlike `verify.tsx`, which auto-spends on mount because a magic link has nothing to
 * show, this page peeks first: the space's name and who invited the person are the
 * reason to click, and when the invitation carried no name the page asks for one before
 * spending. The peek never spends (spec §3), so a reload is safe, and the retry button
 * re-peeks rather than blindly spending.
 *
 * The token comes from `takeEntraToken`, the same shared scrubber as `verify.tsx`:
 * `main.tsx` took it out of the URL before `initAnalytics` ran, so PostHog's first
 * pageview carries no `?t=` (REB-229 extended to this path by REB-291). The search
 * param is only the fallback for a render that never went through `main.tsx` (a unit
 * test, like this file's own).
 *
 * `go` is injectable because jsdom does not let a test spy on `window.location.assign`,
 * and the landing is a full navigation, not a router push: the session cookies were just
 * set for this basepath and the rest of the application should start from nothing but
 * them, exactly like `EnterPage`.
 */
export function InvitePage({
  token,
  go = (url) => window.location.assign(url),
}: {
  token: string
  go?: (url: string) => void
}) {
  const { enterWithInvite } = useAuth()
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [nome, setNome] = useState('')
  const [entering, setEntering] = useState(false)
  // The peek effect re-runs only when this counter moves: «Riprova» is a re-peek, the
  // one right answer for a network failure or a 429 (both leave the token unspent),
  // and never a blind second accept.
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const peek = await unwrap(
          api.GET('/api/auth/invite', { params: { query: { t: token } } }),
        )
        if (!cancelled) setState({ kind: 'peek', peek })
      } catch (caught) {
        const problem = toProblem(caught)
        if (!cancelled)
          setState(isDeadCode(problem.code) ? deadState(problem) : errorState(problem))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [token, attempt])

  async function enter() {
    setEntering(true)
    try {
      // The name field only exists when the peek carried none, and then it is what the
      // accept needs; otherwise the invitation's stored name is the server's to read.
      const askedForNome = state.kind === 'peek' && state.peek.nome === null
      await enterWithInvite(token, askedForNome ? nome : undefined)
      go(await homeAfterEntry())
    } catch (caught) {
      const problem = toProblem(caught)
      // The invitation can die between the peek and the click (an admin revoked it, a
      // mail scanner raced the spend): the accept's own answer is the truth.
      setState(isDeadCode(problem.code) ? deadState(problem) : errorState(problem))
      setEntering(false)
    }
  }

  if (state.kind === 'dead') {
    return (
      <PageShell>
        <DeadInviteCard detail={state.detail} used={state.used} />
      </PageShell>
    )
  }

  return (
    <PageShell>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="inline-flex items-center text-2xl">
            <BrandMark className="mr-2.5 size-3.5" />
            {state.kind === 'peek' ? `Entra in ${state.peek.spazio}` : 'Un momento…'}
          </CardTitle>
          {state.kind === 'peek' && (
            <CardDescription>
              {state.peek.invitato_da
                ? `${state.peek.invitato_da} ti ha invitato in questo spazio.`
                : 'Sei stato invitato in questo spazio.'}
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="space-y-4">
          {state.kind === 'loading' && <Skeleton className="h-20 w-full" />}
          {state.kind === 'peek' && (
            <>
              {state.peek.nome === null ? (
                <div className="space-y-2">
                  <Label htmlFor="invite-nome">Come ti chiami?</Label>
                  <Input
                    id="invite-nome"
                    value={nome}
                    onChange={(event) => setNome(event.target.value)}
                    autoComplete="name"
                  />
                </div>
              ) : (
                <p className="text-muted-foreground text-sm">Ciao {state.peek.nome}!</p>
              )}
              <Button className="w-full" onClick={enter} disabled={entering}>
                {entering ? 'Un momento…' : 'Entra nello spazio'}
              </Button>
            </>
          )}
          {state.kind === 'error' && (
            <>
              <p className="text-destructive text-sm" role="alert">
                {state.detail}
              </p>
              <Button
                variant="outline"
                className="w-full"
                onClick={() => {
                  setEntering(false)
                  setAttempt((n) => n + 1)
                }}
              >
                Riprova
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </PageShell>
  )
}

/** The API's own sentence for a token that never was an invitation (`InvitationUnknown`,
 *  packages/core/src/pigrocrm/core/auth/invitations.py): a link that arrives with no
 *  `?t=` at all describes the same dead state, and shows the same words rather than a
 *  page-invented paraphrase of them. */
const NO_TOKEN = 'questo invito non esiste: chiedi a chi amministra lo spazio di rimandarlo'

function InviteRoute() {
  const { t } = Route.useSearch()
  // Lazy initialiser, as verify.tsx: computed once per mount, so the one-shot read of
  // the stripped token is not repeated by a re-render (or Strict Mode's double call).
  const [token] = useState(() => takeEntraToken() ?? t)
  if (!token) {
    return (
      <PageShell>
        <DeadInviteCard detail={NO_TOKEN} used={false} />
      </PageShell>
    )
  }
  return <InvitePage token={token} />
}

export const Route = createFileRoute('/app/invite')({
  validateSearch: (search: Record<string, unknown>): { t: string } => ({
    t: typeof search.t === 'string' ? search.t : '',
  }),
  component: InviteRoute,
})
