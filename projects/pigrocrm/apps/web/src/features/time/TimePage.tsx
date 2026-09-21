import { ChevronLeft, ChevronRight, Clock } from 'lucide-react'
import { useMemo, useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useDeals } from '@/features/deals/queries'
import { useAuth, useCanWrite } from '@/lib/auth'
import { useTimeEntries } from './queries'
import { TimeRegister } from './TimeRegister'
import { TimerBar } from './TimerBar'
import { WeekGrid } from './WeekGrid'
import { shiftWeek, weekDays } from './week'

const TABS = [
  { id: 'registro', label: 'Registro' },
  { id: 'settimana', label: 'Settimana' },
] as const
type TabId = (typeof TABS)[number]['id']

/**
 * `/app/hours`, in two views over one week of one person's hours.
 *
 * «Registro» is the Toggl shape asked for on 2026-09-09: a bar to start a timer or add a
 * line by hand, and under it the week as a list of what was done, day by day. «Settimana»
 * is the grid slice 4 §15 designed, unchanged: deals down, days across, a cell per hour,
 * the view that shows a Tuesday nobody entered. The two share the week chosen here and
 * the same two reads, so switching tabs costs no request.
 *
 * One `<h1>`, the week as its description and the week controls as its actions, exactly
 * as `WeekGrid` drew them before it became the second tab.
 */
export function TimePage() {
  const { user } = useAuth()
  const canWrite = useCanWrite()
  const userId = user?.id
  const [anchor, setAnchor] = useState(() => new Date())
  const [tab, setTab] = useState<TabId>('registro')
  const days = useMemo(() => weekDays(anchor), [anchor])

  const entries = useTimeEntries(
    { user_id: userId, da: days[0]?.iso, a: days[6]?.iso },
    // See `useTimeEntries`' own docstring: an unfiltered list is answered, for an admin,
    // with the whole team's hours, so this must not fire before the session is known.
    { enabled: userId !== undefined },
  )
  const deals = useDeals()
  const dealNames = useMemo(
    () => new Map((deals.data?.items ?? []).map((deal) => [deal.id, deal.nome])),
    [deals.data],
  )

  const header = (
    <PageHeader
      icon={Clock}
      title="Ore"
      description={`${days[0]?.label} — ${days[6]?.label}`}
      actions={
        <>
          <Button
            variant="outline"
            size="icon"
            aria-label="Settimana precedente"
            onClick={() => setAnchor((current) => shiftWeek(current, -1))}
          >
            <ChevronLeft className="size-4" />
          </Button>
          <Button variant="outline" onClick={() => setAnchor(new Date())}>
            Questa settimana
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="Settimana successiva"
            onClick={() => setAnchor((current) => shiftWeek(current, 1))}
          >
            <ChevronRight className="size-4" />
          </Button>
        </>
      }
      tabs={
        <TabsList variant="line">
          {TABS.map((candidate) => (
            <TabsTrigger key={candidate.id} value={candidate.id}>
              {candidate.label}
            </TabsTrigger>
          ))}
        </TabsList>
      }
    />
  )

  // A failed request is neither "loading" nor "there is nothing here", and a register or
  // a grid drawn empty for it would say the second -- a week with no hours in it, which
  // is exactly the claim this screen exists to make loudly. The banner instead.
  let body: React.ReactNode
  if (entries.isError || deals.isError) {
    body = <QueryErrorBanner error={entries.error ?? deals.error} />
  } else if (userId === undefined || entries.isPending || deals.isPending) {
    body = <p className="text-sm text-muted-foreground">Caricamento…</p>
  } else if (tab === 'registro') {
    body = (
      <>
        <TimerBar deals={deals.data.items} userId={userId} canWrite={canWrite} />
        <TimeRegister
          entries={entries.data.items}
          days={days}
          dealNames={dealNames}
          canWrite={canWrite}
        />
      </>
    )
  } else {
    body = (
      <WeekGrid days={days} userId={userId} entries={entries.data.items} deals={deals.data.items} />
    )
  }

  return (
    <Tabs value={tab} onValueChange={(next) => setTab(next as TabId)}>
      {header}
      <div className="space-y-6 px-8 py-6">{body}</div>
    </Tabs>
  )
}
