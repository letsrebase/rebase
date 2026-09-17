import { initAnalytics } from '@rebase/analytics/browser'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { analyticsMiddleware } from './lib/analytics'
import { api } from './lib/api'
import { AuthProvider } from './lib/auth'
import { stripEntraToken } from './lib/entra-token'
import { queryClient } from './lib/query'
import { tenantPrefix } from './lib/tenant'
import { routeTree } from './routeTree.gen'
import './styles/tokens.css'

// Under a space the same routes live at `/<slug>/app/...`: the basepath is the one
// place the prefix enters the router, so every `to: '/app/...'` keeps working as is.
const router = createRouter({ routeTree, basepath: tenantPrefix || undefined })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

// Before anything else, including analytics: `initAnalytics` (below) captures a
// pageview with the URL as its first act, so a magic-link token sitting in `?t=`
// would already be in that event by the time the entra route ever mounts to strip it
// itself (REB-229). A no-op on every other route.
stripEntraToken()

// Once, before anything renders: `initAnalytics` decides on the hostname whether this
// page is measured at all (nothing on localhost), and every wrapper after it is a
// no-op until it has. Every text in a replay is masked, not only the inputs: a
// recording of the CRM shows where a person clicks and stops, never an invoice
// amount or a customer's name.
initAnalytics({ maskText: true })
api.use(analyticsMiddleware())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
