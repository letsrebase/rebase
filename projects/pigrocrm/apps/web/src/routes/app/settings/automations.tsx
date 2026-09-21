import { createFileRoute } from '@tanstack/react-router'
import { AutomationsPanel } from '@/features/settings/AutomationsPanel'

// Only `Route` is exported: anything else opts this route out of the router plugin's
// automatic code-splitting. The panel itself lives in `features/settings/`.
export const Route = createFileRoute('/app/settings/automations')({
  component: AutomationsPanel,
})
