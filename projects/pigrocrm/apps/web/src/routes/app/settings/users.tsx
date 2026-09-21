import { createFileRoute } from '@tanstack/react-router'
import { UsersPanel } from '@/features/settings/UsersPanel'

export const Route = createFileRoute('/app/settings/users')({ component: UsersPanel })
