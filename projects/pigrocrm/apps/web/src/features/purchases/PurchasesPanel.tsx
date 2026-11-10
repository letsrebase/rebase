import { useNavigate } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { DataTable } from '@/components/DataTable'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'
import { buildPurchaseColumns } from './columns'
import { useCostCategories, usePurchases } from './queries'

/**
 * The deal's purchases (invoices, credit notes, receipts and expenses), mounted under the
 * hours table on the Ore tab.
 *
 * They sit there in 4A rather than on an «Economia» tab because that tab does not exist
 * until 4B, and a purchase with nowhere to be entered is a purchase nobody records.
 *
 * `dealId` is optional: without one this is the general-purchases list (§7.4), and a new
 * purchase composed here is filed with `deal_id: null` rather than against some default
 * deal.
 *
 * It holds no form: «Registra acquisto» opens the page of «Nuovo acquisto» with this deal, and
 * a row opens the purchase's own page, where it is edited (the approved brief of 2026-11-10).
 */
export function PurchasesPanel({ dealId }: { dealId?: string }) {
  const purchases = usePurchases(dealId === undefined ? { only_general: true } : { deal_id: dealId })
  // Archived categories included, deliberately: a row filed under one before it was
  // archived must still show its name.
  const categories = useCostCategories(true)
  const schema = useEntitySchema('purchase')
  const canWrite = useCanWrite()
  const navigate = useNavigate()

  // A failed request is "we do not actually know", which is a different claim from
  // "there is nothing here" -- and not this component's to make on the caller's behalf.
  // The category list is in the check too: without it every row's Categoria column
  // would read as a dash, which looks like data rather than like a failure.
  if (purchases.isError || categories.isError) {
    return <QueryErrorBanner error={purchases.error ?? categories.error} />
  }

  const customFields = schema.data?.custom_fields ?? []
  const all = categories.data ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Acquisti</h2>
          <p className="text-sm text-muted-foreground">
            {dealId === undefined
              ? 'Gli acquisti che non appartengono a nessun deal.'
              : 'Gli acquisti fatti per questo deal.'}{' '}
            Un totale negativo è un rimborso o una nota di credito, non un errore.
          </p>
        </div>
        {canWrite && (
          <Button
            onClick={() => void navigate({ to: '/app/purchases/new', search: { deal_id: dealId } })}
          >
            <Plus className="mr-2 size-4" />
            Registra acquisto
          </Button>
        )}
      </div>

      <DataTable
        label="Acquisti"
        columns={buildPurchaseColumns(all, customFields)}
        data={purchases.data?.items ?? []}
        isLoading={purchases.isLoading}
        emptyMessage={
          dealId === undefined
            ? 'Nessun acquisto generale registrato.'
            : 'Nessun acquisto registrato su questo deal.'
        }
        onRowClick={(row) =>
          void navigate({ to: '/app/purchases/$purchaseId', params: { purchaseId: row.id } })
        }
      />
    </div>
  )
}
