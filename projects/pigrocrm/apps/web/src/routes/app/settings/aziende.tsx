import { createFileRoute } from '@tanstack/react-router'
import { AziendePanel } from '@/features/settings/AziendePanel'

export const Route = createFileRoute('/app/settings/aziende')({ component: AziendePanel })
