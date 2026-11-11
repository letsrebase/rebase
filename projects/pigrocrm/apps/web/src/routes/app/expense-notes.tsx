import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ExpenseNotesPage } from '@/features/expense-notes/ExpenseNotesPage'
import { compactSearch, validateExpenseNotesSearch } from '@/features/expense-notes/search'

/**
 * `/app/expense-notes?tab=&status=&person=&category=&legal_entity=&note=`, «Note spese»
 * (REB-1046). The administrator's tab, a staff member's status filter, the filters and the open
 * note are in the URL; the page is `features/expense-notes/ExpenseNotesPage.tsx`, so this file
 * exports only `Route`. It belongs to the modules `staff` and `purchases` together
 * (`ROUTE_MODULES`): with either off the route answers a sentence naming the module.
 */
function ExpenseNotesRoute() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return (
    <ExpenseNotesPage
      search={search}
      onSearchChange={(patch, options) =>
        void navigate({
          search: (previous) => compactSearch({ ...previous, ...patch }),
          replace: options?.replace,
        })
      }
    />
  )
}

export const Route = createFileRoute('/app/expense-notes')({
  validateSearch: validateExpenseNotesSearch,
  component: ExpenseNotesRoute,
})
