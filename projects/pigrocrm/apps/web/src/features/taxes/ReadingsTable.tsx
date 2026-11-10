import { cn } from '@rebase/ui/cn'
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import {
  LEGEND,
  STAGES,
  STAGE_LABELS,
  TAX_LABELS,
  type Stage,
  type Tax,
  type TaxReadingLine,
  type TaxReadings,
} from './labels'
import { leftOutNotes, readingCell, totalCell } from './readings'

/** The reading that counts is in bold, in the column of its stage. */
function figureClass(line: TaxReadingLine, stage: Stage): string {
  return cn(
    'whitespace-nowrap text-right tabular-nums',
    line.counts === stage && 'font-semibold',
    line[stage] == null && 'text-muted-foreground',
  )
}

/**
 * One row per tax of the pack and year: «Imposta», «Stima», «Dichiarato», «Pagato», the reading
 * that counts in bold and a «Totale» row whose figures are the server's. From `md` a table; on a
 * phone one card per tax with its three readings as lines (the brief: no horizontal table).
 * A row opens the tax's panel; nothing here adds or compares an amount.
 */
export function ReadingsTable({
  readings,
  selected,
  wide,
  onSelect,
}: {
  readings: TaxReadings
  selected: Tax | null
  wide: boolean
  onSelect: (tax: Tax) => void
}) {
  const notes = leftOutNotes(readings)
  return (
    <div className="space-y-3">
      {wide ? (
        <div className="overflow-x-auto overflow-y-hidden border bg-card">
          <Table scrollLabel="Imposte dell’anno">
            <TableHeader>
              <TableRow>
                <TableHead>Imposta</TableHead>
                {STAGES.map((stage) => (
                  <TableHead key={stage} className="text-right">
                    {STAGE_LABELS[stage]}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {readings.lines.map((line) => (
                <TableRow
                  key={line.tax}
                  data-state={line.tax === selected ? 'selected' : undefined}
                  aria-selected={line.tax === selected}
                  tabIndex={0}
                  className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                  onClick={() => onSelect(line.tax)}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') return
                    event.preventDefault()
                    onSelect(line.tax)
                  }}
                >
                  <TableCell className="font-medium">{TAX_LABELS[line.tax]}</TableCell>
                  {STAGES.map((stage) => (
                    <TableCell key={stage} className={figureClass(line, stage)}>
                      {readingCell(line, stage)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
            <TableFooter>
              <TableRow>
                <TableCell className="font-semibold">Totale</TableCell>
                {STAGES.map((stage) => (
                  <TableCell
                    key={stage}
                    className={cn(
                      'whitespace-nowrap text-right tabular-nums',
                      readings.totals[stage] == null && 'text-muted-foreground',
                    )}
                  >
                    {totalCell(readings, stage)}
                  </TableCell>
                ))}
              </TableRow>
            </TableFooter>
          </Table>
        </div>
      ) : (
        <ul className="space-y-3" aria-label="Imposte dell’anno">
          {readings.lines.map((line) => (
            <li key={line.tax}>
              <button
                type="button"
                onClick={() => onSelect(line.tax)}
                className="block w-full space-y-2 border bg-card p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <TaxReadingLines line={line} />
              </button>
            </li>
          ))}
          <li className="space-y-2 border bg-card p-4">
            <p className="font-semibold">Totale</p>
            {STAGES.map((stage) => (
              <div key={stage} className="flex items-baseline justify-between gap-4 text-sm">
                <span>{STAGE_LABELS[stage]}</span>
                <span
                  className={cn(
                    'tabular-nums',
                    readings.totals[stage] == null && 'text-muted-foreground',
                  )}
                >
                  {totalCell(readings, stage)}
                </span>
              </div>
            ))}
          </li>
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{LEGEND}</p>
      {notes.map((note) => (
        <p key={note} className="text-xs text-muted-foreground">
          {note}
        </p>
      ))}
    </div>
  )
}

/** A tax's name over its three readings as lines, the one that counts in bold: the phone's
 *  card of a tax, and the light page's «Le tue tasse». */
export function TaxReadingLines({ line }: { line: TaxReadingLine }) {
  return (
    <>
      <p className="font-medium">{TAX_LABELS[line.tax]}</p>
      {STAGES.map((stage) => (
        <div key={stage} className="flex items-baseline justify-between gap-4 text-sm">
          <span className={cn(line.counts === stage && 'font-semibold')}>
            {STAGE_LABELS[stage]}
          </span>
          <span
            className={cn(
              'tabular-nums',
              line.counts === stage && 'font-semibold',
              line[stage] == null && 'text-muted-foreground',
            )}
          >
            {readingCell(line, stage)}
          </span>
        </div>
      ))}
    </>
  )
}
