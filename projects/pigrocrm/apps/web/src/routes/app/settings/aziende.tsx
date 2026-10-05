import { createFileRoute } from '@tanstack/react-router'
import { LegalEntitiesPanel } from '@/features/settings/LegalEntitiesPanel'

export const Route = createFileRoute('/app/settings/aziende')({ component: LegalEntitiesPanel })
