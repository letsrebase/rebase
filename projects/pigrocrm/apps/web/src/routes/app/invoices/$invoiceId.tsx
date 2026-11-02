import { createFileRoute } from '@tanstack/react-router'
import { InvoiceDetail } from '@/features/invoices/InvoiceDetail'

export const Route = createFileRoute('/app/invoices/$invoiceId')({ component: InvoiceDetail })
