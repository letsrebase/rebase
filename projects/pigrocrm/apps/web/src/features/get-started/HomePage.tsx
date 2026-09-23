/**
 * The Home, `/app/` (spec 2026-09-16 §4.1, REB-222).
 *
 * While the space is empty (no customer, deal, time entry or document) the Home is the
 * start page, the same one the sidebar's «Primi passi» opens: a new space has no numbers
 * to chart, and the first screen should bring the person's own work in rather than show
 * them zeros. From the first piece of work on it is the dashboard, with a «Completa lo
 * spazio» card above it while a step is still open that this person could do, then the
 * dashboard alone. The one-time redirect to «Primi passi» of 2026-09-12 (ORB-180) is
 * gone: the Home already is that page when it matters.
 *
 * Nothing is drawn until the four work reads settle, so a space with data never flashes
 * the start page and an empty one never flashes the dashboard (nor sends its requests).
 * Those reads retry like the rest of the app; one that still fails counts as work
 * (`useFirstSteps`), so a broken API lands on the dashboard, whose own requests say so,
 * rather than on a start page that would call the space empty. That fallback is shown,
 * not held: the next read that succeeds decides.
 *
 * The choice is made once per visit and then held. A step's prompt asks the assistant to
 * create a customer, and the reads refresh when the person comes back to the tab: a Home
 * that followed them would swap the start page for the dashboard under the cursor, and
 * take with it a token minted there and not yet copied, which no navigation guard sees.
 * The next visit to the Home reads again.
 */
import { Link } from '@tanstack/react-router'
import { ChevronRight } from 'lucide-react'
import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@rebase/ui/card'
import { useCanWrite } from '@/lib/auth'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { messaggioEsito } from '@/features/gmail/esito'
import type { DashboardSearch, HomeSearch } from '@/features/dashboard/search'
import { useFirstSteps, type FirstStepsState } from './firstSteps'
import { GetStartedPage } from './GetStartedPage'

export function HomePage({
  search,
  onSearchChange,
}: {
  search: HomeSearch
  onSearchChange: (next: Partial<DashboardSearch>) => void
}) {
  const state = useFirstSteps()
  const canWrite = useCanWrite()
  // Held from the first settled answer on, for the reason the module docstring gives.
  // Set during render, React's pattern for state derived from a previous render: the
  // page never draws once with the choice still unmade.
  const [startPage, setStartPage] = useState<boolean | null>(null)
  if (startPage === null && state.spaceEmpty !== null && !state.workFailed) setStartPage(state.spaceEmpty)
  const chosen = startPage ?? state.spaceEmpty
  if (chosen === null) return null
  if (chosen) return <GetStartedPage esito={search.esito} />
  const messaggio = messaggioEsito(search.esito)
  return (
    <DashboardPage
      search={search}
      onSearchChange={onSearchChange}
      notice={
        <>
          {/* The consent flow comes back here only if the API read the space as empty
              and this page did not (a read that failed counts as work): the outcome is
              still the person's to read. */}
          {messaggio ? (
            <p role="status" className="bg-muted/50 mb-6 border px-3 py-2 text-sm">
              {messaggio}
            </p>
          ) : null}
          {state.loading ? null : <CompleteSpaceCard state={state} canWrite={canWrite} />}
        </>
      }
    />
  )
}

/**
 * «Completa lo spazio»: how many steps are done and the way back to the rest. Only
 * while a step is open that this person could do: a readonly user, or a collaboratore
 * whose one open step is the admin's fiscal data, would otherwise be nagged about work
 * that is not theirs.
 */
function CompleteSpaceCard({ state, canWrite }: { state: FirstStepsState; canWrite: boolean }) {
  const open = state.steps.filter((step) => !step.done && step.canDo)
  if (!canWrite || open.length === 0) return null
  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>
          <h2>Completa lo spazio</h2>
        </CardTitle>
        <CardDescription>
          {state.doneCount} di {state.steps.length} primi passi fatti. Gli altri ti aspettano
          in «Primi passi», ognuno con il suo prompt per l’assistente.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button asChild variant="outline">
          <Link to="/app/get-started">
            Vai ai primi passi
            <ChevronRight className="ml-1 size-4" aria-hidden />
          </Link>
        </Button>
      </CardContent>
    </Card>
  )
}
