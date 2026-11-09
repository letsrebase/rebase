import { Link } from '@tanstack/react-router'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { useModules } from '@/lib/modules'
import { formatMoney } from './columns'
import type { ValuePreview } from './queries'
import { emptyLine, type LineDraft, type LinePeriod, type LineUnit } from './valueLines'

const UNIT_LABEL: Record<LineUnit, string> = { flat: 'A corpo', day: 'Giorni', hour: 'Ore' }
const PERIOD_LABEL: Record<LinePeriod, string> = {
  one_off: 'Una tantum',
  monthly: 'Mensile',
  annual: 'Annuale',
}

interface Props {
  lines: LineDraft[]
  onChange: (lines: LineDraft[]) => void
  preview: ValuePreview | undefined
  problem: { key: string | null; message: string } | null
}

export function DealValueLines({ lines, onChange, preview, problem }: Props) {
  function patch(key: string, change: Partial<LineDraft>) {
    onChange(
      lines.map((line) => {
        if (line.key !== key) return line
        const next = { ...line, ...change }
        // A lump sum is its price (quantity 1) and, paid once, has no window.
        if (next.unit === 'flat') next.quantity = '1'
        if (next.unit === 'flat' && next.delivery_period === 'one_off') next.duration_months = ''
        return next
      }),
    )
  }

  return (
    <section className="space-y-3" aria-labelledby="deal-value-lines">
      <div className="flex items-center justify-between">
        <h3 id="deal-value-lines" className="text-sm font-medium">
          Valore
        </h3>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange([...lines, emptyLine()])}
        >
          <Plus className="mr-2 size-4" />
          Aggiungi riga
        </Button>
      </div>

      {lines.length === 0 && (
        <p className="text-sm text-muted-foreground">Nessuna riga: il deal resta senza valore.</p>
      )}

      {lines.map((line, index) => {
        const n = index + 1
        const flat = line.unit === 'flat'
        const showDuration = !(flat && line.delivery_period === 'one_off')
        const error = problem?.key === line.key ? problem.message : null
        return (
          <div
            key={line.key}
            className="space-y-3 rounded-md border p-3"
            role="group"
            aria-label={`Riga ${n}`}
          >
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-1">
                <Label htmlFor={`${line.key}-description`}>Descrizione<span className="sr-only"> riga {n}</span></Label>
                <Input
                  id={`${line.key}-description`}
                  value={line.description}
                  maxLength={255}
                  onChange={(event) => patch(line.key, { description: event.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor={`${line.key}-unit`}>Unità<span className="sr-only"> riga {n}</span></Label>
                <Select
                  value={line.unit}
                  onValueChange={(value) => patch(line.key, { unit: value as LineUnit })}
                >
                  <SelectTrigger
                    id={`${line.key}-unit`}
                    className="w-full"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.keys(UNIT_LABEL) as LineUnit[]).map((unit) => (
                      <SelectItem key={unit} value={unit}>
                        {UNIT_LABEL[unit]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor={`${line.key}-period`}>Periodo<span className="sr-only"> riga {n}</span></Label>
                <Select
                  value={line.delivery_period}
                  onValueChange={(value) =>
                    patch(line.key, { delivery_period: value as LinePeriod })
                  }
                >
                  <SelectTrigger
                    id={`${line.key}-period`}
                    className="w-full"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.keys(PERIOD_LABEL) as LinePeriod[]).map((period) => (
                      <SelectItem key={period} value={period}>
                        {PERIOD_LABEL[period]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor={`${line.key}-price`}>
                  {flat ? 'Importo (€)' : `Prezzo per ${line.unit === 'day' ? 'giorno' : 'ora'} (€)`}
                  <span className="sr-only"> riga {n}</span>
                </Label>
                <Input
                  id={`${line.key}-price`}
                  type="number"
                  min="0"
                  step="0.01"
                  value={line.unit_price}
                  aria-invalid={error !== null}
                  aria-describedby={error === null ? undefined : `${line.key}-error`}
                  onChange={(event) => patch(line.key, { unit_price: event.target.value })}
                />
              </div>
              {!flat && (
                <div className="space-y-1">
                  <Label htmlFor={`${line.key}-quantity`}>Quantità<span className="sr-only"> riga {n}</span></Label>
                  <Input
                    id={`${line.key}-quantity`}
                    type="number"
                    min="0"
                    step="0.01"
                    value={line.quantity}
                    onChange={(event) => patch(line.key, { quantity: event.target.value })}
                  />
                </div>
              )}
              <div className="space-y-1">
                <Label htmlFor={`${line.key}-start`}>Inizio<span className="sr-only"> riga {n}</span></Label>
                <Input
                  id={`${line.key}-start`}
                  type="date"
                  value={line.start}
                  onChange={(event) => patch(line.key, { start: event.target.value })}
                />
              </div>
              {showDuration && (
                <div className="space-y-1">
                  <Label htmlFor={`${line.key}-duration`}>Durata (mesi)<span className="sr-only"> riga {n}</span></Label>
                  <Input
                    id={`${line.key}-duration`}
                    type="number"
                    min="1"
                    step="1"
                    placeholder="Aperta"
                    value={line.duration_months}
                    onChange={(event) => patch(line.key, { duration_months: event.target.value })}
                  />
                </div>
              )}
            </div>

            <div className="flex items-start justify-between gap-3">
              <p
                id={`${line.key}-error`}
                className="text-sm text-destructive"
                role={error ? 'alert' : undefined}
              >
                {error}
              </p>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={`Rimuovi riga ${n}`}
                onClick={() => onChange(lines.filter((other) => other.key !== line.key))}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>
          </div>
        )
      })}

      {problem !== null && problem.key === null && (
        <p className="text-sm text-destructive" role="alert">
          {problem.message}
        </p>
      )}

      <p className="text-xs text-muted-foreground">
        Il valore è lordo: quello che paga il cliente, IVA esclusa. Un canone senza durata conta
        12 mesi.
      </p>

      <dl
        className="grid grid-cols-3 gap-3 rounded-md bg-muted p-3 text-sm"
        aria-label="Valore del deal"
      >
        <div>
          <dt className="text-muted-foreground">Valore previsto</dt>
          <dd className="font-medium" data-testid="preview-total">
            {formatMoney(preview?.expected_value ?? null)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Una tantum</dt>
          <dd className="font-medium" data-testid="preview-one-off">
            {formatMoney(preview?.one_off_value ?? null)}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Ricorrente al mese</dt>
          <dd className="font-medium" data-testid="preview-monthly">
            {preview?.monthly_recurring_value == null
              ? '—'
              : `${formatMoney(preview.monthly_recurring_value)}/mese`}
          </dd>
        </div>
      </dl>
    </section>
  )
}

/**
 * The lines of a deal that became a contract: read-only, because from the conversion on the
 * contract's rate cards are the price (`DealService.update` refuses a write to the lines). The
 * deal form shows what was booked and where the price lives now, and sends no `value_lines`.
 */
export function LockedValueLines({
  lines,
  contractId,
}: {
  lines: LineDraft[]
  contractId: string
}) {
  // The contract's page is `contracts`': the sentence stays, the link goes with the module.
  const contractsOn = useModules().on('contracts')
  return (
    <section className="space-y-3" aria-labelledby="deal-value-lines">
      <h3 id="deal-value-lines" className="text-sm font-medium">
        Valore
      </h3>
      <p className="text-sm text-muted-foreground">
        Il prezzo ora è nel contratto, le righe non si modificano più.{' '}
        {contractsOn && (
          <Link
            to="/app/contracts/$contractId"
            params={{ contractId }}
            className="underline underline-offset-2"
          >
            Apri il contratto
          </Link>
        )}
      </p>
      <ul className="divide-y rounded-md border text-sm">
        {lines.map((line, index) => (
          <li key={line.key} className="flex justify-between gap-4 px-3 py-2">
            <span>
              {line.description || `Riga ${index + 1}`} · {UNIT_LABEL[line.unit]} ·{' '}
              {PERIOD_LABEL[line.delivery_period]}
              {line.unit === 'flat' ? '' : ` · ${line.quantity} previsti`}
            </span>
            <span className="text-right font-medium">{formatMoney(line.unit_price)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
