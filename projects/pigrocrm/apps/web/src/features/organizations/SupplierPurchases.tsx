import { useNavigate } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { DataTable } from '@/components/DataTable'
import { Button } from '@rebase/ui/button'
import { buildPurchaseColumns } from '@/features/purchases/columns'
import { useCostCategories, usePurchases } from '@/features/purchases/queries'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

/**
 * The «Acquisti» tab of a supplier: the purchases list filtered by this organization, the
 * same table as «Acquisti» (the approved brief of 2026-11-10). A row opens the purchase's
 * own page; a writer starts a new purchase with the supplier already chosen.
 */
export function SupplierPurchases({ supplierId }: { supplierId: string }) {
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const purchases = usePurchases({ supplier_id: supplierId })
  // Archived categories included: a row filed under one before it was archived still names it.
  const categories = useCostCategories(true)
  const schema = useEntitySchema('purchase')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold">Acquisti</h2>
        {canWrite && (
          <Button
            onClick={() =>
              void navigate({ to: '/app/purchases/new', search: { supplier_id: supplierId } })
            }
          >
            <Plus className="mr-2 size-4" />
            Nuovo acquisto
          </Button>
        )}
      </div>
      <DataTable
        label="Acquisti del fornitore"
        columns={buildPurchaseColumns(categories.data ?? [], schema.data?.custom_fields ?? [])}
        data={purchases.data?.items ?? []}
        isLoading={purchases.isLoading}
        loadingLabel="Leggo gli acquisti…"
        isError={purchases.isError}
        error={purchases.error}
        onRetry={() => void purchases.refetch()}
        onRowClick={(row) =>
          void navigate({ to: '/app/purchases/$purchaseId', params: { purchaseId: row.id } })
        }
        emptyMessage="Nessun acquisto da questo fornitore."
      />
    </div>
  )
}
