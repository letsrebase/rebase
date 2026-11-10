import { useNavigate } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { DataTable } from '@/components/DataTable'
import { LoadMore } from '@/components/LoadMore'
import { Button } from '@rebase/ui/button'
import { buildPurchaseColumns } from '@/features/purchases/columns'
import { useCostCategories, usePurchaseList } from '@/features/purchases/queries'
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
  // The registered ones, a page at a time: a cancelled purchase is not what the supplier sold us.
  const purchases = usePurchaseList({ supplier_id: supplierId, status: 'registered' })
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
        data={purchases.items}
        isLoading={purchases.isLoading}
        loadingLabel="Leggo gli acquisti…"
        isError={purchases.isError}
        error={purchases.error}
        onRetry={purchases.retry}
        onRowClick={(row) =>
          void navigate({ to: '/app/purchases/$purchaseId', params: { purchaseId: row.id } })
        }
        emptyMessage="Nessun acquisto da questo fornitore."
      />
      {purchases.hasMore && (
        <LoadMore
          label="Carica altri"
          message={
            purchases.totalCount !== undefined
              ? `Mostrati ${purchases.items.length} acquisti su ${purchases.totalCount}.`
              : `Mostrati ${purchases.items.length} acquisti, ce ne sono altri.`
          }
          isFetchingMore={purchases.isFetchingMore}
          hasError={purchases.isError}
          error={purchases.error}
          onLoadMore={purchases.loadMore}
        />
      )}
    </div>
  )
}
