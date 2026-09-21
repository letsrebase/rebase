import { createFileRoute } from '@tanstack/react-router'
import { FieldsPanel } from '@/features/settings/FieldsPanel'

export const Route = createFileRoute('/app/settings/fields')({ component: FieldsPanel })
