import { createFileRoute } from '@tanstack/react-router'
import { FiscalPanel } from '@/features/settings/FiscalPanel'

export const Route = createFileRoute('/app/settings/fiscal')({ component: FiscalPanel })
