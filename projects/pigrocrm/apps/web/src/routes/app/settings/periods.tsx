import { createFileRoute } from '@tanstack/react-router'
import { PeriodsPanel } from '@/features/settings/PeriodsPanel'

export const Route = createFileRoute('/app/settings/periods')({ component: PeriodsPanel })
