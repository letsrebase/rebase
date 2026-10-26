import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { api, unwrap } from '@/lib/api'
import { useAuth, useCan } from '@/lib/auth'
import { queryKeys } from '@/lib/query'
import type { OwnerOption } from './CustomerForm'

/**
 * Who a customer's owner is, in words (REB-773).
 *
 * The owner is an id on the customer. Only an admin may list the space's users
 * (`GET /api/users`), so only an admin gets names for everybody and the people to hand a
 * customer to; anybody else gets their own name when the customer is theirs and «un
 * collega» otherwise. The query shares `queryKeys.users` with the Utenti panel, so an
 * admin who has been there pays no second request.
 */
export function useCustomerOwners() {
  const { user } = useAuth()
  const canChange = useCan('change_customer_owner')
  const users = useQuery({
    queryKey: queryKeys.users,
    queryFn: () => unwrap(api.GET('/api/users')),
    enabled: canChange,
  })

  const options = useMemo<OwnerOption[]>(
    () =>
      canChange
        ? (users.data ?? []).filter((item) => item.attivo).map(({ id, nome }) => ({ id, nome }))
        : [],
    [canChange, users.data],
  )

  function nameOf(ownerId: string | null | undefined): string | null {
    if (ownerId === null || ownerId === undefined) return null
    if (user?.id === ownerId) return `${user.nome} (tu)`
    const known = (canChange ? users.data : undefined)?.find((item) => item.id === ownerId)
    return known ? known.nome : 'Un collega'
  }

  return { canChange, options, nameOf }
}
