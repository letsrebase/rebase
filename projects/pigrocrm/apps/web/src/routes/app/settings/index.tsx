import { createFileRoute, redirect } from '@tanstack/react-router'

// Mirrors routes/index.tsx's own bare "/" -> "/app" redirect: the tabbed layout
// (routes/app/settings.tsx) has no content of its own to show for the bare
// "/app/settings" path (typed directly, or reached via a stale link), so
// this sends it to the first tab rather than leaving `<Outlet />` empty there.
export const Route = createFileRoute('/app/settings/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/fields' })
  },
})
