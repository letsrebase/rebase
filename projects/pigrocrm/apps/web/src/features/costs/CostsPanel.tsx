import { Plus } from 'lucide-react'
import { useState } from 'react'
import { DataTable } from '@/components/DataTable'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'
import { CostForm } from './CostForm'
import { buildCostColumns } from './columns'
import { costToFormValues } from './formValues'
import { useCostCategories, useCosts, type Cost } from './queries'

/**
 * The deal's direct costs, mounted under the hours table on the Ore tab.
 *
 * They sit there in 4A rather than on an «Economia» tab because that tab does not exist
 * until 4B, and a cost with nowhere to be entered is a cost nobody records.
 *
 * `dealId` is optional: without one this is the general-expenses list (§7.4), and a new
 * cost composed here is filed with `deal_id: null` rather than against some default
 * deal.
 */
export function CostsPanel({ dealId }: { dealId?: string }) {
  const costs = useCosts(dealId === undefined ? { solo_generali: true } : { deal_id: dealId })
  // Archived categories included, deliberately: a row filed under one before it was
  // archived must still show its name, and the picker below is the place that keeps
  // them out of *new* costs.
  const categories = useCostCategories(true)
  const schema = useEntitySchema('cost')
  const canWrite = useCanWrite()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Cost | null>(null)

  // A failed request is "we do not actually know", which is a different claim from
  // "there is nothing here" -- and not this component's to make on the caller's behalf.
  // The category list is in the check too: without it every row's Categoria column
  // would read as a dash, which looks like data rather than like a failure.
  if (costs.isError || categories.isError) {
    return <QueryErrorBanner error={costs.error ?? categories.error} />
  }

  const customFields = schema.data?.custom_fields ?? []
  const all = categories.data ?? []
  const active = all.filter((category) => !category.archiviata)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Costi diretti</h2>
          <p className="text-sm text-muted-foreground">
            {dealId === undefined
              ? 'Le spese che non appartengono a nessun deal.'
              : 'Le spese sostenute per questo deal.'}{' '}
            Un importo negativo è un rimborso o una nota di credito ricevuta, non un errore.
          </p>
        </div>
        {canWrite && (
          <Button onClick={() => setCreating(true)}>
            <Plus className="mr-2 size-4" />
            Registra costo
          </Button>
        )}
      </div>

      <DataTable
        columns={buildCostColumns(all, customFields)}
        data={costs.data?.items ?? []}
        isLoading={costs.isLoading}
        emptyMessage={
          dealId === undefined
            ? 'Nessuna spesa generale registrata.'
            : 'Nessun costo registrato su questo deal.'
        }
        onRowClick={canWrite ? (row) => setEditing(row) : undefined}
      />

      <CostForm
        open={creating}
        onOpenChange={setCreating}
        dealId={dealId}
        categories={active}
        customFields={customFields}
        title="Registra costo"
      />

      {/* Mounted only while a row is selected, and keyed on that row: the dialog seeds
          its state once, when `open` flips, so reusing one instance across two different
          rows would show the first row's values on the second. */}
      {editing && (
        <CostForm
          key={editing.id}
          open
          onOpenChange={() => setEditing(null)}
          dealId={dealId}
          categories={active}
          customFields={customFields}
          initial={costToFormValues(editing)}
          costId={editing.id}
          title="Modifica costo"
          onSaved={() => setEditing(null)}
        />
      )}
    </div>
  )
}
