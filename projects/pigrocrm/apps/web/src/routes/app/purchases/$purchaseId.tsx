import { createFileRoute } from '@tanstack/react-router'
import { PurchaseDetail } from '@/features/purchases/PurchaseDetail'

export const Route = createFileRoute('/app/purchases/$purchaseId')({ component: PurchaseDetail })
