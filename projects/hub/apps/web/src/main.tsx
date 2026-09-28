import { initAnalytics } from '@rebase/analytics/browser'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { stripAnswerLink } from './lib/answer-link'
import { stripEntraToken } from './lib/entra-token'
import { router } from './router'
import './styles/tokens.css'

// Before anything else, including analytics: `initAnalytics` (below) captures a
// pageview with the URL as its first act, so a magic-link token sitting in `?t=`
// would already be in that event by the time the entra route ever mounts to strip it
// itself (REB-273). A no-op on every other route. The availability mail's answer page
// carries a token of the same kind, and loses it the same way (REB-517).
stripEntraToken()
stripAnswerLink()

// Once, before anything renders: `initAnalytics` decides on the hostname whether this
// page is measured at all (nothing on localhost), and every wrapper after it is a
// no-op until it has. Every text in a replay is masked, not only the inputs (REB-274):
// the wizards are forms, but the admin area renders every freelancer's and company's
// name, email, rate and links as plain text, and a recording of an admin browsing it
// is a copy of the candidate database at a third party otherwise.
initAnalytics({ maskText: true })

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1 } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
