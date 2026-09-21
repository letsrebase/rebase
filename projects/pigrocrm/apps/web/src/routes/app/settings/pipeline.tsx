import { createFileRoute } from '@tanstack/react-router'
import { PipelinePanel } from '@/features/settings/PipelinePanel'

export const Route = createFileRoute('/app/settings/pipeline')({ component: PipelinePanel })
