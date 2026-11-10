import { createFileRoute } from '@tanstack/react-router'
import { PersonDetail } from '@/features/people/PersonDetail'

export const Route = createFileRoute('/app/people/$personId')({ component: PersonDetail })
