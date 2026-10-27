import { useQuery } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'
import { api, unwrap } from '@/lib/api'
import { useAuth } from '@/lib/auth'

export interface Member {
  id: string
  nome: string
}

/**
 * The active members of the space by name (REB-780): who a deal can be handed to, and how
 * to say who it is in. `GET /api/users` is the administrators' (it carries emails, roles
 * and rates); `GET /api/users/directory` carries only id and name and is open to every
 * role, so a collaborator picks and reads owners as an admin does.
 */
export function useMemberDirectory() {
  const { user } = useAuth()
  const directory = useQuery({
    queryKey: ['member-directory'],
    queryFn: () => unwrap(api.GET('/api/users/directory')),
  })

  const members = useMemo<Member[]>(
    () => (directory.data ?? []).map(({ id, nome }) => ({ id, nome })),
    [directory.data],
  )

  /** The person's name, «(tu)» for the signed-in one, `null` for nobody. A person the
   *  directory does not list (deactivated since) is «Un collega», not a bare id. */
  const nameOf = useCallback(
    (id: string | null | undefined): string | null => {
      if (id === null || id === undefined) return null
      const known = members.find((member) => member.id === id)
      if (id === user?.id) return `${known?.nome ?? user.nome} (tu)`
      return known ? known.nome : 'Un collega'
    },
    [members, user],
  )

  return { members, nameOf, me: user?.id ?? null, isLoading: directory.isLoading }
}
