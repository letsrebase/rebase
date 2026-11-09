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
import { useState } from 'react'
import { api, toProblem } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useModules } from '@/lib/modules'
import { queryKeys } from '@/lib/query'

export type StepId = 'onboarding' | 'tax' | 'customer' | 'work' | 'document'
export type StepTarget =
  | '/app/onboarding'
  | '/app/settings/legal-entities'
  | '/app/customers'
  | '/app/deal'

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

/**
 * The five «is there anything» reads keep the **page** the API sent and answer the boolean
 * in `select`, instead of caching a boolean. Their keys sit under the prefixes the rest of
 * the app invalidates (`['deals', …]`, `['customers', …]`), and that is also the prefix
 * the Kanban's optimistic move walks with `getQueriesData` expecting `{ items }`: a boolean
 * under `['deals', { scope: 'first-steps' }]` made `useMoveDeal` throw on `data.items.map`
 * and drop every card move, once the shell kept the read mounted on every page (REB-873).
 * The cache entry has the shape of a list page, as any other entry under the prefix does.
 */
interface Page {
  items?: unknown[]
}

const NO_ITEMS = { items: [], next_cursor: null }

const hasItems = (page: Page) => (page.items?.length ?? 0) > 0

async function customersPage() {
  return settled(await api.GET('/api/customers', { params: { query: { limit: 1 } } })) ?? NO_ITEMS
}

async function dealsPage() {
  return settled(await api.GET('/api/deals', { params: { query: { limit: 1 } } })) ?? NO_ITEMS
}

async function timeEntriesPage() {
  return settled(await api.GET('/api/time-entries', { params: { query: { limit: 1 } } })) ?? NO_ITEMS
}

async function documentsPage() {
  return settled(await api.GET('/api/documents', { params: { query: { limit: 1 } } })) ?? NO_ITEMS
}

/** «La prima offerta» is an offer, not any document: since the invoice door (REB-224)
 *  files an issued invoice's PDF as a `invoice` document, «any document» would tick the
 *  step for a file that is not an offer. Any document still counts as work
 *  (`documentsPage`, `spaceEmpty`). */
async function offersPage() {
  return (
    settled(await api.GET('/api/documents', { params: { query: { type: 'offer', limit: 1 } } })) ??
    NO_ITEMS
  )
}

