import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `invoices/index.tsx` is the real page now.
export const Route = createFileRoute('/app/fatture/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/invoices' })
  },
})
