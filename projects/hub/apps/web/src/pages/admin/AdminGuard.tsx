import { Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect } from 'react'
import { useMe } from '@/lib/me'

/**
 * `/admin/*`'s own access rule, nested one level inside `SignedInLayout`'s guard
 * (REB-279): a signed-in non-admin does not see a blank frame (REB-106) and does not
 * get sent to a login form -- they land on their own area with a short sentence saying
 * why. By the time this mounts, `SignedInLayout` has already resolved `useMe()` to a
 * non-null person, so reading the cached query here costs no extra fetch.
 */
export function AdminGuard() {
  const me = useMe()
  const navigate = useNavigate()
  const isAdmin = me.data?.role === 'admin'

  useEffect(() => {
    if (me.data && !isAdmin) void navigate({ to: '/me', search: { negato: true }, replace: true })
  }, [me.data, isAdmin, navigate])

  if (!isAdmin) return null
  return <Outlet />
}
