import { Link, Outlet, useRouterState } from '@tanstack/react-router'
import { Settings, ShieldAlert } from 'lucide-react'
import { defaultDashboardSearch } from '@/features/dashboard/search'
import { SETTINGS_TABS } from '@/features/settings/tabs'
import { PageHeader } from '@/components/PageHeader'
import { Button } from '@rebase/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useIsAdmin } from '@/lib/auth'

/**
 * Campi/Pipeline/Template/Emittente/Utenti are admin-only at the service layer
 * (`FieldDefinitionService`/`PipelineService`/`TemplateService`/
 * `EmitterProfileService`/`UserService` all call `actor.require_admin` on every
 * write) -- Token is not (`PatService` scopes by `actor.id`, not role) and lives
 * at its own route, `/app/token`, outside this gate entirely.
 *
 * Note the asymmetry on the two newest: `TemplateService.list`/`describe`/
 * `preview` and `EmitterProfileService.get` carry no role check, because the
 * new-from-template dialog and the PDF header need them for every role. Only the
 * writes are gated, and only the writes live behind this tab.
 *
 * The three slice-4 tabs are gated the same way and for the same reason:
 * `CostCategoryService` and `PeriodLockService` call `actor.require_admin` on every
 * write, and so do `update_user_rates` and `update_deal_rate`. All four are also
 * deliberately absent from the MCP surface, so this gate is the explanation of why
 * they are here and never the thing that enforces it.
 *
 * `useIsAdmin` reads the session `AuthProvider` already cached -- no extra
 * request -- and this reads it *before* rendering `<Outlet />`, not in a
 * `useEffect` that would let a first render mount the children regardless.
 * `routes/app.tsx`'s own guard already blocks reaching this component at all
 * until the session has resolved, so there is no separate loading state to
 * account for here. A non-admin gets an explanation in place, never a silent
 * redirect.
 *
 * Lives here, not in `routes/app/impostazioni.tsx` itself, so it can be
 * imported by a plain component test (`SettingsLayout.test.tsx`) the same way
 * every other component in this codebase is -- a route file exporting
 * anything beyond `Route` also opts that route out of the router plugin's
 * automatic code-splitting, which a `routeTree.gen.ts` warning caught
 * directly while wiring this test up.
 *
 * One tab is the exception to the admin gate above: `profilo` (`ProfilePanel`) is a
 * person's own preferences, not an admin-only write, and the weekly digest's own
 * opt-out link (spec 2026-09-16 §3.6) sends whoever received the mail straight to
 * `/app/impostazioni/profilo` -- a collaboratore or readonly account included. Without
 * this exemption that link would be dead for anyone but an admin, which REB-221's
 * first round shipped and its second round exists to fix. A non-admin on that one path
 * sees a `tabs` list of exactly one entry, never the other admin-only tabs.
 */
export function SettingsLayout() {
  const isAdmin = useIsAdmin()
  const { location } = useRouterState()
  const onProfileTab = location.pathname.endsWith('profilo')

  if (!isAdmin && !onProfileTab) {
    return (
      <div className="p-8">
        <div className="mx-auto flex max-w-md flex-col items-center gap-3 pt-16 text-center">
          <ShieldAlert className="size-10 text-muted-foreground" aria-hidden="true" />
          <h1 className="text-xl font-semibold tracking-tight">Accesso riservato</h1>
          <p className="text-muted-foreground">
            Le impostazioni sono riservate agli amministratori. Se ti serve un nuovo campo, uno
            stato della pipeline o un nuovo utente, chiedi a un amministratore del tuo account.
          </p>
          <Button asChild variant="outline" className="mt-2">
            {/* Since slice 6 `/app/` declares `validateSearch`, so its search params are
                part of its type and a `<Link to="/app">` without them does not compile.
                This link has no period of its own in mind, which is exactly what
                `defaultDashboardSearch()` answers -- and sending the resolved month rather
                than an empty object means the address bar is true from the first paint,
                which is the whole reason §4 put the period in the URL. */}
            <Link to="/app" search={defaultDashboardSearch()}>
              Torna alla dashboard
            </Link>
          </Button>
        </div>
      </div>
    )
  }

  // A non-admin who got past the gate above is on `profilo` and nothing else, so the
  // tab strip shows them exactly that one tab -- never a row of admin-only tabs next
  // to the one page they are actually allowed to open.
  const tabs = isAdmin ? SETTINGS_TABS : SETTINGS_TABS.filter((tab) => tab.value === 'profilo')
  const active =
    tabs.find((tab) => location.pathname.endsWith(tab.value))?.value ??
    (isAdmin ? 'campi' : 'profilo')

  // `Tabs` wraps the header rather than sitting under it: the underline tabs of §4 are
  // *part* of the page's intestazione (`PageHeader`'s own `tabs` slot draws them and
  // closes them with the rule), and Radix needs the list inside its own root.
  // `SETTINGS_TABS` stays the single source -- `AppShell` renders the same array as the
  // sidebar's sub-items, so the two cannot drift.
  return (
    <Tabs value={active}>
      <PageHeader
        icon={Settings}
        title="Impostazioni"
        tabs={
          <TabsList variant="line">
            {tabs.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value} asChild>
                <Link to={`/app/impostazioni/${tab.value}`}>{tab.label}</Link>
              </TabsTrigger>
            ))}
          </TabsList>
        }
      />
      <div className="px-8 py-6">
        <Outlet />
      </div>
    </Tabs>
  )
}
