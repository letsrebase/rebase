import { useEffect, useState, type ReactNode } from 'react'
import { BrandMark } from '@rebase/ui/brand-mark'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { LoadingState } from '@/components/LoadingState'
import { api, toProblem } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { saveResponse } from '@/lib/download'
import { tenantPrefix } from '@/lib/tenant'
import { formatDay, formatMebibytes } from './labels'
import { periodName } from './periods'

type Shared = components['schemas']['SharedExportRead']

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; shared: Shared }
  /** The link is unknown, expired or revoked, or the page could not read it: the server's own
   *  sentence says which. */
  | { kind: 'refused'; detail: string }

/** An address that carried no token: there is nothing to ask the server. */
const INCOMPLETE: State = {
  kind: 'refused',
  detail: 'Questo link non è completo: chiedi a chi te l’ha mandato di rifarlo.',
}

function PageShell({ children }: { children: ReactNode }) {
  return <div className="flex min-h-full items-center justify-center p-4">{children}</div>
}

/**
 * What the accountant opens (the approved brief, «What the accountant opens»): a page that says
 * what the link is before anything downloads, with the company and period, who shared it and when
 * it expires, and one button per ZIP with its size. No navigation and no account prompt.
 *
 * The token is the one the address carried, taken out of it before this renders
 * (`stripVerifyToken`), and goes to the server only in a POST body: it is in no URL, no history
 * entry and no `Referer`.
 */
export function SharedExportPage({ token }: { token: string }) {
  const [state, setState] = useState<State>(token === '' ? INCOMPLETE : { kind: 'loading' })
  const [busy, setBusy] = useState<number | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  useEffect(() => {
    if (token === '') return
    let current = true
    void api
      .POST('/api/accountant-export/shared/describe', { body: { token } })
      .then(({ data, error, response }) => {
        if (!current) return
        setState(
          data !== undefined
            ? { kind: 'ready', shared: data }
            : { kind: 'refused', detail: toProblem(error, response.status).detail },
        )
      })
      .catch((error: unknown) => {
        if (current) setState({ kind: 'refused', detail: toProblem(error).detail })
      })
    return () => {
      current = false
    }
  }, [token])

  async function download(part: number) {
    setBusy(part)
    setFailure(null)
    try {
      const response = await fetch(`${tenantPrefix}/api/accountant-export/shared/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, part }),
      })
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null)
        throw toProblem(payload, response.status)
      }
      await saveResponse(response)
    } catch (error) {
      setFailure(toProblem(error).detail)
    } finally {
      setBusy(null)
    }
  }

  if (state.kind === 'loading') {
    return (
      <PageShell>
        <LoadingState label="Apro il link…" />
      </PageShell>
    )
  }
  if (state.kind === 'refused') {
    return (
      <PageShell>
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="inline-flex items-center text-2xl">
              <BrandMark className="mr-2.5 size-3.5" />
              Il link non funziona
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p role="alert" className="text-sm text-destructive">
              {state.detail}
            </p>
          </CardContent>
        </Card>
      </PageShell>
    )
  }

  const { shared } = state
  const many = shared.parts.length > 1
  return (
    <PageShell>
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="inline-flex items-center text-2xl">
            <BrandMark className="mr-2.5 size-3.5" />
            Esportazione per il commercialista
          </CardTitle>
          <CardDescription>
            {shared.company} · {periodName(shared.period_from, shared.period_to)}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {shared.shared_by === null ? 'Condivisa' : `Condivisa da ${shared.shared_by}`}. Il link
            scade il {formatDay(shared.expires_at)}.
          </p>
          {many ? (
            <p className="text-sm text-muted-foreground">
              {shared.parts.length} file ZIP: ognuno si apre da solo, e l’indice dentro il primo dice
              quale contiene cosa.
            </p>
          ) : null}
          <div className="flex flex-col gap-2">
            {shared.parts.map((part) => (
              <Button
                key={part.part}
                variant={many ? 'outline' : 'default'}
                className="w-full justify-between"
                disabled={busy !== null}
                onClick={() => void download(part.part)}
              >
                <span>{many ? `Scarica il file ${part.part} di ${shared.parts.length}` : 'Scarica lo ZIP'}</span>
                <span className="ml-3 tabular-nums opacity-80">{formatMebibytes(part.size_bytes)}</span>
              </Button>
            ))}
          </div>
          {failure === null ? null : (
            <p role="alert" className="text-sm text-destructive">
              {failure}
            </p>
          )}
        </CardContent>
      </Card>
    </PageShell>
  )
}
