/**
 * What a new space still has to do, read from the data and never stored (spec
 * 2026-09-12 §6.7): a step is done because the thing exists. Six one-row reads behind
 * the «Get started» page (ORB-180), which is where the Home sends a person the first
 * time and where the sidebar takes them afterwards.
 *
 * The keys sit under the prefixes the rest of the app invalidates (`['customers', …]`,
 * `['emitter', …]`, `['tokens', userId, …]`), so creating the first customer, saving
 * the emitter or minting a token refreshes the page on its next visit without waiting
 * out `staleTime`.
 */
import { useQuery } from '@tanstack/react-query'
import { api, toProblem } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { queryKeys } from '@/lib/query'
import { tenantPrefix } from '@/lib/tenant'

export type StepId = 'fiscali' | 'cliente' | 'lavoro' | 'documento'
export type StepTarget = '/app/settings/issuer' | '/app/customers' | '/app/deal'

export interface FirstStep {
  id: StepId
  title: string
  hint: string
  to: StepTarget
  /** Whether the person who is looking can do this step themselves. */
  canDo: boolean
  done: boolean
}

export interface FirstStepsState {
  loading: boolean
  steps: FirstStep[]
  doneCount: number
  allDone: boolean
  /** Whether this user has a personal access token: their assistant is connected. */
  assistantConnected: boolean
  /** Whether any of the six reads errored. The panel's own `done` still treats an
   *  errored read as done (`value` below, for the same reason as ever); this is only
   *  for a caller, like the Home's redirect, that must not act as if every step had
   *  actually been read. */
  failed: boolean
}

/** Whether this browser has already been taken to «Get started» for this space and
 *  user: the Home does it once after the first login (ORB-180), and remembers here, per
 *  space (two spaces share the origin) and per user. */
export function seenKey(userId: string): string {
  return `pigrocrm.get-started.visto:${tenantPrefix || '/'}:${userId}`
}

export function hasSeenGetStarted(userId: string): boolean {
  try {
    return window.localStorage.getItem(seenKey(userId)) === '1'
  } catch {
    // A browser that refuses storage (a private window, blocked site data) is never
    // redirected: the sidebar entry is one click away, and a loop would be worse.
    return true
  }
}

export function markGetStartedSeen(userId: string): void {
  try {
    window.localStorage.setItem(seenKey(userId), '1')
  } catch {
    // Storage refused: `hasSeenGetStarted` answers `true` in the same browser, so
    // nothing is lost and nothing loops.
  }
}

/** Where the «Collega l'assistente» button goes. ORB-170 is shipping the «Collega un
 *  agente» dialog in the sidebar; until the Home can open that dialog, the token page
 *  is the place where the assistant is connected today. */
export const CONNECT_ASSISTANT_TO = '/app/token' as const

const SCOPE = { scope: 'first-steps', limit: 1 } as const

/** Throws for any answer that is not 2xx, except the one the caller names as an
 *  ordinary state (the emitter's 404). A step that could not be read counts as done
 *  (`value` below): a panel that nags because a request broke would be the untrue thing
 *  on the page, and the charts beside it already say when the API is down. */
function settled<T>(
  result: { data?: T; error?: unknown; response: Response },
  okStatuses: readonly number[] = [],
): T | undefined {
  if (result.error !== undefined && !okStatuses.includes(result.response.status)) {
    throw toProblem(result.error, result.response.status)
  }
  return result.data
}

async function hasCustomers(): Promise<boolean> {
  const page = settled(await api.GET('/api/customers', { params: { query: { limit: 1 } } }))
  return (page?.items.length ?? 0) > 0
}

async function hasDeals(): Promise<boolean> {
  const page = settled(await api.GET('/api/deals', { params: { query: { limit: 1 } } }))
  return (page?.items.length ?? 0) > 0
}

async function hasTimeEntries(): Promise<boolean> {
  const page = settled(await api.GET('/api/time-entries', { params: { query: { limit: 1 } } }))
  return (page?.items.length ?? 0) > 0
}

async function hasDocuments(): Promise<boolean> {
  const page = settled(await api.GET('/api/documents', { params: { query: { limit: 1 } } }))
  return (page?.items.length ?? 0) > 0
}

async function fiscalDataSaved(): Promise<boolean> {
  // 404 until the emitter profile is saved once: not an error here, a step to do.
  const profile = settled(await api.GET('/api/emitter'), [404])
  return Boolean(profile?.partita_iva || profile?.codice_fiscale)
}

async function hasToken(): Promise<boolean> {
  const tokens = settled(await api.GET('/api/tokens'))
  return (tokens?.length ?? 0) > 0
}

const OPTIONS = { retry: false, staleTime: 30_000 } as const

/** `enabled: false` issues no request and reports `loading: false`: for the Home's
 *  redirect once it has nothing left to decide. */
export function useFirstSteps({ enabled = true }: { enabled?: boolean } = {}): FirstStepsState {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  const isAdmin = user?.ruolo === 'admin'
  const steps = { ...OPTIONS, enabled }
  const token = useQuery({
    queryKey: [...queryKeys.tokens(userId), 'first-steps'],
    queryFn: hasToken,
    ...steps,
  })
  const fiscali = useQuery({ queryKey: [...queryKeys.emitter, 'first-steps'], queryFn: fiscalDataSaved, ...steps })
  const cliente = useQuery({ queryKey: queryKeys.customers(SCOPE), queryFn: hasCustomers, ...steps })
  const deal = useQuery({ queryKey: queryKeys.deals(SCOPE), queryFn: hasDeals, ...steps })
  const ore = useQuery({ queryKey: queryKeys.timeEntries(SCOPE), queryFn: hasTimeEntries, ...steps })
  const documento = useQuery({ queryKey: queryKeys.documents(SCOPE), queryFn: hasDocuments, ...steps })

  const active = [token, fiscali, cliente, deal, ore, documento]
  const loading = enabled && active.some((q) => q.isPending)
  const failed = enabled && active.some((q) => q.isError)
  const value = (q: { data?: boolean; isError: boolean }) => (q.isError ? true : (q.data ?? false))

  const list: FirstStep[] = [
    {
      id: 'fiscali',
      title: 'I tuoi dati fiscali',
      hint: isAdmin
        ? 'Partita IVA o codice fiscale, indirizzo, regime: finiscono su offerte e fatture.'
        : 'Li imposta l’amministratore dello spazio, in Impostazioni.',
      to: '/app/settings/issuer',
      canDo: isAdmin,
      done: value(fiscali),
    },
    {
      id: 'cliente',
      title: 'Il primo cliente',
      hint: 'Un’azienda o una persona per cui lavori. Tutto il resto parte da qui.',
      to: '/app/customers',
      canDo: true,
      done: value(cliente),
    },
    {
      id: 'lavoro',
      title: 'Il primo deal, o le prime ore',
      hint: 'Una trattativa in corso, oppure le ore che hai già lavorato.',
      to: '/app/deal',
      canDo: true,
      done: value(deal) || value(ore),
    },
    {
      id: 'documento',
      title: 'La prima offerta',
      hint: 'Dal deal, «Crea documento»: il template «Offerta» è già pronto.',
      to: '/app/deal',
      canDo: true,
      done: value(documento),
    },
  ]
  const doneCount = list.filter((s) => s.done).length
  return {
    loading,
    steps: list,
    doneCount,
    allDone: doneCount === list.length,
    assistantConnected: value(token),
    failed,
  }
}
