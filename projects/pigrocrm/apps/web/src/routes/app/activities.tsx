import { createFileRoute } from '@tanstack/react-router'
import { AttivitaPage } from '@/features/attivita/AttivitaPage'

// Only `Route` is exported: a route file exporting anything else opts that route out of
// the router plugin's automatic code-splitting, which is why the page lives in `features/`.
export const Route = createFileRoute('/app/activities')({ component: AttivitaPage })
