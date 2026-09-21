import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): mirrors `settings/index.tsx`'s own redirect to the
// first tab.
export const Route = createFileRoute('/app/impostazioni/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/settings/fields' })
  },
})
