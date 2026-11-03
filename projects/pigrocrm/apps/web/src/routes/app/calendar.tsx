import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { CalendarPage } from '@/features/calendar/CalendarPage'
import { monthOf } from '@/features/calendar/month'

// Only `Route` is exported: a route file exporting anything else opts that route out of
// the router plugin's automatic code-splitting -- the same reason the calendar's own
// components live under `features/`.
//
// No admin guard, and a top-level sibling of `hours`: the month of one's own days and
// deadlines is what every authenticated role has, and the API scopes the hours to the
// caller by default.
function Calendar() {
  const { month } = Route.useSearch()
  const navigate = useNavigate()
  return (
    <CalendarPage
      // The month is a search param so a link to a month is a link, and the default is
      // computed in the browser's own calendar: the server's `today` marks the current
      // *day* in the response, which is the value the grid trusts for «today».
      month={month ?? monthOf(new Date())}
      onMonthChange={(next) => void navigate({ to: '/app/calendar', search: { month: next } })}
    />
  )
}

export const Route = createFileRoute('/app/calendar')({
  component: Calendar,
  // `AAAA-MM` or nothing. Validated here as well as by the API: a malformed month in a
  // pasted URL becomes «this month» rather than a request the server refuses and a page
  // that shows a banner about a query string.
  validateSearch: (search: Record<string, unknown>): { month?: string } => {
    const raw = search.month
    const month = typeof raw === 'string' && /^\d{4}-(?:0[1-9]|1[0-2])$/.test(raw) ? raw : undefined
    return { month }
  },
})
