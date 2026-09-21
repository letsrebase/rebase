import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `settings/rates.tsx` is the real page now.
export const Route = createFileRoute('/app/impostazioni/tariffe')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/rates' })
  },
})
