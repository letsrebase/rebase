import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (REB-969): the registry is «organizations» now, and a link already in a
// sent mail or a reminder still says `/app/customers/<id>` (`core/reminders/links.py`, the
// 2026-11-09 row of docs/design/DECISIONS.md). `organizations/$organizationId.tsx` is the
// real page.
export const Route = createFileRoute('/app/customers/$customerId')({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: '/app/organizations/$organizationId',
      params: { organizationId: params.customerId },
    })
  },
})
