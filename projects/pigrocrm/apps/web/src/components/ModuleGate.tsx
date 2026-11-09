import { useLocation } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { modulesOfPath, useModules } from '@/lib/modules'
import { ModuleOff, RoutePending } from './RouteFallbacks'

/**
 * The one guard of the pages a module owns (REB-953, spec 2026-11-09 A2 and B2): what
 * stands between the shell and the router's `<Outlet />`. A path that `ROUTE_MODULES`
 * gives to a module that is off shows «Il modulo … è spento» instead of the page, so a
 * stale link or bookmark explains itself rather than rendering a page whose every request
 * answers 404, and the page's component is never mounted, so it asks the API for nothing.
 *
 * One mechanism at the layout, not a check per route file: a route file exports only
 * `Route`, and a new page of a module is covered by the prefix its sibling already has in
 * the table. While the first read of the modules is on its way a guarded path waits (a
 * pending page, once: the answer is cached afterwards) rather than flash the page and take
 * it away; a read that failed is not an answer and lets the page through.
 */
export function ModuleGate({ children }: { children: ReactNode }) {
  // The pathname is basepath-relative (the router strips the tenant prefix), the shape the
  // table is written in.
  const { pathname } = useLocation()
  const modules = useModules()
  const needed = modulesOfPath(pathname.replace(/\/+$/, ''))
  if (needed.length === 0) return children
  if (modules.loading) return <RoutePending />
  const off = modules.firstOff(...needed)
  return off === undefined ? children : <ModuleOff module={modules.label(off)} />
}
