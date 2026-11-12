import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { emptyRow, yearsOf, type ContractRow } from './seasonalContracts'

interface Props {
  rows: ContractRow[]
  onChange: (rows: ContractRow[]) => void
  thisYear: number
  error: string | null
}

/**
 * «Contratti stagionali»: one row per contract of the worker with the company, with its dates and
 * the days of effective work in each tax year it touches (REB-1090). The deduction of art. 11
 * c. 4-octies reads the history: from the second contract, within two years of the end of the
 * previous one, with 120 days in two tax periods. A year left empty is «not stated».
 */
export function SeasonalContractsField({ rows, onChange, thisYear, error }: Props) {
  function patch(index: number, change: Partial<ContractRow>) {
    onChange(rows.map((row, at) => (at === index ? { ...row, ...change } : row)))
  }

  return (
    <fieldset className="space-y-3 sm:col-span-2">
      <legend className="text-sm font-medium">Contratti stagionali</legend>
      <p className="text-xs text-muted-foreground">
        Dall’IRAP: il 70% del costo dal secondo contratto con l’azienda, entro due anni dalla fine del precedente e con almeno 120 giorni
        lavorati in due anni d’imposta. Servono le date e i giorni effettivamente lavorati, non quelli del contratto.
      </p>
      {rows.map((row, index) => (
        <div key={index} className="space-y-2 rounded-md border p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={`contract-${index}-start`}>{`Contratto ${index + 1}, dal`}</Label>
              <Input id={`contract-${index}-start`} type="date" value={row.start} onChange={(event) => patch(index, { start: event.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`contract-${index}-end`}>{`Contratto ${index + 1}, fino al`}</Label>
              <Input id={`contract-${index}-end`} type="date" value={row.end} onChange={(event) => patch(index, { end: event.target.value })} />
            </div>
          </div>
          {yearsOf(row, thisYear).length === 0 ? null : (
            <div className="grid gap-3 sm:grid-cols-3">
              {yearsOf(row, thisYear).map((year) => (
                <div key={year} className="space-y-1.5">
                  <Label htmlFor={`contract-${index}-days-${year}`}>{`Giorni lavorati nel ${year}`}</Label>
                  <Input
                    id={`contract-${index}-days-${year}`}
                    inputMode="numeric"
                    value={row.days[String(year)] ?? ''}
                    onChange={(event) => patch(index, { days: { ...row.days, [String(year)]: event.target.value } })}
                  />
                </div>
              ))}
            </div>
          )}
          <Button type="button" variant="ghost" size="sm" onClick={() => onChange(rows.filter((_, at) => at !== index))}>
            {`Togli il contratto ${index + 1}`}
          </Button>
        </div>
      ))}
      {error === null ? null : <p className="text-xs text-destructive">{error}</p>}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...rows, emptyRow()])}>
        Aggiungi un contratto
      </Button>
    </fieldset>
  )
}
