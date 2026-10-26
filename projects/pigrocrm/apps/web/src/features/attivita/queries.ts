import { useCallback, useMemo } from 'react'
import { useCustomer } from '@/features/customers/queries'
import { useDeal } from '@/features/deals/queries'
import { useInvoice } from '@/features/invoices/queries'
import { usePerson } from '@/features/people/queries'
import { useUsers } from '@/features/settings/queries'
import { useAuth, useIsAdmin } from '@/lib/auth'
import type { LinkKind, LinkValue } from './links'

/**
 * The name of the record an attività hangs from, read by id.
 *
 * Four reads behind one hook, three of them switched off: a hook cannot be called
 * conditionally, so each is `enabled` only for its own kind and an empty id never reaches
 * the network. The row already holds the id and nothing else, and a link drawn as a bare
 * UUID would be a link nobody could read.
 */
export function useRecordLabel(link: LinkValue | null): string | undefined {
  const is = (kind: LinkKind) => link?.kind === kind
  const id = link?.id ?? ''
  const customer = useCustomer(id, { enabled: is('customer') })
  const person = usePerson(id, { enabled: is('person') })
  const deal = useDeal(id, { enabled: is('deal') })
  const invoice = useInvoice(is('invoice') ? id : '')

  if (link === null) return undefined
  switch (link.kind) {
    case 'customer':
      return customer.data?.ragione_sociale
    case 'person':
      return person.data ? [person.data.nome, person.data.cognome].filter(Boolean).join(' ') : undefined
    case 'deal':
      return deal.data?.nome
    case 'invoice': {
      const data = invoice.data
      if (!data) return undefined
      // The number is assigned at emission: until then the state stands in for it, the
      // same rule the global search's label follows.
      const prefix = data.anno !== null && data.numero !== null ? `${data.anno}/${data.numero}` : data.stato
      return data.causale ? `${prefix} · ${data.causale}` : prefix
    }
  }
}

export interface Assignee {
  id: string
  nome: string
}

/**
 * The people an attività can be given to, and how to name whoever it already is.
 *
 * `GET /api/users` is the administrators' (`UserService.list`), so for anyone else the
 * list is the person themselves and a colleague is named «Un collega»: the API accepts
 * any active user as an assignee, but the web has no directory of the space to offer a
 * collaborator and does not invent one here.
 */
export function useAssignees() {
  const { user } = useAuth()
  const admin = useIsAdmin()
  const users = useUsers({ enabled: admin })

  const options = useMemo<Assignee[]>(() => {
    if (admin) {
      return (users.data ?? []).filter((item) => item.attivo).map(({ id, nome }) => ({ id, nome }))
    }
    return user ? [{ id: user.id, nome: user.nome }] : []
  }, [admin, users.data, user])

  const nameOf = useCallback(
    (id: string | null): string | null => {
      if (id === null) return null
      const known = (admin ? users.data : undefined)?.find((item) => item.id === id)
      if (known) return known.nome
      return user?.id === id ? user.nome : 'Un collega'
    },
    [admin, users.data, user],
  )

  return { options, nameOf, me: user?.id ?? null }
}
