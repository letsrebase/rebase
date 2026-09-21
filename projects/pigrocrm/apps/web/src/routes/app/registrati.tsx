import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `register.tsx` is the real page now; nginx also 301s
// this exact path at the edge (deploy/nginx/pigro.letsrebase.conf,
// preview.pigro.letsrebase.conf), and the hub's own `Area.tsx` still links here
// until that cross-project update lands separately.
export const Route = createFileRoute('/app/registrati')({
  beforeLoad: () => {
    throw redirect({ to: '/app/register' })
  },
})
