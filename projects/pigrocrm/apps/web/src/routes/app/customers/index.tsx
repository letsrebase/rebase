import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (REB-969), see `customers/$customerId.tsx`. The `?search=` term of a
// palette link is carried over, and dropped when it is not a non-empty string, as the real
// page does.
export const Route = createFileRoute('/app/customers/')({
  validateSearch: (search: Record<string, unknown>): { search?: string } => ({
    search:
      typeof search.search === 'string' && search.search.length > 0 ? search.search : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/app/organizations', search })
  },
})
