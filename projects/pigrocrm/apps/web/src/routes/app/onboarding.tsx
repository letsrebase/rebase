import { Navigate, createFileRoute } from '@tanstack/react-router'
import { defaultDashboardSearch } from '@/features/dashboard/search'

/**
 * `/app/onboarding`: where «Primi passi» sends an administrator who answered «Decido dopo».
 * The screen itself is drawn by the gate in `routes/app.tsx`, outside the shell, and only
 * while the onboarding is skipped; for anybody else, or once the space has a profile, this
 * route is not a page and the person goes Home. Exports nothing but `Route`.
 */
export const Route = createFileRoute('/app/onboarding')({
  component: () => <Navigate to="/app" search={defaultDashboardSearch()} />,
})
