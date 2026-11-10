import { createFileRoute } from '@tanstack/react-router'
import { EngagementPage } from '@/features/engagements/EngagementPage'

export const Route = createFileRoute('/app/engagements/$engagementId')({ component: EngagementPage })
