import { BadgeEuro, Play } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { RowActions } from '@/components/RowActions'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { toProblem } from '@/lib/api'
import { HOURS_SCALE, sumDecimalStrings } from '@/lib/decimal'
import { useEntitySchema } from '@/lib/schema'
import { cn } from '@rebase/ui/cn'
import { formatHoursValue } from './columns'
import { timeEntryToFormValues } from './formValues'
import { groupByDay } from './register'
import { useDeleteTimeEntry, useRunningTimer, useStartTimer, type TimeEntry } from './queries'
import { TimeEntryForm } from './TimeEntryForm'
import type { WeekDay } from './week'

/** The register's own default, so a row that was never described still reads as one. */
const UNDESCRIBED = 'Senza descrizione'


/**
 * The week as a register: one line per entry, grouped by day with the day's total, the
 * week's total on top. Every line can be continued (a new timer with the same deal and
 * description -- Toggl's most-used button), edited in the same dialog the deal's Ore
 * tab uses, or removed. What the grid says with thirty-five cells, this says with words:
 * *what* was done, not only how much.
 */
export function TimeRegister({
  entries,
  days,
  dealNames,
  canWrite,
}: {
  entries: TimeEntry[]
  days: WeekDay[]
  dealNames: Map<string, string>
  canWrite: boolean
}) {
  const groups = useMemo(() => groupByDay(entries, days), [entries, days])
  const schema = useEntitySchema('time_entry')
  const running = useRunningTimer({ enabled: canWrite })
  const start = useStartTimer()
  const remove = useDeleteTimeEntry()
  const [editing, setEditing] = useState<TimeEntry | null>(null)

  const failWith = (error: unknown) => toast.error(toProblem(error).detail)
  const total = sumDecimalStrings(
    entries.map((entry) => entry.ore),
    HOURS_SCALE,
  )
  const billable = sumDecimalStrings(
    entries.filter((entry) => entry.fatturabile).map((entry) => entry.ore),
    HOURS_SCALE,
  )

  function continueEntry(entry: TimeEntry) {
    start.mutate(
      { deal_id: entry.deal_id, descrizione: entry.descrizione, fatturabile: entry.fatturabile },
      { onError: failWith },
    )
  }

  function deleteEntry(entry: TimeEntry) {
    const confirmed = window.confirm(
      'Eliminare questa voce di ore? Sparisce dagli elenchi e dai rapporti, ma il dato resta e l’operazione è reversibile.',
    )
    if (!confirmed) return
    remove.mutate(
      { entryId: entry.id, dealId: entry.deal_id },
      { onSuccess: () => toast.success('Voce eliminata'), onError: failWith },
    )
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground" data-testid="week-total">
        Settimana: <strong className="font-medium text-foreground">{formatHoursValue(total)} h</strong>
        {' · '}fatturabili {formatHoursValue(billable)} h
      </p>

      {groups.length === 0 ? (
        <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
          Nessuna ora registrata in questa settimana. Avvia il timer o aggiungi le ore a mano.
        </p>
      ) : (
        groups.map((group) => (
          <section key={group.iso} aria-label={group.label} className="space-y-2">
            <header className="flex items-baseline justify-between px-1">
              <h3 className="text-sm font-medium first-letter:uppercase">{group.label}</h3>
              <span className="text-sm tabular-nums text-muted-foreground">
                {formatHoursValue(group.total)} h
              </span>
            </header>
            <ul className="divide-y rounded-lg border bg-card">
              {group.entries.map((entry) => {
                const billed = entry.invoice_line_id !== null
                return (
                  <li key={entry.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                    <div className="min-w-0 flex-1">
                      <p className={cn('truncate', entry.descrizione === '' && 'italic text-muted-foreground')}>
                        {entry.descrizione === '' ? UNDESCRIBED : entry.descrizione}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {dealNames.get(entry.deal_id) ?? 'Deal'}
                        {billed ? ' · in fattura' : ''}
                      </p>
                    </div>
                    {entry.fatturabile ? (
                      <BadgeEuro
                        className="size-4 shrink-0 text-muted-foreground"
                        aria-label="Fatturabile"
                      />
                    ) : (
                      <Badge variant="pill" className="shrink-0 text-muted-foreground">
                        non fatturabile
                      </Badge>
                    )}
                    <span className="w-16 shrink-0 text-right font-medium tabular-nums">
                      {formatHoursValue(entry.ore)}
                    </span>
                    {canWrite && (
                      <>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={`Continua: ${entry.descrizione || UNDESCRIBED}`}
                          title="Continua con un nuovo timer"
                          disabled={Boolean(running.data) || start.isPending}
                          onClick={() => continueEntry(entry)}
                        >
                          <Play className="size-4" />
                        </Button>
                        <RowActions
                          label={`Azioni: ${entry.descrizione || UNDESCRIBED}`}
                          items={[
                            { label: 'Modifica', onSelect: () => setEditing(entry) },
                            {
                              label: 'Elimina',
                              destructive: true,
                              // Frozen on the server once billed; a disabled item says so
                              // where a hidden one would leave the person hunting.
                              disabled: billed || remove.isPending,
                              onSelect: () => deleteEntry(entry),
                            },
                          ]}
                        />
                      </>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        ))
      )}

      {editing && (
        <TimeEntryForm
          key={editing.id}
          open
          onOpenChange={() => setEditing(null)}
          dealId={editing.deal_id}
          customFields={schema.data?.custom_fields ?? []}
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
