import { createFileRoute } from '@tanstack/react-router'
import { JobsPanel } from '@/features/settings/JobsPanel'

// Only `Route` is exported: anything else opts this route out of the router plugin's
// automatic code-splitting. The panel itself lives in `features/settings/`.
export const Route = createFileRoute('/app/settings/jobs')({
  component: JobsPanel,
})
