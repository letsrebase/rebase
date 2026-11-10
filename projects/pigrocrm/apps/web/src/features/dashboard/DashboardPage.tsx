import { LayoutDashboard } from 'lucide-react'
import type { ReactNode } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { LoadingState } from '@/components/LoadingState'
import { HomeTaxDates } from '@/features/taxes/TaxDates'
import { TAB_MODULES, useModules } from '@/lib/modules'
import { CommercialTab } from './CommercialTab'
import { DaFareBand } from './DaFareBand'
import { DaysTab } from './DaysTab'
import { EconomicTab } from './EconomicTab'
import { PeriodPicker } from './PeriodPicker'
import { ReceivablesTab } from './ReceivablesTab'
import { availableTabs, resolveTab, type DashboardSearch } from './search'
import { YearTab } from './YearTab'

/**
 * Four tabs and a period, all of it driven by the URL.
 *
 * The search object comes in as a prop and every change goes back out through
 * `onSearchChange` rather than into local state, so the route can push it into the URL and
 * a shared link reopens the same dashboard (§4). It also makes the round trip testable
 * without standing up a router: what comes in is what the tab requests, what the controls
 * change is what comes back out.
 *
 * Lives here rather than in `routes/app/index.tsx` for the reason `SettingsLayout` does: a
 * route file exporting anything besides `Route` opts that route out of the router plugin's
 * automatic code-splitting.
 */
export function DashboardPage({
  search,
  onSearchChange,
  notice,
  lead,
}: {
  search: DashboardSearch
  onSearchChange: (next: Partial<DashboardSearch>) => void
  /** Drawn above the tab's content, under the header: the Home's «Completa lo spazio»
   *  card (REB-222), which belongs to the page and not to any one tab. */
  notice?: ReactNode
  /** Drawn right under the title, before the day's tasks and the tabs: the phone's money
   *  figures (REB-1025), which lead the Home there. */
  lead?: ReactNode
}) {
  const { from, to, base } = search
  // The tabs of a module the space has switched off are not drawn, and a URL naming one
  // opens the first tab that is (REB-953).
  const modules = useModules()
  const tab = resolveTab(search.tab, modules)
  // A tab of a module is not mounted before the modules are known: it would ask the API for
  // what a switched-off module refuses, for the length of one read.
  const waiting = modules.loading && (TAB_MODULES[search.tab] ?? []).length > 0

  // The page owns its own intestazione since the 2026-09-08 revision: §4 draws the tabs
  // closed by their rule, and the period picker is the one control this screen puts top
  // right. The route above only supplies the search object and
  // pushes the changed one into the URL, so there is exactly one `<h1>` on the home page.
  return (
    <Tabs value={tab} onValueChange={(next) => onSearchChange({ tab: next as typeof tab })}>
      <PageHeader
        icon={LayoutDashboard}
        title="Home"
        // No picker on the scadenziario: it has no period (what is owed is owed today), and a
        // control that changed nothing on screen would be a promise the page does not keep.
        actions={
          tab === 'receivables' ? undefined : (
            <PeriodPicker period={{ from, to }} onChange={(next) => onSearchChange(next)} />
          )
        }
        // The late and due-today tasks (REB-874): the first thing under the title, with
        // the analysis tabs starting under it.
        // The next two tax dates sit under it (REB-1026), on every tab.
        aboveTabs={
          <>
            {lead && <div className="px-8 pb-2">{lead}</div>}
            <DaFareBand />
            <HomeTaxDates variant="band" />
          </>
        }
        tabs={
          <TabsList variant="line">
            {availableTabs(modules).map((candidate) => (
              <TabsTrigger key={candidate.id} value={candidate.id}>
                {candidate.label}
              </TabsTrigger>
            ))}
          </TabsList>
        }
      />

      <div className="px-8 py-6">
        {notice}
        {/* One tab is mounted at a time, deliberately. Rendering all three and hiding two
            would issue three requests -- three snapshot transactions, each holding two
            pooled connections on the API side -- to draw one screen. §17's placeholders that
            stood here until 6C landed are gone: both dashboards exist now, so a paragraph
            explaining their absence would be the untrue thing on the page. */}
        {waiting && <LoadingState />}
        {!waiting && tab === 'commercial' && <CommercialTab period={{ from, to }} />}
        {!waiting && tab === 'receivables' && <ReceivablesTab />}
        {!waiting && tab === 'year' && <YearTab period={{ from, to }} />}
        {!waiting && tab === 'work_units' && <DaysTab period={{ from, to }} />}
        {!waiting && tab === 'economic' && (
          <EconomicTab
            period={{ from, to }}
            base={base}
            onBaseChange={(next) => onSearchChange({ base: next })}
          />
        )}
      </div>
    </Tabs>
  )
}
