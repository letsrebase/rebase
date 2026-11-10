import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `organizations/$organizationId.tsx` is the real page now.
export const Route = createFileRoute('/app/clienti/$customerId')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/organizations/$organizationId',
      params: { organizationId: params.customerId },
    })
  },
})
