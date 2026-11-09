import { createFileRoute } from '@tanstack/react-router'
import { OrganizationDetail } from '@/features/organizations/OrganizationDetail'

export const Route = createFileRoute('/app/organizations/$organizationId')({ component: OrganizationDetail })
