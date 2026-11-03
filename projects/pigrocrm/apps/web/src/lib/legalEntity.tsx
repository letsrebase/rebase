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
  legal_entities: LegalEntityRecord[]
  /** The selected azienda's id, or `null` for «Tutte le aziende». */
  selected: string | null
  select: (id: string | null) => void
  /** True from the second azienda on: when the selector, the column and the name are drawn. */
  several: boolean
  /** True when the session is scoped to some aziende (`me.legal_entities` is a list, REB-635):
   *  the list above is then theirs alone, which the server already answers. */
  scoped: boolean
  /** True for a scoped person with one azienda: the selection is that azienda, drawn as
   *  a label rather than a control, since there is nothing to choose between. */
  pinned: boolean
  /** The active azienda an id names, or `undefined` for `null` and for an id no longer there. */
  byId: (id: string | null | undefined) => LegalEntityRecord | undefined
}

const OneLegalEntity: LegalEntityValue = {
  legal_entities: [],
  selected: null,
  select: () => {},
  several: false,
  scoped: false,
  pinned: false,
  byId: () => undefined,
}

export const LegalEntityContext = createContext<LegalEntityValue>(OneLegalEntity)

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
    queryKey: queryKeys.legal_entities,
    queryFn: () => unwrap(api.GET('/api/legal-entities')),
    enabled: userId !== '',
  })
  const legal_entities = useMemo(() => (list.data ?? []).filter((a) => a.active), [list.data])
  // The choice, remembered with whose it is: another person signing in on this browser
  // gets their own stored choice, not the previous one's, and the switch happens in
  // render rather than in an effect that would paint one frame with the wrong azienda.
  const [choice, setChoice] = useState<{ userId: string; id: string | null }>(() => ({
    userId,
    id: readSelectedLegalEntity(userId),
  }))
  const chosen = choice.userId === userId ? choice.id : readSelectedLegalEntity(userId)

  const several = legal_entities.length > 1
  // A scoped session (spec 2026-10-03 §1.11, §5): `me.legal_entities` is the list the person
  // may see, and `GET /api/aziende` already answers those alone, so nothing is filtered
  // here. With one of them there is nothing to choose: the selection is pinned to it,
  // so the dashboards and the estimate ask for that azienda by name, and the sidebar
  // draws its name instead of a selector. With several, the selector offers theirs.
  const scoped = Array.isArray(user?.legal_entities)
  const pinned = scoped && legal_entities.length === 1
  // A stored id that names no active azienda any more (deactivated since), or a space
  // back to one azienda: «tutte», which is also what the lists send when nothing is chosen.
  const selected = pinned
    ? (legal_entities[0]?.id ?? null)
    : several && chosen !== null && legal_entities.some((a) => a.id === chosen)
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
    (id: string | null | undefined) => (id ? legal_entities.find((a) => a.id === id) : undefined),
    [legal_entities],
  )
  const value = useMemo<LegalEntityValue>(
    () => ({ legal_entities, selected, select, several, scoped, pinned, byId }),
    [legal_entities, selected, select, several, scoped, pinned, byId],
  )
  return <LegalEntityContext value={value}>{children}</LegalEntityContext>
}

export function useLegalEntity(): LegalEntityValue {
  return use(LegalEntityContext)
}

/**
 * The parameters a list hook sends, with the selection as `legal_entity_id` when there is one
 * and the list has no owner of its own. The result is also the query key's argument, so a
 * switch of the sidebar is a new key and a fresh request, with no invalidation to forget.
 */
export function useLegalEntityScope<P extends object>(params: P): P & { legal_entity_id?: string } {
  const { selected } = useLegalEntity()
  const owner = params as { customer_id?: string; deal_id?: string }
  if (selected === null || owner.customer_id !== undefined || owner.deal_id !== undefined) {
    return params
  }
  return { ...params, legal_entity_id: selected }
}

/**
 * The aziende to name on the rows of a list, or `undefined` when a row needs no name:
 * with one azienda, and with one selected, every row is that azienda's and a name on
 * each line would only repeat the sidebar.
 */
export function useLegalEntitiesToName(): LegalEntityRecord[] | undefined {
  const { legal_entities, selected, several } = useLegalEntity()
  return several && selected === null ? legal_entities : undefined
}

/**
 * The azienda `country` proposes for a new customer (REB-626, spec 2026-10-03 §1.6), read
 * from the server so the form shows what `POST /api/customers` would pick: the rule lives
 * in `LegalEntityService.propose` once. `enabled` is the caller's: the customer form asks only
 * from the second azienda on, while creating, and until the person picks one by hand. A
 * nation is two letters, so a half-typed one asks nothing rather than flipping the picker
 * to the default between the first letter and the second.
 */
export function useLegalEntityProposal(country: string, enabled: boolean) {
  const paese = country.trim().toUpperCase() || 'IT'
  return useQuery({
    queryKey: queryKeys.legalEntityProposal(paese),
    queryFn: () =>
      unwrap(api.GET('/api/legal-entities/suggestion', { params: { query: { country: paese } } })),
    enabled: enabled && paese.length === 2,
  })
}
