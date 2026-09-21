import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `settings/pipeline.tsx` is the real page now.
export const Route = createFileRoute('/app/impostazioni/pipeline')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/pipeline' })
  },
})
