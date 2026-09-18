import { Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { DataTable } from '@/components/DataTable'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { CostsPanel } from '@/features/costs/CostsPanel'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'
import { TimeEntryForm } from './TimeEntryForm'
import { TimeReportButtons } from './TimeReportButtons'
import { buildTimeEntryColumns, formatHoursValue, formatMoneyValue } from './columns'
import { timeEntryToFormValues } from './formValues'
import { useDealTimeSummary, useTimeEntries, type TimeEntry } from './queries'

function Figure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

export function TimeEntriesTab({ dealId }: { dealId: string }) {
  const entries = useTimeEntries({ deal_id: dealId })
  const summary = useDealTimeSummary(dealId)
  const schema = useEntitySchema('time_entry')
  const canWrite = useCanWrite()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<TimeEntry | null>(null)

  // Three deliberately different shapes, never one table with different text in a cell:
  // a failed request is "we do not actually know", which is a different claim from
  // "there is nothing here" and not this component's to make on the caller's behalf.
  if (entries.isError || summary.isError) {
    return <QueryErrorBanner error={entries.error ?? summary.error} />
  }

  const customFields = schema.data?.custom_fields ?? []

  return (
    <div className="space-y-6">
      {summary.data && (
        <Card>
          <CardContent className="flex flex-wrap items-start justify-between gap-6 pt-6">
            {/* Every figure below is the API's own, read straight off the summary
                document: §6 puts the summing in `packages/core`, and re-adding the
                rows on screen is how two surfaces start disagreeing about the same
                deal. */}
            <Figure label="Ore consuntivate" value={formatHoursValue(summary.data.ore_totali)} />
            <Figure
              label="Da fatturare"
              value={formatHoursValue(summary.data.ore_fatturabili_non_fatturate)}
            />
            <Figure
              label="Valore maturato (stima)"
              value={formatMoneyValue(summary.data.valore_ore_non_fatturate)}
              // Named as a stima and never as a margin: it is the estimate of §3
              // decision 2, and it stops being consulted the moment an invoice exists.
              hint="Non è un ricavo: il ricavo è la fattura"
            />
            <div className="flex flex-col items-start gap-2">
              <p className="text-sm text-muted-foreground">Stato</p>
              <Badge variant={summary.data.stato === 'chiuso' ? 'default' : 'secondary'}>
                {summary.data.stato}
              </Badge>
              {summary.data.ore_senza_tariffa > 0 && (
                <p className="text-xs text-muted-foreground">
                  {summary.data.ore_senza_tariffa}{' '}
                  {summary.data.ore_senza_tariffa === 1 ? 'voce' : 'voci'} senza tariffa,
                  esclusa dal valore maturato
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <TimeReportButtons dealId={dealId} />
        {canWrite && (
          <Button onClick={() => setCreating(true)}>
            <Plus className="mr-2 size-4" />
            Registra ore
          </Button>
        )}
      </div>

      <DataTable
        columns={buildTimeEntryColumns(customFields)}
        data={entries.data?.items ?? []}
        isLoading={entries.isLoading}
        emptyMessage="Nessuna voce di ore su questo deal."
        onRowClick={
          canWrite
            ? (row) => {
                if (row.invoice_line_id !== null) {
                  // Said before the dialog opens as well as inside it: the row is still
                  // worth opening (notes and custom fields remain editable), and being
                  // told why five controls are missing beats discovering it.
                  toast.info(
                    'La voce è su una fattura: ore, data, tariffa e descrizione non sono più modificabili.',
                  )
                }
                setEditing(row)
              }
            : undefined
        }
      />

      {/* Costs sit on the Ore tab in 4A rather than on an «Economia» tab because that
          tab does not exist until 4B, and a cost with nowhere to be entered is a cost
          nobody records. */}
      <CostsPanel dealId={dealId} />

      <TimeEntryForm
        open={creating}
        onOpenChange={setCreating}
        dealId={dealId}
        customFields={customFields}
        title="Registra ore"
      />

      {/* Mounted only while a row is selected, and keyed on that row: the dialog seeds
          its state once, when `open` flips, so reusing one instance across two different
          rows would show the first row's values on the second. */}
      {editing && (
        <TimeEntryForm
          key={editing.id}
          open
          onOpenChange={() => setEditing(null)}
          dealId={dealId}
          customFields={customFields}
          initial={timeEntryToFormValues(editing)}
          entryId={editing.id}
          locked={editing.invoice_line_id !== null}
          title="Modifica voce"
          onSaved={() => setEditing(null)}
        />
      )}
    </div>
  )
}
