import { createFileRoute } from '@tanstack/react-router'
import { OggiPage } from '@/features/attivita/OggiPage'

// Only `Route` is exported: a route file exporting anything else opts that route out of
// the router plugin's automatic code-splitting, which is why the page lives in `features/`.
export const Route = createFileRoute('/app/oggi')({
  component: OggiPage,
})
