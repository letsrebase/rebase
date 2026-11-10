import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { InboxPage } from '@/features/inbox/InboxPage'
import { validateInboxSearch } from '@/features/inbox/search'

/**
 * `/app/inbox?tab=&item=`, «In arrivo» (REB-984). The tab and the open item are in the URL;
 * the page is `features/inbox/InboxPage.tsx` and the validator `features/inbox/search.ts`,
 * so this file exports only `Route` and the router plugin keeps code-splitting it. The inbox
 * is core, never behind a module, and what a person sees of it is the API's to decide.
 */
function InboxRoute() {
  const { tab, item } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  return (
    <InboxPage
      tab={tab}
      item={item ?? null}
      onTabChange={(next) => void navigate({ search: { tab: next } })}
      onItemChange={(next) =>
        void navigate({ search: (previous) => ({ tab: previous.tab, ...(next ? { item: next } : {}) }) })
      }
    />
  )
}

export const Route = createFileRoute('/app/inbox')({
  validateSearch: validateInboxSearch,
  component: InboxRoute,
})
