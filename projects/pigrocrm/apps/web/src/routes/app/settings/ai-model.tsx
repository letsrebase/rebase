import { createFileRoute } from '@tanstack/react-router'
import { AiModelPanel } from '@/features/settings/AiModelPanel'

export const Route = createFileRoute('/app/settings/ai-model')({ component: AiModelPanel })
