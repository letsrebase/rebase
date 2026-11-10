import { createFileRoute } from '@tanstack/react-router'
import { DealDetail } from '@/features/deals/DealDetail'

export const Route = createFileRoute('/app/deal/$dealId')({ component: DealDetail })
