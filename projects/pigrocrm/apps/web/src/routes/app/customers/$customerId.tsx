import { createFileRoute } from '@tanstack/react-router'
import { CustomerDetail } from '@/features/customers/CustomerDetail'

export const Route = createFileRoute('/app/customers/$customerId')({ component: CustomerDetail })
