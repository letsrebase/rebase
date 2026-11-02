import { createFileRoute } from '@tanstack/react-router'
import { ContractDetail } from '@/features/contracts/ContractDetail'

export const Route = createFileRoute('/app/contracts/$contractId')({ component: ContractDetail })
