import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ExportPage } from '@/features/export/ExportPage'
import { validateExportSearch } from '@/features/export/search'

/**
 * `/app/export?tab=&export=`, «Esporta per il commercialista» (REB-1028). The tab and the open
 * export are in the URL; the page is `features/export/ExportPage.tsx` and the validator
 * `features/export/search.ts`, so this file exports only `Route` and the router plugin keeps
 * code-splitting it. The page belongs to no module (the registers it reads are the core's, and
 * which parts an export holds follows the modules on the server); it is an admin's, and a
 * collaborator who arrives on a stale link gets the API's own refusal.
 */
function ExportRoute() {
  const { tab, export: open } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return (
    <ExportPage
      tab={tab}
      item={open ?? null}
      onTabChange={(next) => void navigate({ search: { tab: next } })}
      onItemChange={(next) =>
        void navigate({ search: (previous) => ({ tab: previous.tab, ...(next ? { export: next } : {}) }) })
      }
    />
  )
}

export const Route = createFileRoute('/app/export')({
  validateSearch: validateExportSearch,
  component: ExportRoute,
})
