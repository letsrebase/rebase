import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): a magic-link email or bookmark that still points here
// keeps working. `verify.tsx` is the real page now; nginx also 301s this exact path
// at the edge (deploy/nginx/pigro.letsrebase.conf, preview.pigro.letsrebase.conf,
// spa.conf), but the client-side router needs its own redirect too for local dev,
// where nothing sits in front of the Vite server. `t` is declared here, the same
// shape `verify.tsx` itself validates, so the token survives the hop.
export const Route = createFileRoute('/app/entra')({
  validateSearch: (search: Record<string, unknown>): { t?: string } => ({
    t: typeof search.t === 'string' ? search.t : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/app/verify', search: { t: search.t ?? '' } })
  },
})
