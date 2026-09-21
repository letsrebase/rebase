import { createFileRoute } from '@tanstack/react-router'
import { ProfilePanel } from '@/features/settings/ProfilePanel'

export const Route = createFileRoute('/app/settings/profile')({ component: ProfilePanel })
