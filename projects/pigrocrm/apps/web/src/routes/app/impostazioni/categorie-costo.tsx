import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `settings/cost-categories.tsx` is the real page now.
export const Route = createFileRoute('/app/impostazioni/categorie-costo')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/cost-categories' })
  },
})
