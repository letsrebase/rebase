/**
 * The azienda the sidebar has selected (REB-625, spec 2026-10-03 §1.10, §5).
 *
 * One space may run several aziende, and from the second one on the sidebar offers a
 * choice: «Tutte le aziende», or one of them. The choice is a session preference, like
 * the collapsed groups of the sidebar and unlike a filter a link should fix, so it lives
 * here and in this browser (per space and per user, the key shape
 * `features/get-started/invoiceHandoff.ts` uses) and never in the URL.
 *
 * What reads it: the list hooks, through `useLegalEntityScope`, and the two lists that name
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

export type LegalEntityRecord = components['schemas']['LegalEntityRead']

export interface LegalEntityValue {
  /** The active aziende of the space, the default first; empty until the list has loaded. */
  aziende: LegalEntityRecord[]
  /** The selected azienda's id, or `null` for «Tutte le aziende». */
  selected: string | null
  select: (id: string | null) => void
  /** True from the second azienda on: when the selector, the column and the name are drawn. */
  several: boolean
  /** True when the session is scoped to some aziende (`me.aziende` is a list, REB-635):
   *  the list above is then theirs alone, which the server already answers. */
  scoped: boolean
  /** True for a scoped person with one azienda: the selection is that azienda, drawn as
   *  a label rather than a control, since there is nothing to choose between. */
  pinned: boolean
  /** The active azienda an id names, or `undefined` for `null` and for an id no longer there. */
  byId: (id: string | null | undefined) => LegalEntityRecord | undefined
}

const ONE_AZIENDA: LegalEntityValue = {
  aziende: [],
  selected: null,
  select: () => {},
  several: false,
  scoped: false,
  pinned: false,
  byId: () => undefined,
}

export const LegalEntityContext = createContext<LegalEntityValue>(ONE_AZIENDA)

export function legalEntityKey(userId: string): string {
  return `pigrocrm.azienda:${tenantPrefix || '/'}:${userId}`
}

/** Every access is guarded: a browser that refuses storage simply forgets the choice. */
export function readSelectedLegalEntity(userId: string): string | null {
  if (!userId) return null
  try {
    return window.localStorage.getItem(legalEntityKey(userId))
  } catch {
    return null
  }
}

export function writeSelectedLegalEntity(userId: string, id: string | null): void {
  if (!userId) return
  try {
    if (id === null) window.localStorage.removeItem(legalEntityKey(userId))
    else window.localStorage.setItem(legalEntityKey(userId), id)
  } catch {
    // Storage refused: the choice holds until the page is left.
  }
}

export function LegalEntityProvider({ children }: { children: ReactNode }) {
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
    id: readSelectedLegalEntity(userId),
  }))
  const chosen = choice.userId === userId ? choice.id : readSelectedLegalEntity(userId)

  const several = aziende.length > 1
  // A scoped session (spec 2026-10-03 §1.11, §5): `me.aziende` is the list the person
  // may see, and `GET /api/aziende` already answers those alone, so nothing is filtered
  // here. With one of them there is nothing to choose: the selection is pinned to it,
  // so the dashboards and the estimate ask for that azienda by name, and the sidebar
  // draws its name instead of a selector. With several, the selector offers theirs.
  const scoped = Array.isArray(user?.aziende)
  const pinned = scoped && aziende.length === 1
  // A stored id that names no active azienda any more (deactivated since), or a space
  // back to one azienda: «tutte», which is also what the lists send when nothing is chosen.
  const selected = pinned
    ? (aziende[0]?.id ?? null)
    : several && chosen !== null && aziende.some((a) => a.id === chosen)
      ? chosen
      : null
  const select = useCallback(
    (id: string | null) => {
      setChoice({ userId, id })
      writeSelectedLegalEntity(userId, id)
    },
    [userId],
  )
  const byId = useCallback(
    (id: string | null | undefined) => (id ? aziende.find((a) => a.id === id) : undefined),
    [aziende],
  )
  const value = useMemo<LegalEntityValue>(
    () => ({ aziende, selected, select, several, scoped, pinned, byId }),
    [aziende, selected, select, several, scoped, pinned, byId],
  )
  return <LegalEntityContext value={value}>{children}</LegalEntityContext>
}

export function useLegalEntity(): LegalEntityValue {
  return use(LegalEntityContext)
}

/**
 * The parameters a list hook sends, with the selection as `azienda_id` when there is one
 * and the list has no owner of its own. The result is also the query key's argument, so a
 * switch of the sidebar is a new key and a fresh request, with no invalidation to forget.
 */
export function useLegalEntityScope<P extends object>(params: P): P & { azienda_id?: string } {
  const { selected } = useLegalEntity()
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
export function useLegalEntitiesToName(): LegalEntityRecord[] | undefined {
  const { aziende, selected, several } = useLegalEntity()
  return several && selected === null ? aziende : undefined
}

/**
 * The azienda `nazione` proposes for a new customer (REB-626, spec 2026-10-03 §1.6), read
 * from the server so the form shows what `POST /api/customers` would pick: the rule lives
 * in `LegalEntityService.propose` once. `enabled` is the caller's: the customer form asks only
 * from the second azienda on, while creating, and until the person picks one by hand. A
 * nation is two letters, so a half-typed one asks nothing rather than flipping the picker
 * to the default between the first letter and the second.
 */
export function useLegalEntityProposal(nazione: string, enabled: boolean) {
  const paese = nazione.trim().toUpperCase() || 'IT'
  return useQuery({
    queryKey: queryKeys.aziendaProposta(paese),
    queryFn: () =>
      unwrap(api.GET('/api/aziende/proposta', { params: { query: { nazione: paese } } })),
    enabled: enabled && paese.length === 2,
  })
}