async function fiscalDataSaved(): Promise<boolean> {
  // The default azienda is the first of the list (REB-617); a space has one from its
  // first boot, with the name and nothing fiscal, so the step is done once either id
  // is on it.
  const legal_entities = settled(await api.GET('/api/legal-entities'))
  const profile = legal_entities?.[0]
  return Boolean(profile?.vat_number || profile?.tax_code)
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
/** The sidebar asks the same four reads for one boolean it never needs fresher than the
 *  data it already has: no refetch on a window focus, and no stale-time expiry. A write
 *  that invalidates the keys still refreshes them while they are watched. */
const PASSIVE_WORK_OPTIONS = { staleTime: Infinity, refetchOnWindowFocus: false } as const

/** What a step's read says: a read that failed counts as done (see `settled`). */
const value = (q: { data?: boolean; isError: boolean }) => (q.isError ? true : (q.data ?? false))

/** The four reads that say whether the space has any work, and the verdict they give. */
function useWork(options: { staleTime: number; refetchOnWindowFocus?: boolean; enabled?: boolean } = WORK_OPTIONS) {
  // A module the space has switched off is not read (REB-953): its routes answer 404, which
  // would count as «work» (a failed read does, see `value`) and keep an empty space from
  // ever being the start page. Nor is anything read until the modules are known, for the
  // same reason: a read that is about to be refused is no answer.
  const modules = useModules()
  const ready = (options.enabled ?? true) && !modules.loading
  const dealsOn = modules.on('deals')
  const timeOn = modules.on('time')
  const customer = useQuery({ queryKey: queryKeys.customers(SCOPE), queryFn: customersPage, select: hasItems, ...options, enabled: ready })
  const deal = useQuery({ queryKey: queryKeys.deals(SCOPE), queryFn: dealsPage, select: hasItems, ...options, enabled: ready && dealsOn })
  const hours = useQuery({ queryKey: queryKeys.timeEntries(SCOPE), queryFn: timeEntriesPage, select: hasItems, ...options, enabled: ready && timeOn })
  const document = useQuery({ queryKey: queryKeys.documents(SCOPE), queryFn: documentsPage, select: hasItems, ...options, enabled: ready })
  const reads = [customer, document, ...(dealsOn ? [deal] : []), ...(timeOn ? [hours] : [])]
  return {
    customer,
    deal,
    hours,
    dealsOn,
    reads,
    // Not while a work read is refetching either: a Home mounted right after the first
    // customer was created would otherwise decide on the cached «empty» that the
    // invalidation is about to replace, and then hold that stale answer for the visit.
    // And not while the modules are unknown: the reads are held back until they are, and a
    // held-back read that has a cached answer reports neither pending nor fetching.
    spaceEmpty:
      modules.loading || reads.some((q) => q.isPending || q.isFetching) ? null : !reads.some(value),
    workFailed: reads.some((q) => q.isError),
  }
}

/**
 * Whether the space has any work yet, `null` while that is not known: the question the Home
 * asks to choose between the start page and the dashboard. The same four reads under the
 * same keys as `useSpaceHasData`, so the two share one answer.
 */
export function useSpaceEmpty(): { spaceEmpty: boolean | null; workFailed: boolean } {
  const { spaceEmpty, workFailed } = useWork()
  return { spaceEmpty, workFailed }
}

/**
 * Whether the space has data, for the sidebar's «Primi passi» entry (REB-873): the same
 * predicate as the Home's, so the two cannot disagree about when a space stops being new.
 * `false` while it is unknown or for a space that answered «empty».
 *
 * Held once it is known, as the Home holds its own choice: `spaceEmpty` goes back to
 * `null` while any of the four reads refetches (a write), and the entry must not blink out
 * of the menu for that. A space does not lose its work -- so once it is known the reads
 * are switched off here, and the sidebar costs nothing more for the rest of the session.
 * Until then it watches them passively (`PASSIVE_WORK_OPTIONS`), sharing the Home's cache.
 */
export function useSpaceHasData(): boolean {
  const [known, setKnown] = useState(false)
  const { spaceEmpty } = useWork({ ...PASSIVE_WORK_OPTIONS, enabled: !known })
  if (spaceEmpty === false && !known) setKnown(true)
  return known
}

export function useFirstSteps(): FirstStepsState {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  const isAdmin = user?.role === 'admin'
  const modules = useModules()
  const token = useQuery({
    queryKey: [...queryKeys.tokens(userId), 'first-steps'],
    queryFn: hasToken,
    ...OPTIONS,
  })
  const tax = useQuery({ queryKey: [...queryKeys.legal_entities, 'first-steps'], queryFn: fiscalDataSaved, ...OPTIONS })
  const { customer, deal, hours, dealsOn, reads, spaceEmpty, workFailed } = useWork()
  const offer = useQuery({
    queryKey: queryKeys.documents({ ...SCOPE, type: 'offer' }),
    queryFn: offersPage,
    select: hasItems,
    ...OPTIONS,
  })

  const loading = [token, tax, offer, ...reads].some((q) => q.isPending)

  const everyStep: FirstStep[] = [
    {
      id: 'tax',
      title: 'I tuoi dati fiscali',
      hint: isAdmin
        ? 'Partita IVA o codice fiscale, indirizzo, regime: finiscono su offerte e fatture.'
        : 'Li imposta l’amministratore dello spazio, in Impostazioni.',
      to: '/app/settings/legal-entities',
      screen: 'Impostazioni → Aziende',
      canDo: isAdmin,
      done: value(tax),
    },
    {
      id: 'customer',
      title: 'Il primo cliente',
      hint: 'Un’azienda o una persona per cui lavori. Tutto il resto parte da qui.',
      to: '/app/customers',
      screen: 'Clienti',
      canDo: true,
      done: value(customer),
    },
    {
      id: 'work',
      title: 'Il primo deal, o le prime ore',
      hint: 'Una trattativa in corso, oppure le ore che hai già lavorato.',
      to: '/app/deal',
      screen: 'Deal',
      canDo: true,
      done: value(deal) || value(hours),
    },
    {
      id: 'document',
      title: 'La prima offerta',
      hint: 'Dal deal, «Crea documento»: il template «Offerta» è già pronto.',
      to: '/app/deal',
      screen: 'Deal',
      canDo: true,
      done: value(offer),
    },
  ]
  // «Il primo deal, o le prime ore» and «La prima offerta» send the person to the Deal
  // pages: with `deals` off (REB-953) there is no such page to send them to.
  const dealSteps = dealsOn ? everyStep : everyStep.filter((s) => s.id !== 'work' && s.id !== 'document')
  // An administrator who answered «Decido dopo» to the onboarding (spec 2026-11-09, B3,
  // REB-957) finds it here as the first item until the space has a profile; it is not
  // stored anywhere but in the space's own settings, like the rest of this list is read.
  const list: FirstStep[] =
    modules.onboarding === 'skipped'
      ? [
          {
            id: 'onboarding',
            title: 'Come lavora il tuo spazio',
            hint: 'Da solo o come azienda: lo spazio mostra solo i moduli che ti servono.',
            to: '/app/onboarding',
            screen: 'Le tre domande',
            canDo: isAdmin,
            done: false,
          },
          ...dealSteps,
        ]
      : dealSteps
  const doneCount = list.filter((s) => s.done).length
  return {
    loading,
    steps: list,
    doneCount,
    allDone: doneCount === list.length,
    assistantConnected: value(token),
    spaceEmpty,
    workFailed,
  }
}
