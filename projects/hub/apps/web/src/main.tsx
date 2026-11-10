import { initAnalytics } from '@rebase/analytics/browser'
import { stripVerifyToken } from '@rebase/analytics/verify-token'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { stripAnswerLink } from './lib/answer-link'
import { router } from './router'
import './styles/tokens.css'

// Before anything else, including analytics: `initAnalytics` (below) captures a
// pageview with the URL as its first act, so a magic-link token sitting in `?t=`
// would already be in that event by the time the verify route ever mounts to strip it
// itself (REB-273). The magic-link mails sent before the 2026-11 rename point at
// `/hub/entra`, which redirects to `/hub/verify`: the token leaves the URL on both. A
// no-op on every other route. The availability mail's answer page
// carries a token of the same kind, and loses it the same way (REB-517).
stripVerifyToken(/^\/hub\/(verify|entra)$/)
stripAnswerLink()

// Once, before anything renders: `initAnalytics` decides on the hostname whether this
// page is measured at all (nothing on localhost), and every wrapper after it is a
// no-op until it has. The public routes (the chooser, the wizards, `/team`) are anonymous
// pages, so nothing is measured there until the visitor said yes to the website's cookie
// notice; a person who signs in is measured regardless, per the 2026-09-12 decision
// (`requireConsent`, HWEB-A-01). Every text in a replay is masked, not only the inputs (REB-274):
// the wizards are forms, but the admin area renders every freelancer's and company's
// name, email, rate and links as plain text, and a recording of an admin browsing it
// is a copy of the candidate database at a third party otherwise.
initAnalytics({ maskText: true, requireConsent: true })

// Thirty seconds fresh and no refetch on focus: an admin list is a stack of infinite-query
// pages, and refetching every loaded page each time the window regains focus (or on
// every back navigation) is a sequential burst of requests for data that has not
// changed. The polling queries (`Campaign`, `AiModels`) set their own `refetchInterval`.
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000, refetchOnWindowFocus: false } },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
