import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { api, toProblem, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { tenantPrefix } from '@/lib/tenant'
import { useIdentitySpaces, useRootSlug } from './queries'

export type IdentitySpace = components['schemas']['IdentitySpace']

/**
 * Moving between the spaces of one identity, for the shell's space menu and the login
 * chooser alike.
 *
 * `enter` opens a space with no second proof (design §3, REB-345): the identity cookie
 * already is one. A different basepath is a different application instance, so landing
 * there is a full navigation (`go`). An invitation (REB-678) is not a space yet: the API
 * answers the acceptance page, with a token for the address this browser proved, and the
 * page takes it from there (it asks for the name when the invitation has none).
 *
 * `refresh` re-reads the space list and the root name: the shell stays mounted for a whole
 * session, so a space this identity gained after mount -- an invitation accepted in another
 * tab -- would otherwise never show without a full reload, and a transient failure must not
 * leave the root name unknown for the rest of it (Greptile, PR #419).
 */
export function useSpaceSwitcher(go: (url: string) => void) {
  const spacesQuery = useIdentitySpaces()
  const root = useRootSlug()
  // The space being entered right now: disables every row while that request is in flight.
  const [enteringSlug, setEnteringSlug] = useState<string | null>(null)

  const spaces = spacesQuery.data ?? []
  // `tenantPrefix` is `/<slug>` or `''`; at `''` the space already open is the root's own
  // slug when this installation has one, never "no slug". While that answer has not come
  // back yet, `currentSlug` is `null` rather than a guess: the switcher shows nothing
  // sooner than it shows the wrong thing (CodeRabbit, PR #419), and settles once the root
  // slug resolves, even to "no root at all" (`''`).
  const currentSlug = tenantPrefix === '' ? root.slug : tenantPrefix.replace(/^\//, '')
  const otherSpaces =
    currentSlug === null ? [] : spaces.filter((space) => space.slug !== currentSlug)

  async function enter(space: IdentitySpace) {
    const slug = space.slug
    setEnteringSlug(slug)
    try {
      if (space.invito) {
        const door = await unwrap(
          api.POST('/api/identity/invite/{slug}', { params: { path: { slug } } }),
        )
        go(door.url)
        return
      }
      await unwrap(api.POST('/api/identity/enter/{slug}', { params: { path: { slug } } }))
      go(`/${slug}/app/`)
    } catch (error) {
      toast.error(toProblem(error).detail)
      setEnteringSlug(null)
    }
  }

  return {
    spaces,
    otherSpaces,
    root,
    enteringSlug,
    enter,
    refresh: () => {
      void spacesQuery.refetch()
      root.refetch()
    },
  }
}
