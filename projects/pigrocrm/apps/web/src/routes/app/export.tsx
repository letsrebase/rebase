import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ExportPage } from '@/features/export/ExportPage'
import { validateExportSearch, type ExportSearch } from '@/features/export/search'

/**
 * `/app/export?tab=&export=&link=`, «Esporta per il commercialista» (REB-1028). The tab and the open
 * export are in the URL; the page is `features/export/ExportPage.tsx` and the validator
 * `features/export/search.ts`, so this file exports only `Route` and the router plugin keeps
 * code-splitting it. The page belongs to no module (the registers it reads are the core's, and
 * which parts an export holds follows the modules on the server); it is an admin's, and a
 * collaborator who arrives on a stale link gets the API's own refusal.
 */
function ExportRoute() {
  const { tab, export: open, link } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const goTo = (search: ExportSearch) => void navigate({ search })
  return (
    <ExportPage
      tab={tab}
      item={open ?? null}
      link={link ?? null}
      onTabChange={(next) => goTo({ tab: next })}
      onItemChange={(next) => goTo({ tab: 'exports', ...(next ? { export: next } : {}) })}
      onLinkChange={(next) => goTo({ tab: 'links', ...(next ? { link: next } : {}) })}
      onOpenLink={(id) => goTo({ tab: 'links', link: id })}
      onOpenExport={(id) => goTo({ tab: 'exports', export: id })}
    />
  )
}

export const Route = createFileRoute('/app/export')({
  validateSearch: validateExportSearch,
  component: ExportRoute,
})
