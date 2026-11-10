import { StatusPill } from '@/components/StatusPill'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { money } from '@/features/dashboard/format'
import { formatIsoDateItalian } from '@/lib/dates'
import { ITEM_STATE_TONE, NOT_IN_BALANCE, taxName } from './labels'
import type { SoloTaxRow } from './queries'

/** The solo's own taxes (decision 4): a table under the weeks, each tax with its date, its state
 *  and its amount, and the words that say it is not taken out of any balance. */
export function SoloTaxes({ taxes }: { taxes: SoloTaxRow[] }) {
  return (
    <section aria-label="Le tue tasse" className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">Le tue tasse</h2>
        <p className="text-xs text-muted-foreground">{NOT_IN_BALANCE}</p>
      </div>
      <Table scrollLabel="Le tue tasse">
        <TableHeader>
          <TableRow>
            <TableHead>Imposta</TableHead>
            <TableHead>Scadenza</TableHead>
            <TableHead>Stato</TableHead>
            <TableHead className="text-right">Importo</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {taxes.map((row) => (
            <TableRow key={row.due_item_id}>
              <TableCell>
                <span className="block">{taxName(row.tax) ?? row.description ?? 'Imposta'}</span>
                {row.tax && row.description && (
                  <span className="block text-xs text-muted-foreground">{row.description}</span>
                )}
              </TableCell>
              <TableCell className="tabular-nums">
                {row.due_date === null ? 'senza scadenza' : formatIsoDateItalian(row.due_date)}
              </TableCell>
              <TableCell>
                <StatusPill tone={ITEM_STATE_TONE[row.state]}>
                  {row.state === 'forecast' ? 'Previsto' : 'Da pagare'}
                </StatusPill>
              </TableCell>
              <TableCell className="text-right tabular-nums">{money(row.amount)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}
