/**
 * The azienda the sidebar has selected (REB-625, spec 2026-10-03 §1.10, §5).
 *
 * One space may run several aziende, and from the second one on the sidebar offers a
 * choice: «Tutte le aziende», or one of them. The choice is a session preference, like
 * the collapsed groups of the sidebar and unlike a filter a link should fix, so it lives
 * here and in this browser (per space and per user, the key shape
 * `features/get-started/invoiceHandoff.ts` uses) and never in the URL.
 *
 * What reads it: the list hooks, through `useAziendaScope`, and the two lists that name
 * the azienda on a row in «tutte» (the invoice number, the customer column). What never
 * reads it: a list that already has an owner. A customer's or a deal's tab shows that
 * owner's rows whatever the sidebar says, otherwise opening a deal of azienda A with B
 * selected would show an empty tab and read as a loss of data.
 *
 * With one azienda, which is every space until milestone 5 opens creation, `several` is
 * false, the selection is `null`, and every request is the one the space sent before
 * this file existed. The default value of the context is that same state, so a page or a
 * test mounted without the provider behaves as a one-azienda space.
 */
import { useQuery } from '@tanstack/react-query'
import { createContext, use, useCallback, useMemo, useState, type ReactNode } from 'react'
import { api, unwrap } from './api'
import type { components } from './api-types'
import { useAuth } from './auth'
import { queryKeys } from './query'
import { tenantPrefix } from './tenant'

export type AziendaRecord = components['schemas']['AziendaRead']

export interface AziendaValue {
  /** The active aziende of the space, the default first; empty until the list has loaded. */
  aziende: AziendaRecord[]
  /** The selected azienda's id, or `null` for «Tutte le aziende». */
  selected: string | null
  select: (id: string | null) => void
  /** True from the second azienda on: when the selector, the column and the name are drawn. */
  several: boolean
  /** The active azienda an id names, or `undefined` for `null` and for an id no longer there. */
  byId: (id: string | null | undefined) => AziendaRecord | undefined
}

const ONE_AZIENDA: AziendaValue = {
  aziende: [],
  selected: null,
  select: () => {},
  several: false,
  byId: () => undefined,
}

export const AziendaContext = createContext<AziendaValue>(ONE_AZIENDA)

export function aziendaKey(userId: string): string {
  return `pigrocrm.azienda:${tenantPrefix || '/'}:${userId}`
}

/** Every access is guarded: a browser that refuses storage simply forgets the choice. */
export function readSelectedAzienda(userId: string): string | null {
  if (!userId) return null
  try {
    return window.localStorage.getItem(aziendaKey(userId))
  } catch {
    return null
  }
}

export function writeSelectedAzienda(userId: string, id: string | null): void {
  if (!userId) return
  try {
    if (id === null) window.localStorage.removeItem(aziendaKey(userId))
    else window.localStorage.setItem(aziendaKey(userId), id)
  } catch {
    // Storage refused: the choice holds until the page is left.
  }
}

export function AziendaProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const userId = user?.id ?? ''
  // The same key `Impostazioni → Aziende` reads and its save invalidates, so a renamed
  // azienda reaches the selector without a reload. Disabled until there is a session:
  // the login page must not ask for a list it would only get a 401 for.
  const list = useQuery({
    queryKey: queryKeys.aziende,
    queryFn: () => unwrap(api.GET('/api/aziende')),
    enabled: userId !== '',
  })
  const aziende = useMemo(() => (list.data ?? []).filter((a) => a.attiva), [list.data])
  // The choice, remembered with whose it is: another person signing in on this browser
  // gets their own stored choice, not the previous one's, and the switch happens in
  // render rather than in an effect that would paint one frame with the wrong azienda.
  const [choice, setChoice] = useState<{ userId: string; id: string | null }>(() => ({
    userId,
    id: readSelectedAzienda(userId),
  }))
  const chosen = choice.userId === userId ? choice.id : readSelectedAzienda(userId)

  const several = aziende.length > 1
  // A stored id that names no active azienda any more (deactivated since), or a space
  // back to one azienda: «tutte», which is also what the lists send when nothing is chosen.
  const selected =
    several && chosen !== null && aziende.some((a) => a.id === chosen) ? chosen : null
  const select = useCallback(
    (id: string | null) => {
      setChoice({ userId, id })
      writeSelectedAzienda(userId, id)
    },
    [userId],
  )
  const byId = useCallback(
    (id: string | null | undefined) => (id ? aziende.find((a) => a.id === id) : undefined),
    [aziende],
  )
  const value = useMemo<AziendaValue>(
    () => ({ aziende, selected, select, several, byId }),
    [aziende, selected, select, several, byId],
  )
  return <AziendaContext value={value}>{children}</AziendaContext>
}

export function useAzienda(): AziendaValue {
  return use(AziendaContext)
}

/**
 * The parameters a list hook sends, with the selection as `azienda_id` when there is one
 * and the list has no owner of its own. The result is also the query key's argument, so a
 * switch of the sidebar is a new key and a fresh request, with no invalidation to forget.
 */
export function useAziendaScope<P extends object>(params: P): P & { azienda_id?: string } {
  const { selected } = useAzienda()
  const owner = params as { customer_id?: string; deal_id?: string }
  if (selected === null || owner.customer_id !== undefined || owner.deal_id !== undefined) {
    return params
  }
  return { ...params, azienda_id: selected }
}

/**
 * The aziende to name on the rows of a list, or `undefined` when a row needs no name:
 * with one azienda, and with one selected, every row is that azienda's and a name on
 * each line would only repeat the sidebar.
 */
export function useAziendeToName(): AziendaRecord[] | undefined {
  const { aziende, selected, several } = useAzienda()
  return several && selected === null ? aziende : undefined
}

/**
 * The azienda `nazione` proposes for a new customer (REB-626, spec 2026-10-03 §1.6), read
 * from the server so the form shows what `POST /api/customers` would pick: the rule lives
 * in `AziendaService.propose` once. `enabled` is the caller's: the customer form asks only
 * from the second azienda on, while creating, and until the person picks one by hand. A
 * nation is two letters, so a half-typed one asks nothing rather than flipping the picker
 * to the default between the first letter and the second.
 */
export function useAziendaProposta(nazione: string, enabled: boolean) {
  const paese = nazione.trim().toUpperCase() || 'IT'
  return useQuery({
    queryKey: queryKeys.aziendaProposta(paese),
    queryFn: () =>
      unwrap(api.GET('/api/aziende/proposta', { params: { query: { nazione: paese } } })),
    enabled: enabled && paese.length === 2,
  })
}
