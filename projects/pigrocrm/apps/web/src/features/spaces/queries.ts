import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { queryKeys } from '@/lib/query'

/**
 * The other spaces this identity may reach (design §3, REB-345), live: the chooser the
 * login page shows and the shell's space menu draw from this one read.
 *
 * Silent on a 401 or a failure, the shape the chooser has always had: a stranger, or an
 * identity with no live cookie, simply sees no spaces, so there is no retry and no error
 * to show. A failed read throws rather than answering `[]`, so a refetch that fails keeps
 * the list already known instead of overwriting it with "none".
 */
export function useIdentitySpaces() {
  return useQuery({
    queryKey: queryKeys.identitySpaces,
    queryFn: async () => {
      const { data } = await api.GET('/api/identity/spaces')
      if (!Array.isArray(data)) throw new Error('identity/spaces: no list')
      return data
    },
    retry: false,
  })
}

/**
 * The installation's own name (design §1, 2026-09-08 spec §6): the grandfathered root is no
 * registry row, the API lists it under its own `PIGROCRM_ROOT_SLUG` name (REB-583), and it
 * is reachable both unprefixed and under that alias. `slug` is `null` until the API has
 * answered and `''` when this installation has no name; `failed` is a read that gave
 * nothing, which a caller may treat as "no name" (the login page) or keep waiting on (the
 * space menu, which hides itself rather than guess). A refetch with no `data` never
 * overwrites an already-known slug with "no root at all".
 */
export function useRootSlug(): { slug: string | null; failed: boolean; refetch: () => void } {
  const query = useQuery({
    queryKey: queryKeys.rootTenant,
    queryFn: async () => {
      const { data } = await api.GET('/api/tenants/root')
      if (!data) throw new Error('tenants/root: no body')
      return data.slug ?? ''
    },
    retry: false,
  })
  return {
    slug: query.data ?? null,
    failed: query.isError,
    refetch: () => void query.refetch(),
  }
}
