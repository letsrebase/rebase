/**
 * What a new space still has to do, read from the data and never stored (spec
 * 2026-09-12 §6.7): a step is done because the thing exists. Seven one-row reads behind
 * the «Primi passi» page (ORB-180) and behind the Home, which is that same page while
 * the space is empty and the dashboard afterwards (spec 2026-09-16 §4.1, REB-222).
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

export type StepId = 'fiscali' | 'cliente' | 'lavoro' | 'documento'
export type StepTarget = '/app/settings/issuer' | '/app/customers' | '/app/deal'

export interface FirstStep {
  id: StepId
  title: string
  hint: string
  to: StepTarget
  /** The screen `to` opens, as the menu names it: the label of the step's «a mano» link. */
  screen: string
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
  /** No customer, deal, time entry or document (spec 2026-09-16 §4.1): the Home is the
   *  start page instead of the dashboard. The fiscal data and the token are steps, not
   *  work, so they do not count, and they are not waited for either: `null` only while
   *  one of the four work reads is pending or refetching, so the Home chooses its page on
   *  an answer that is current, without waiting for the other two. A read that failed counts as a thing that exists, for the
   *  reason `settled` gives: the dashboard is the safe page to fall back to. The API's
   *  `space_is_empty` (`pigrocrm.core.first_steps`) answers the same question over the
   *  same four tables, for the Gmail consent flow's way back. */
  spaceEmpty: boolean | null
  /** Whether one of those four reads failed, retries spent: `spaceEmpty` is then a
   *  guess (a failure counts as work), which the Home shows but does not hold. */
  workFailed: boolean
}

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

/** «La prima offerta» is an offer, not any document: since the invoice door (REB-224)
 *  files an issued invoice's PDF as a `fattura` document, «any document» would tick the
 *  step for a file that is not an offer. Any document still counts as work
 *  (`hasDocuments`, `spaceEmpty`). */
async function hasOffers(): Promise<boolean> {
  const page = settled(
    await api.GET('/api/documents', { params: { query: { tipo: 'offerta', limit: 1 } } }),
  )
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
/** The four reads that decide which page the Home is. They keep the app's own retry
 *  policy (`lib/query.ts`: two retries, none on 401/403) instead of `retry: false`: one
 *  dropped request must not send an empty space's Home to the dashboard. */
const WORK_OPTIONS = { staleTime: 30_000 } as const

export function useFirstSteps(): FirstStepsState {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  const isAdmin = user?.ruolo === 'admin'
  const token = useQuery({
    queryKey: [...queryKeys.tokens(userId), 'first-steps'],
    queryFn: hasToken,
    ...OPTIONS,
  })
  const fiscali = useQuery({ queryKey: [...queryKeys.emitter, 'first-steps'], queryFn: fiscalDataSaved, ...OPTIONS })
  const cliente = useQuery({ queryKey: queryKeys.customers(SCOPE), queryFn: hasCustomers, ...WORK_OPTIONS })
  const deal = useQuery({ queryKey: queryKeys.deals(SCOPE), queryFn: hasDeals, ...WORK_OPTIONS })
  const ore = useQuery({ queryKey: queryKeys.timeEntries(SCOPE), queryFn: hasTimeEntries, ...WORK_OPTIONS })
  const documento = useQuery({ queryKey: queryKeys.documents(SCOPE), queryFn: hasDocuments, ...WORK_OPTIONS })
  const offerta = useQuery({
    queryKey: queryKeys.documents({ ...SCOPE, tipo: 'offerta' }),
    queryFn: hasOffers,
    ...OPTIONS,
  })

  const work = [cliente, deal, ore, documento]
  const loading = [token, fiscali, offerta, ...work].some((q) => q.isPending)
  const value = (q: { data?: boolean; isError: boolean }) => (q.isError ? true : (q.data ?? false))

  const list: FirstStep[] = [
    {
      id: 'fiscali',
      title: 'I tuoi dati fiscali',
      hint: isAdmin
        ? 'Partita IVA o codice fiscale, indirizzo, regime: finiscono su offerte e fatture.'
        : 'Li imposta l’amministratore dello spazio, in Impostazioni.',
      to: '/app/settings/issuer',
      screen: 'Impostazioni → Emittente',
      canDo: isAdmin,
      done: value(fiscali),
    },
    {
      id: 'cliente',
      title: 'Il primo cliente',
      hint: 'Un’azienda o una persona per cui lavori. Tutto il resto parte da qui.',
      to: '/app/customers',
      screen: 'Clienti',
      canDo: true,
      done: value(cliente),
    },
    {
      id: 'lavoro',
      title: 'Il primo deal, o le prime ore',
      hint: 'Una trattativa in corso, oppure le ore che hai già lavorato.',
      to: '/app/deal',
      screen: 'Deal',
      canDo: true,
      done: value(deal) || value(ore),
    },
    {
      id: 'documento',
      title: 'La prima offerta',
      hint: 'Dal deal, «Crea documento»: il template «Offerta» è già pronto.',
      to: '/app/deal',
      screen: 'Deal',
      canDo: true,
      done: value(offerta),
    },
  ]
  const doneCount = list.filter((s) => s.done).length
  return {
    loading,
    steps: list,
    doneCount,
    allDone: doneCount === list.length,
    assistantConnected: value(token),
    // Not while a work read is refetching either: a Home mounted right after the first
    // customer was created would otherwise decide on the cached «empty» that the
    // invalidation is about to replace, and then hold that stale answer for the visit.
    spaceEmpty: work.some((q) => q.isPending || q.isFetching) ? null : !work.some(value),
    workFailed: work.some((q) => q.isError),
  }
}
