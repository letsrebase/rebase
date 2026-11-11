import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { BrandMark } from '@rebase/ui/brand-mark'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { Input } from '@rebase/ui/input'
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

/** Reads what a link is; `access` is the session of a right code, absent on a plain link. */
async function describeLink(token: string, access: string | null): Promise<State> {
  try {
    const { data, error, response } = await api.POST('/api/accountant-export/shared/describe', {
      body: access === null ? { token } : { token, access },
    })
    return data !== undefined
      ? { kind: 'ready', shared: data }
      : { kind: 'refused', detail: toProblem(error, response.status).detail }
  } catch (error) {
    return { kind: 'refused', detail: toProblem(error).detail }
  }
}

/**
 * What the accountant opens (the approved brief, «What the accountant opens»): a page that says
 * what the link is before anything downloads, with the company and period, who shared it and when
 * it expires, and one button per ZIP with its size. No navigation and no account prompt.
 *
 * A link that carries pay asks a one-time code first (`CodeGate`): the files show only once a
 * right code opened a session, which lives in this component's state and nowhere else.
 *
 * The token is the one the address carried, taken out of it before this renders
 * (`stripVerifyToken`), and goes to the server only in a POST body: it is in no URL, no history
 * entry and no `Referer`.
 */
export function SharedExportPage({ token }: { token: string }) {
  const [state, setState] = useState<State>(token === '' ? INCOMPLETE : { kind: 'loading' })
  const [busy, setBusy] = useState<number | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  // The session a right code opened: in memory only, never stored and never in an address.
  const [access, setAccess] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [gateKey, setGateKey] = useState(0)

  useEffect(() => {
    if (token === '') return
    let current = true
    void describeLink(token, null).then((next) => {
      if (current) setState(next)
    })
    return () => {
      current = false
    }
  }, [token])

  async function unlock(granted: string) {
    const before = state
    setAccess(granted)
    setNotice(null)
    setState({ kind: 'loading' })
    // The code is spent by now: one more try before giving up, so a blip does not cost a new code.
    let next = await describeLink(token, granted)
    if (next.kind === 'refused') next = await describeLink(token, granted)
    if (next.kind === 'refused' && before.kind === 'ready') {
      setAccess(null)
      setNotice('Non sono riuscito ad aprire i file: chiedi un nuovo codice.')
      setGateKey((key) => key + 1)
      setState(before)
      return
    }
    setState(next)
  }

  async function download(part: number) {
    setBusy(part)
    setFailure(null)
    try {
      const response = await fetch(`${tenantPrefix}/api/accountant-export/shared/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(access === null ? { token, part } : { token, part, access }),
      })
      if (!response.ok) {
        const payload: unknown = await response.json().catch(() => null)
        throw toProblem(payload, response.status)
      }
      await saveResponse(response)
    } catch (error) {
      const problem = toProblem(error)
      if (problem.code === 'export_code_required' && state.kind === 'ready') {
        // The session of the code is over: back to the code step, which asks a new one.
        setAccess(null)
        setNotice('La sessione è scaduta: chiedi un nuovo codice per riaprire i file.')
        setGateKey((key) => key + 1)
        setState({ kind: 'ready', shared: { ...state.shared, unlocked: false, parts: [] } })
      } else {
        setFailure(problem.detail)
      }
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
  const locked = shared.requires_code && !shared.unlocked
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
          {locked ? (
            <CodeGate
              key={gateKey}
              token={token}
              hint={shared.recipient_hint}
              notice={notice}
              onUnlocked={(granted) => void unlock(granted)}
            />
          ) : (
            <>
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
            </>
          )}
        </CardContent>
      </Card>
    </PageShell>
  )
}

const DIGITS = 6

/**
 * The boxes of a six-digit code: one digit each, numeric, pasting or an autofilled code spreads
 * over them, typing moves on and Backspace on an empty box goes back.
 */
function CodeBoxes({
  digits,
  onChange,
  inputs,
  labelledBy,
}: {
  digits: string[]
  onChange: (next: string[]) => void
  inputs: { current: (HTMLInputElement | null)[] }
  labelledBy: string
}) {
  function focusBox(index: number) {
    inputs.current[Math.max(0, Math.min(DIGITS - 1, index))]?.focus()
  }

  function fill(start: number, typed: string) {
    const next = [...digits]
    for (let offset = 0; offset < typed.length && start + offset < DIGITS; offset += 1) {
      next[start + offset] = typed.charAt(offset)
    }
    onChange(next)
    focusBox(start + typed.length)
  }

  function change(index: number, raw: string) {
    let typed = raw.replace(/\D/g, '')
    if (typed === '') {
      const next = [...digits]
      next[index] = ''
      onChange(next)
      return
    }
    // A box that already holds a digit: keep only what was just typed.
    const held = digits[index] ?? ''
    if (typed.length === 2 && held !== '') typed = typed.replace(held, '')
    fill(index, typed)
  }

  return (
    <div role="group" aria-labelledby={labelledBy} className="grid grid-cols-6 gap-2">
      {digits.map((digit, index) => (
        <Input
          // The six boxes never reorder: the position is the identity.
          key={index}
          ref={(element) => {
            inputs.current[index] = element
          }}
          value={digit}
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]*"
          aria-label={`Cifra ${index + 1} di ${DIGITS}`}
          className="h-11 px-0 text-center text-lg tabular-nums md:text-lg"
          onFocus={(event) => event.currentTarget.select()}
          onChange={(event) => change(index, event.target.value)}
          onPaste={(event) => {
            const pasted = event.clipboardData.getData('text').replace(/\D/g, '')
            if (pasted === '') return
            event.preventDefault()
            const all = pasted.length >= DIGITS
            fill(all ? 0 : index, pasted.slice(0, DIGITS))
          }}
          onKeyDown={(event) => {
            if (event.key === 'Backspace' && digit === '' && index > 0) {
              event.preventDefault()
              const next = [...digits]
              next[index - 1] = ''
              onChange(next)
              focusBox(index - 1)
            } else if (event.key === 'ArrowLeft') {
              event.preventDefault()
              focusBox(index - 1)
            } else if (event.key === 'ArrowRight') {
              event.preventDefault()
              focusBox(index + 1)
            }
          }}
        />
      ))}
    </div>
  )
}

/**
 * The step of a link that asks a one-time code: first one button that mails the code to the
 * recipient, then the six boxes. The access a right code opens goes up to the page, which keeps it
 * in memory only.
 */
function CodeGate({
  token,
  hint,
  notice,
  onUnlocked,
}: {
  token: string
  hint: string | null
  notice: string | null
  onUnlocked: (access: string) => void
}) {
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [digits, setDigits] = useState<string[]>(() => Array.from({ length: DIGITS }, () => ''))
  const [working, setWorking] = useState(false)
  const [failure, setFailure] = useState<string | null>(notice)
  const inputs = useRef<(HTMLInputElement | null)[]>([])
  const labelId = useId()

  useEffect(() => {
    if (sentTo !== null) inputs.current[0]?.focus()
  }, [sentTo])

  async function sendCode() {
    setWorking(true)
    setFailure(null)
    try {
      const { data, error, response } = await api.POST('/api/accountant-export/shared/code', { body: { token } })
      if (data === undefined) throw toProblem(error, response.status)
      setDigits(Array.from({ length: DIGITS }, () => ''))
      setSentTo(data.sent_to)
    } catch (error) {
      setFailure(toProblem(error).detail)
    } finally {
      setWorking(false)
    }
  }

  async function submit() {
    setWorking(true)
    setFailure(null)
    try {
      const { data, error, response } = await api.POST('/api/accountant-export/shared/verify', {
        body: { token, code: digits.join('') },
      })
      if (data === undefined) {
        const problem = toProblem(error, response.status)
        if (problem.code === 'export_code_invalid') {
          setDigits(Array.from({ length: DIGITS }, () => ''))
          inputs.current[0]?.focus()
        }
        throw problem
      }
      onUnlocked(data.access)
    } catch (error) {
      setFailure(toProblem(error).detail)
      setWorking(false)
    }
  }

  const alert =
    failure === null ? null : (
      <p role="alert" className="text-sm text-destructive">
        {failure}
      </p>
    )

  if (sentTo === null) {
    return (
      <div className="space-y-4">
        <p className="text-sm">
          Per aprire i file ti mandiamo un codice a {hint ?? 'l’indirizzo di chi riceve questo link'}.
        </p>
        <Button className="w-full" disabled={working} onClick={() => void sendCode()}>
          {working ? 'Invio del codice…' : 'Invia il codice'}
        </Button>
        {alert}
      </div>
    )
  }
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <p className="text-sm">Ti abbiamo scritto un codice a {sentTo}. Inseriscilo qui sotto.</p>
      <div className="space-y-2">
        <span id={labelId} className="block text-sm font-medium">
          Codice
        </span>
        <CodeBoxes digits={digits} onChange={setDigits} inputs={inputs} labelledBy={labelId} />
      </div>
      {alert}
      <div className="flex flex-col gap-2">
        <Button type="submit" className="w-full" disabled={working || digits.some((digit) => digit === '')}>
          {working ? 'Controllo…' : 'Apri i file'}
        </Button>
        <Button type="button" variant="ghost" className="w-full" disabled={working} onClick={() => void sendCode()}>
          Invia un altro codice
        </Button>
      </div>
    </form>
  )
}
