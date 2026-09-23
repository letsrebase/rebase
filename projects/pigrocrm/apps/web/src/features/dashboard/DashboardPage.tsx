import { LayoutDashboard } from 'lucide-react'
import type { ReactNode } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { CommercialTab } from './CommercialTab'
import { EconomicTab } from './EconomicTab'
import { PeriodPicker } from './PeriodPicker'
import { ReceivablesTab } from './ReceivablesTab'
import { DASHBOARD_TABS, type DashboardSearch } from './search'

/**
 * Three tabs and a period, all of it driven by the URL.
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
}: {
  search: DashboardSearch
  onSearchChange: (next: Partial<DashboardSearch>) => void
  /** Drawn above the tab's content, under the header: the Home's «Completa lo spazio»
   *  card (REB-222), which belongs to the page and not to any one tab. */
  notice?: ReactNode
}) {
  const { tab, da, a, base } = search

  // The page owns its own intestazione since the 2026-09-08 revision: §4 draws the tabs
  // as part of the header, closed by its rule, and the period picker is the one control
  // this screen puts top right. The route above only supplies the search object and
  // pushes the changed one into the URL, so there is exactly one `<h1>` on the home page.
  return (
    <Tabs value={tab} onValueChange={(next) => onSearchChange({ tab: next as typeof tab })}>
      <PageHeader
        icon={LayoutDashboard}
        title="Home"
        // No picker on the scadenziario: it has no period (what is owed is owed today), and a
        // control that changed nothing on screen would be a promise the page does not keep.
        actions={
          tab === 'scadenziario' ? undefined : (
            <PeriodPicker periodo={{ da, a }} onChange={(next) => onSearchChange(next)} />
          )
        }
        tabs={
          <TabsList variant="line">
            {DASHBOARD_TABS.map((candidate) => (
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
        {tab === 'commerciale' && <CommercialTab periodo={{ da, a }} />}
        {tab === 'scadenziario' && <ReceivablesTab />}
        {tab === 'economica' && (
          <EconomicTab
            periodo={{ da, a }}
            base={base}
            onBaseChange={(next) => onSearchChange({ base: next })}
          />
        )}
      </div>
    </Tabs>
  )
}
