import { createFileRoute } from '@tanstack/react-router'
import { RemindersPanel } from '@/features/settings/RemindersPanel'

// Only `Route` is exported: anything else opts this route out of the router plugin's
// automatic code-splitting. The panel lives in `features/settings/`.
export const Route = createFileRoute('/app/settings/reminders')({ component: RemindersPanel })
