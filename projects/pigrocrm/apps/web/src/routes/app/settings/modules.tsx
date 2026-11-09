import { createFileRoute } from '@tanstack/react-router'
import { ModulesPanel } from '@/features/settings/ModulesPanel'

export const Route = createFileRoute('/app/settings/modules')({ component: ModulesPanel })
