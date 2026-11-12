import { useState } from 'react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { LoadingState } from '@/components/LoadingState'
import { formatPercent } from '@/features/analytics/format'
import { money } from '@/features/dashboard/format'
import { parseAmount } from './amounts'
import { TAX_LABELS, type FiscalEstimate, type LossOffset, type TaxLoss } from './labels'
import { failureText, useDeleteTaxLoss, useSetTaxLoss } from './queries'

type LossTax = 'ires' | 'irpef'

/** «2025», read as a whole year; anything else is not a year. */
function parseYear(text: string): number | null {
  return /^\d{4}$/.test(text.trim()) ? Number.parseInt(text.trim(), 10) : null
}

/**
 * «Digita una perdita»: the loss a tax year closed with, as the return states it, which the
 * estimate carries to the years after it. The part already used and the two facts of the first
 * periods of a new activity are optional; the server has the last word and its sentence is shown
 * as it is.
 */
export function TaxLossDialog({
  legalEntityId,
  tax,
  suggestedYear,
  current,
  onClose,
}: {
  legalEntityId: string
  tax: LossTax
  suggestedYear: number
  current: TaxLoss | undefined
  onClose: () => void
}) {
  const used = current !== undefined && current.remaining_as_of_year > current.year
  const [year, setYear] = useState(String(current?.year ?? suggestedYear))
  const [amount, setAmount] = useState(current?.amount ?? '')
  const [remaining, setRemaining] = useState(used ? (current?.remaining ?? '') : '')
  const [asOf, setAsOf] = useState(used ? String(current?.remaining_as_of_year) : '')
  const [newActivity, setNewActivity] = useState(current?.new_activity ?? false)
  const [start, setStart] = useState(current?.activity_start_year ? String(current.activity_start_year) : '')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const save = useSetTaxLoss(legalEntityId)
  function submit() {
    setProblem(null)
    const found: Record<string, string> = {}
    const originYear = parseYear(year)
    if (originYear === null) found.year = 'Scrivi l’anno con quattro cifre.'
    const total = parseAmount(amount, false)
    if ('error' in total) found.amount = total.error
    else if (total.value === null) found.amount = 'Scrivi l’importo della perdita.'
    const left = parseAmount(remaining, false)
    if ('error' in left) found.remaining = left.error
    const leftYear = asOf.trim() === '' ? null : parseYear(asOf)
    if (asOf.trim() !== '' && leftYear === null) found.asOf = 'Scrivi l’anno con quattro cifre.'
    if ('value' in left && (left.value === null) !== (asOf.trim() === '')) {
      found.remaining = 'Il residuo e l’anno a cui si riferisce vanno scritti insieme.'
    }
    const startYear = newActivity ? parseYear(start) : null
    if (newActivity && startYear === null) found.start = 'Scrivi l’anno di costituzione con quattro cifre.'
    setErrors(found)
    if (Object.keys(found).length > 0 || originYear === null || !('value' in total) || total.value === null) return
    save.mutate(
      {
        tax,
        year: originYear,
        amount: total.value,
        remaining: 'value' in left ? left.value : null,
        remaining_as_of_year: leftYear,
        new_activity: newActivity,
        activity_start_year: startYear,
      },
      { onSuccess: onClose, onError: (error) => setProblem(failureText(error)) },
    )
  }
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {current ? 'Correggi' : 'Digita'} una perdita: {TAX_LABELS[tax]}
          </DialogTitle>
          <DialogDescription>
            La perdita che un anno ha chiuso, come la dichiarazione la riporta. La stima la porta agli
            anni dopo nei limiti della legge. Salvare la stessa imposta e lo stesso anno la sostituisce.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="loss-year">Anno della perdita</Label>
            <Input
              id="loss-year"
              inputMode="numeric"
              value={year}
              onChange={(event) => setYear(event.target.value)}
              aria-invalid={errors.year ? true : undefined}
              className="tabular-nums"
              disabled={current !== undefined}
            />
            {errors.year ? <p className="text-sm text-destructive">{errors.year}</p> : null}
          </div>
          <div className="space-y-1">
            <Label htmlFor="loss-amount">Importo della perdita</Label>
            <Input
              id="loss-amount"
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              aria-invalid={errors.amount ? true : undefined}
              className="tabular-nums"
            />
            <p className={errors.amount ? 'text-sm text-destructive' : 'text-xs text-muted-foreground'}>
              {errors.amount ?? 'In euro, sempre positivo.'}
            </p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="loss-remaining">Residuo (se già usata in parte)</Label>
              <Input
                id="loss-remaining"
                inputMode="decimal"
                value={remaining}
                onChange={(event) => setRemaining(event.target.value)}
                aria-invalid={errors.remaining ? true : undefined}
                className="tabular-nums"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="loss-as-of">Residuo alla fine del</Label>
              <Input
                id="loss-as-of"
                inputMode="numeric"
                value={asOf}
                onChange={(event) => setAsOf(event.target.value)}
                aria-invalid={errors.asOf ? true : undefined}
                className="tabular-nums"
              />
            </div>
          </div>
          {errors.remaining || errors.asOf ? (
            <p className="text-sm text-destructive">{errors.remaining ?? errors.asOf}</p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Lascia vuoti i due campi se la perdita non è stata ancora usata: la stima la porta avanti
              anno per anno.
            </p>
          )}
          <div className="space-y-2">
            <div className="flex items-start gap-2">
              <Checkbox
                id="loss-new-activity"
                checked={newActivity}
                onCheckedChange={(checked) => setNewActivity(checked === true)}
                className="mt-0.5"
              />
              <Label htmlFor="loss-new-activity" className="font-normal">
                Perdita dei primi tre periodi d’imposta di una nuova attività produttiva
              </Label>
            </div>
            {newActivity ? (
              <div className="space-y-1">
                <Label htmlFor="loss-start">Anno di costituzione o di inizio dell’attività</Label>
                <Input
                  id="loss-start"
                  inputMode="numeric"
                  value={start}
                  onChange={(event) => setStart(event.target.value)}
                  aria-invalid={errors.start ? true : undefined}
                  className="tabular-nums"
                />
                {errors.start ? <p className="text-sm text-destructive">{errors.start}</p> : null}
              </div>
            ) : null}
          </div>
        </div>
        {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
        {save.isPending ? <LoadingState label="Salvo la perdita…" /> : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={save.isPending}>
            Salva la perdita
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** What the losses of earlier years did in the estimated year: one row per year of origin, every
 *  figure the server's, with the limit it applied. */
function Offset({ offset }: { offset: LossOffset }) {
  if (offset.origins.length === 0) return null
  return (
    <ul className="space-y-1 text-xs text-muted-foreground">
      {offset.origins.map((origin) => (
        <li key={origin.origin_year}>
          Perdita del {origin.origin_year} ({origin.estimated ? 'stimata' : 'digitata'}): disponibile{' '}
          {money(origin.available)}, usata {money(origin.used)} (limite {formatPercent(origin.limit_percent)}),
          resta {money(origin.remaining)}.
        </li>
      ))}
    </ul>
  )
}

/**
 * The losses of a tax in «Tasse», beside the declared figures: the ones typed for the legal entity
 * with «Correggi» and «Togli», «Digita una perdita», and what the year's estimate did with them.
 * Drawn for an srl's IRES and an ordinary person's IRPEF only, the two taxes that carry a loss.
 */
export function TaxLossSection({
  estimate,
  tax,
  year,
}: {
  estimate: FiscalEstimate
  tax: LossTax
  year: number
}) {
  const [dialog, setDialog] = useState<{ current: TaxLoss | undefined } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const remove = useDeleteTaxLoss(estimate.legal_entity_id)
  const offset = tax === 'ires' ? estimate.company?.ires.losses : estimate.person?.income.losses
  const losses = (estimate.losses ?? []).filter((loss) => loss.tax === tax)
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Perdite degli anni precedenti</h3>
      {losses.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Nessuna perdita digitata.{' '}
          {tax === 'ires'
            ? 'Una perdita stimata di un anno già concluso si riporta da sola, e lo dice.'
            : 'Si riporta una perdita d’impresa, non una di lavoro autonomo.'}
        </p>
      ) : (
        <ul className="space-y-2">
          {losses.map((loss) => (
            <li key={loss.year} className="flex items-baseline justify-between gap-3 text-sm">
              <span>
                {loss.year}: {money(loss.amount)}
                {loss.remaining_as_of_year > loss.year
                  ? `, residuo ${money(loss.remaining)} alla fine del ${loss.remaining_as_of_year}`
                  : ''}
              </span>
              <span className="flex shrink-0 gap-2">
                <Button variant="outline" size="sm" onClick={() => setDialog({ current: loss })}>
                  Correggi
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={remove.isPending}
                  aria-label={`Togli la perdita del ${loss.year}`}
                  onClick={() => {
                    setProblem(null)
                    remove.mutate({ tax, year: loss.year }, { onError: (error) => setProblem(failureText(error)) })
                  }}
                >
                  Togli
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {offset ? <Offset offset={offset} /> : null}
      {offset?.missing.map((sentence) => (
        <p key={sentence} className="text-sm">
          {sentence}
        </p>
      ))}
      {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => setDialog({ current: undefined })}>
          Digita una perdita
        </Button>
      </div>
      {dialog ? (
        <TaxLossDialog
          legalEntityId={estimate.legal_entity_id}
          tax={tax}
          suggestedYear={year - 1}
          current={dialog.current}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </section>
  )
}
